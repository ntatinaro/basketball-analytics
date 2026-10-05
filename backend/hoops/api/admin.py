"""The owner's admin panel (V1.1): one password-protected login, then read-only views of
data health, data quality and model management, plus NCAA absences entry.

Sessions are signed cookies (HMAC with HOOPS_SECRET_KEY): nothing is stored server-side.
Failed logins are limited for everyone together, since behind two proxies the API cannot
reliably tell callers apart, and there is only one account.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import date

import psycopg
from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from hoops.api.deps import get_conn, parse_league, resolve_season
from hoops.api.misc import _live_section
from hoops.leagues import League
from hoops.quality.report import THRESHOLD, issue_breakdown, season_quality
from hoops.settings import load_settings

router = APIRouter(prefix="/api/admin")
COOKIE = "hoops_admin"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 10                 # failed logins allowed per window, for everyone together
FAILURE_WINDOW = 15 * 60
_failures: deque[float] = deque()
_failures_lock = threading.Lock()
_fallback_key = secrets.token_hex(32)   # used only if HOOPS_SECRET_KEY is unset


def _key() -> bytes:
    return (load_settings().secret_key or _fallback_key).encode()


def _sign(expires: int) -> str:
    mac = hmac.new(_key(), f"admin.{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def _valid(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, _, _mac = token.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    return hmac.compare_digest(token, _sign(int(expires)))


def require_admin(hoops_admin: str | None = Cookie(default=None)) -> None:
    if load_settings().admin_password is None:
        raise HTTPException(404, "The admin panel is switched off.")
    if not _valid(hoops_admin):
        raise HTTPException(401, "Please log in.")


class Login(BaseModel):
    password: str = Field(max_length=200)


@router.post("/login")
def login(body: Login, response: Response):
    password = load_settings().admin_password
    if password is None:
        raise HTTPException(404, "The admin panel is switched off.")
    now = time.time()
    with _failures_lock:
        while _failures and _failures[0] < now - FAILURE_WINDOW:
            _failures.popleft()
        if len(_failures) >= MAX_FAILURES:
            raise HTTPException(429, "Too many failed logins. Try again in 15 minutes.")
        if not hmac.compare_digest(body.password.encode(), password.encode()):
            _failures.append(now)
            raise HTTPException(401, "Wrong password.")
    response.set_cookie(COOKIE, _sign(int(now) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                        httponly=True, secure=True, samesite="strict", path="/api/admin")
    return {"admin": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/api/admin")
    return {"admin": False}


@router.get("/me")
def me(hoops_admin: str | None = Cookie(default=None)):
    enabled = load_settings().admin_password is not None
    return {"enabled": enabled, "admin": enabled and _valid(hoops_admin)}


# -- data health ------------------------------------------------------------------------


@router.get("/health", dependencies=[Depends(require_admin)])
def health(conn: psycopg.Connection = Depends(get_conn)):
    jobs = conn.execute(
        """
        SELECT job_name, league,
               max(finished_at) FILTER (WHERE status = 'succeeded') AS last_success,
               max(finished_at) FILTER (WHERE status = 'failed') AS last_failure,
               count(*) FILTER (WHERE status = 'failed'
                                AND started_at > now() - interval '1 day') AS failures_24h,
               count(*) FILTER (WHERE started_at > now() - interval '1 day') AS runs_24h,
               count(*) FILTER (WHERE status = 'succeeded' AND details ? 'errors'
                                AND started_at > now() - interval '1 day') AS partial_24h
        FROM job_runs GROUP BY job_name, league ORDER BY job_name, league
        """).fetchall()
    recent = conn.execute(
        """
        SELECT job_run_id, job_name, league, started_at, finished_at, status, details,
               split_part(error, E'\\n', 1) AS error
        FROM job_runs
        WHERE status <> 'succeeded' OR details ? 'errors' OR started_at > now() - interval '2 hours'
        ORDER BY started_at DESC LIMIT 100
        """).fetchall()
    return {"jobs": jobs, "recent": recent}


# -- data quality ------------------------------------------------------------------------


@router.get("/quality", dependencies=[Depends(require_admin)])
def quality(league: str = "nba", conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    seasons = []
    for q in season_quality(conn, lg):
        seasons.append({
            "season": q.season, "games": q.games,
            "team": q.team_failures, "player": q.player_failures, "pbp": q.pbp_failures,
            "lineups": q.lineup_issues, "flagged": q.flagged_groups,
            "issues": [{"group": g, "check": c, "games": n}
                       for g, c, n in issue_breakdown(conn, lg, q.season)],
        })
    flagged = conn.execute(
        """
        SELECT g.game_id, g.season, g.start_time, ht.abbreviation AS home,
               at.abbreviation AS away, i.check_group, i.check_name, i.detail
        FROM data_quality_issues i JOIN games g USING (game_id)
        JOIN teams ht ON ht.team_id = g.home_team_id JOIN teams at ON at.team_id = g.away_team_id
        WHERE g.league = %s AND i.check_name <> 'lineups_inconsistent'
        ORDER BY g.start_time DESC LIMIT 200
        """, (str(lg),)).fetchall()
    return {"threshold": THRESHOLD, "seasons": seasons, "flagged_games": flagged}


# -- model management -----------------------------------------------------------------


@router.get("/models", dependencies=[Depends(require_admin)])
def models(league: str = "nba", season: int | None = None,
           conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    s = resolve_season(conn, lg, season)
    versions = conn.execute(
        """
        SELECT v.model_version_id, v.model_name, v.version, v.role, v.created_at, v.settings,
               (SELECT count(*) FROM exam_results e
                WHERE e.model_version_id = v.model_version_id) AS exams
        FROM model_versions v WHERE v.league = %s AND v.role <> 'candidate'
        ORDER BY v.model_name, v.role, v.created_at DESC
        """, (str(lg),)).fetchall()
    exams = conn.execute(
        """
        SELECT e.model_name, e.exam_round, e.tuning_seasons, e.exam_season, e.created_at,
               count(*) AS candidates,
               max(e.exam_metrics::text) FILTER (WHERE e.is_winner)::jsonb AS winner_exam,
               min(v.version) FILTER (WHERE e.is_winner) AS winner,
               jsonb_agg(jsonb_build_object('version', v.version,
                                            'tuning', e.tuning_metrics,
                                            'winner', e.is_winner)
                         ORDER BY e.is_winner DESC, v.version) AS table
        FROM exam_results e JOIN model_versions v USING (model_version_id)
        WHERE e.league = %s
        GROUP BY e.model_name, e.exam_round, e.tuning_seasons, e.exam_season, e.created_at
        ORDER BY e.model_name, e.exam_round
        """, (str(lg),)).fetchall()
    runs = conn.execute(
        """
        SELECT r.training_run_id, v.model_name, v.version, r.started_at, r.finished_at,
               r.status, r.metrics, r.error
        FROM training_runs r JOIN model_versions v USING (model_version_id)
        WHERE v.league = %s ORDER BY r.started_at DESC LIMIT 50
        """, (str(lg),)).fetchall()
    live = conn.execute(
        """
        SELECT p.model_version_id, p.is_shadow, g.game_id, g.start_time,
               ht.abbreviation AS home, at.abbreviation AS away, g.home_score, g.away_score,
               p.home_win_prob, gr.home_won, gr.log_loss, gr.market_home_prob
        FROM predictions p JOIN prediction_grades gr USING (prediction_id)
        JOIN games g USING (game_id)
        JOIN teams ht ON ht.team_id = g.home_team_id JOIN teams at ON at.team_id = g.away_team_id
        WHERE g.league = %s AND g.season = %s AND p.is_locked AND NOT p.is_rehearsal
        ORDER BY g.start_time DESC
        """, (str(lg), s)).fetchall()
    by_version: dict[int, list] = {}
    for r in live:
        by_version.setdefault(r["model_version_id"], []).append(r)
    accuracy = []
    for vid, rows in by_version.items():
        section = _live_section(rows)
        section.pop("predictions", None)
        accuracy.append({"model_version_id": vid, "shadow": rows[0]["is_shadow"], **section})
    return {"season": s, "versions": versions, "exams": exams, "training_runs": runs,
            "live_accuracy": accuracy}


# -- NCAA absences ----------------------------------------------------------------------


class AbsenceIn(BaseModel):
    league: League = League.NCAAM
    player_id: int
    status: str = Field(default="Out", pattern="^(Out|Doubtful|Questionable)$")
    starts_on: date
    ends_on: date | None = None
    note: str | None = Field(default=None, max_length=500)


@router.get("/absences", dependencies=[Depends(require_admin)])
def absences(league: str = "ncaam", conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    rows = conn.execute(
        """
        SELECT a.absence_id, a.player_id, p.display_name AS player, a.team_id,
               t.display_name AS team, a.status, a.starts_on, a.ends_on, a.note, a.created_at
        FROM manual_absences a JOIN players p USING (player_id) JOIN teams t USING (team_id)
        WHERE a.league = %s AND (a.ends_on IS NULL OR a.ends_on >= current_date - 30)
        ORDER BY a.starts_on DESC
        """, (str(lg),)).fetchall()
    return {"absences": rows}


@router.post("/absences", dependencies=[Depends(require_admin)])
def add_absence(body: AbsenceIn = Body(...), conn: psycopg.Connection = Depends(get_conn)):
    if body.ends_on and body.ends_on < body.starts_on:
        raise HTTPException(400, "The end date is before the start date.")
    team = conn.execute(
        """
        SELECT r.team_id FROM roster_entries r JOIN players p USING (player_id)
        WHERE r.player_id = %s AND p.league = %s ORDER BY r.season DESC LIMIT 1
        """, (body.player_id, str(body.league))).fetchone()
    if team is None:
        raise HTTPException(404, "No such player on any roster in this league.")
    (absence_id,) = conn.execute(
        """
        INSERT INTO manual_absences (league, player_id, team_id, status, starts_on, ends_on,
                                     note)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING absence_id
        """, (str(body.league), body.player_id, team["team_id"], body.status, body.starts_on,
              body.ends_on, body.note)).fetchone().values()
    return {"absence_id": absence_id}


@router.post("/absences/{absence_id}/end", dependencies=[Depends(require_admin)])
def end_absence(absence_id: int, ends_on: date = Body(embed=True),
                conn: psycopg.Connection = Depends(get_conn)):
    row = conn.execute(
        "UPDATE manual_absences SET ends_on = %s WHERE absence_id = %s"
        " AND starts_on <= %s RETURNING absence_id", (ends_on, absence_id, ends_on)).fetchone()
    if row is None:
        raise HTTPException(404, "No such absence, or the end date is before its start.")
    return {"absence_id": absence_id, "ends_on": ends_on}
