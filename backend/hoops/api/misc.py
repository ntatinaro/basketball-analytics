"""Meta, search, and the model report card (feature 8)."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import psycopg
from fastapi import APIRouter, Depends, Query

from hoops.api.deps import (
    cached,
    current_season,
    data_freshness,
    get_conn,
    parse_league,
    rehearsal,
    resolve_season,
    seasons,
    teams_for_season,
)
from hoops.evaluation import metrics
from hoops.evaluation.exams import target_check
from hoops.leagues import CAPABILITIES

router = APIRouter()

# Common nicknames, so searches like "steph" or "kat" find the right player.
PLAYER_ALIASES = {
    "steph": "Stephen Curry", "chef curry": "Stephen Curry", "kat": "Karl-Anthony Towns",
    "sga": "Shai Gilgeous-Alexander", "ad": "Anthony Davis", "bron": "LeBron James",
    "king james": "LeBron James", "joker": "Nikola Jokic", "greek freak": "Giannis Antetokounmpo",
    "jjj": "Jaren Jackson Jr.", "cp3": "Chris Paul", "pg13": "Paul George",
    "dame": "Damian Lillard", "wemby": "Victor Wembanyama", "the brow": "Anthony Davis",
    "spida": "Donovan Mitchell", "kd": "Kevin Durant", "zion": "Zion Williamson",
    "ant": "Anthony Edwards", "ant man": "Anthony Edwards", "the beard": "James Harden",
}

# Team nicknames, so "sixers" or "dubs" find the right team (ESPN abbreviations).
TEAM_ALIASES = {
    "sixers": "PHI", "76ers": "PHI", "philly": "PHI", "dubs": "GS", "cavs": "CLE",
    "mavs": "DAL", "wolves": "MIN", "t-wolves": "MIN", "twolves": "MIN", "blazers": "POR",
    "rip city": "POR", "nugs": "DEN", "celts": "BOS", "clips": "LAC", "grizz": "MEM",
    "pels": "NO", "nola": "NO", "raps": "TOR", "wiz": "WSH", "dc": "WSH", "okc": "OKC",
    "lal": "LAL", "la": "LAL", "bk": "BKN", "brooklyn": "BKN", "nyk": "NY", "gsw": "GS",
    "sas": "SA", "uta": "UTAH", "was": "WSH", "nop": "NO", "phx": "PHX", "suns": "PHX",
}


@router.get("/api/{league}/meta")
def meta(league: str, conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    season = current_season(conn, lg)
    teams = teams_for_season(conn, lg, season)
    conferences = sorted({t["conference"] for t in teams.values() if t["conference"]})
    return {
        "league": str(lg), "current_season": season, "seasons": seasons(conn, lg),
        "conferences": conferences, "capabilities": sorted(CAPABILITIES[lg]),
        "rehearsal": rehearsal(), **data_freshness(conn, lg),
    }


@router.get("/api/{league}/search/teams")
def search_teams(league: str, q: str = Query(min_length=1, max_length=60),
                 conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    rows = conn.execute(
        """
        SELECT team_id AS id, display_name AS name, abbreviation, logo_url AS logo,
               greatest(similarity(display_name, %(q)s), word_similarity(%(q)s, display_name),
                        CASE WHEN lower(abbreviation) IN (lower(%(q)s), lower(%(alias)s))
                             THEN 1 ELSE 0 END,
                        similarity(coalesce(location, ''), %(q)s),
                        similarity(coalesce(name, ''), %(q)s)) AS score
        FROM teams WHERE league = %(league)s
        ORDER BY score DESC, display_name LIMIT 8
        """,
        {"q": q, "alias": TEAM_ALIASES.get(q.strip().lower(), ""), "league": str(lg)},
    ).fetchall()
    alias = TEAM_ALIASES.get(q.strip().lower())
    if alias:                                   # a known nickname means exactly one team
        return {"results": [r for r in rows if r["abbreviation"] == alias]}
    return {"results": [r for r in rows if r["score"] >= 0.25]}


@router.get("/api/{league}/search/players")
def search_players(league: str, q: str = Query(min_length=1, max_length=60),
                   conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    term = PLAYER_ALIASES.get(q.strip().lower(), q)
    rows = conn.execute(
        """
        SELECT p.player_id AS id, p.display_name AS name, p.position, p.headshot_url AS headshot,
               greatest(similarity(p.display_name, %(q)s),
                        word_similarity(%(q)s, p.display_name)) AS score,
               (SELECT t.abbreviation FROM player_season_stats s JOIN teams t USING (team_id)
                WHERE s.player_id = p.player_id ORDER BY s.last_game DESC LIMIT 1) AS team,
               (SELECT max(s.last_game) FROM player_season_stats s
                WHERE s.player_id = p.player_id) AS last_game
        FROM players p
        WHERE p.league = %(league)s AND (p.display_name %% %(q)s OR %(q)s <%% p.display_name)
        ORDER BY score DESC, last_game DESC NULLS LAST LIMIT 10
        """,
        {"q": term, "league": str(lg)},
    ).fetchall()
    return {"results": rows}


def _summary(prob, won) -> dict:
    return metrics.summary(np.asarray(prob, dtype=float), np.asarray(won, dtype=float))


@router.get("/api/{league}/report-card")
def report_card(league: str, season: int | None = None,
                conn: psycopg.Connection = Depends(get_conn)):
    lg = parse_league(league)
    s = resolve_season(conn, lg, season)

    def build():
        live = conn.execute(
            """
            SELECT g.game_id, g.start_time, ht.abbreviation AS home, at.abbreviation AS away,
                   g.home_score, g.away_score, p.home_win_prob, gr.home_won, gr.log_loss,
                   gr.market_home_prob
            FROM predictions p
            JOIN prediction_grades gr USING (prediction_id)
            JOIN games g USING (game_id)
            JOIN teams ht ON ht.team_id = g.home_team_id
            JOIN teams at ON at.team_id = g.away_team_id
            WHERE g.league = %s AND g.season = %s AND p.is_locked AND NOT p.is_shadow
              AND NOT p.is_rehearsal
            ORDER BY g.start_time DESC
            """,
            (str(lg), s),
        ).fetchall()
        return {"season": s, "live": _live_section(live), "backtests": _backtests(conn, lg)}

    return cached(conn, lg, ("report", s), build)


def _live_section(rows: list[dict]) -> dict:
    if not rows:
        return {"games": 0, "predictions": []}
    prob = [r["home_win_prob"] for r in rows]
    won = [r["home_won"] for r in rows]
    by_month: dict[str, list] = defaultdict(list)
    for r in rows:
        by_month[r["start_time"].strftime("%Y-%m")].append(r)
    market_rows = [r for r in rows if r["market_home_prob"] is not None]
    out = {
        "games": len(rows),
        "model": _summary(prob, won),
        "calibration": metrics.calibration_bins(prob, won),
        "by_month": [{"month": m, **_summary([r["home_win_prob"] for r in rs],
                                             [r["home_won"] for r in rs])}
                     for m, rs in sorted(by_month.items())],
        "predictions": [{
            "game_id": r["game_id"], "date": r["start_time"], "home": r["home"],
            "away": r["away"], "score": f"{r['home_score']}-{r['away_score']}",
            "home_win_prob": r["home_win_prob"], "home_won": r["home_won"],
            "correct": (r["home_win_prob"] >= 0.5) == r["home_won"], "log_loss": r["log_loss"],
        } for r in rows],
    }
    if market_rows:
        model_m = _summary([r["home_win_prob"] for r in market_rows],
                           [r["home_won"] for r in market_rows])
        market_m = _summary([r["market_home_prob"] for r in market_rows],
                            [r["home_won"] for r in market_rows])
        out["market"] = {"model": model_m, "market": market_m,
                         "target": target_check(model_m, market_m)}
    return out


def _backtests(conn, lg) -> list[dict]:
    rows = conn.execute(
        """
        SELECT b.season, b.home_win_prob, b.market_home_prob, g.start_time,
               (g.home_score > g.away_score) AS home_won,
               min(g.start_time) OVER (PARTITION BY b.season) AS season_start
        FROM backtest_predictions b
        JOIN model_versions m USING (model_version_id)
        JOIN games g USING (game_id)
        WHERE m.role = 'champion' AND m.league = %s
        """,
        (str(lg),),
    ).fetchall()
    by_season: dict[int, list] = defaultdict(list)
    for r in rows:
        by_season[r["season"]].append(r)
    out = []
    for season, rs in sorted(by_season.items(), reverse=True):
        early = [r for r in rs if (r["start_time"] - r["season_start"]).days < 28]
        rest = [r for r in rs if (r["start_time"] - r["season_start"]).days >= 28]
        market = [r for r in rs if r["market_home_prob"] is not None]
        entry = {
            "season": season,
            "model": _summary([r["home_win_prob"] for r in rs], [r["home_won"] for r in rs]),
            "early_season": _summary([r["home_win_prob"] for r in early],
                                     [r["home_won"] for r in early]),
            "rest_of_season": _summary([r["home_win_prob"] for r in rest],
                                       [r["home_won"] for r in rest]),
            "calibration": metrics.calibration_bins([r["home_win_prob"] for r in rs],
                                                    [r["home_won"] for r in rs]),
        }
        if market:
            model_m = _summary([r["home_win_prob"] for r in market],
                               [r["home_won"] for r in market])
            market_m = _summary([r["market_home_prob"] for r in market],
                                [r["home_won"] for r in market])
            entry["market"] = {"model": model_m, "market": market_m,
                               "target": target_check(model_m, market_m),
                               "note": "Lines from past seasons may not be closing lines."}
        out.append(entry)
    return out
