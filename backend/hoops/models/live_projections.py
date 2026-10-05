"""Live player projections for the worker (NBA, from V1.1).

Runs with every game prediction: the projection set is written next to the prediction,
locked with it 30 minutes before tip-off, and graded after the game.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import psycopg

from hoops.leagues import COUNTED_SEASON_TYPES, League
from hoops.models import data
from hoops.models.projections import (
    MODEL_NAME,
    PROJECTED,
    History,
    ProjectionSettings,
    Ranges,
    project,
    season_prior,
)

log = logging.getLogger(__name__)
MIN_SHOWN_MINUTES = 1.0       # players projected below this are left out of the set
APPEARANCE_PRIOR_GAMES = 2.0  # pseudo-games behind each player's chance of playing


@dataclass
class ProjectionChampion:
    model_version_id: int
    settings: ProjectionSettings
    ranges: Ranges


def load_projection_champion(conn: psycopg.Connection, league: League) -> ProjectionChampion:
    row = conn.execute(
        "SELECT model_version_id, settings FROM model_versions WHERE league = %s"
        " AND model_name = %s AND role = 'champion'", (str(league), MODEL_NAME)).fetchone()
    if row is None:
        # No projection exams yet: register the default settings as the champion.
        default = ProjectionSettings()
        (vid,) = conn.execute(
            "INSERT INTO model_versions (league, model_name, version, settings, role)"
            " VALUES (%s, %s, 'default', %s, 'champion')"
            " ON CONFLICT (league, model_name, version) DO UPDATE SET role = 'champion'"
            " RETURNING model_version_id",
            (str(league), MODEL_NAME, json.dumps({"settings": default.to_json()})),
        ).fetchone()
        return ProjectionChampion(vid, default, Ranges())
    vid, settings = row
    return ProjectionChampion(vid, ProjectionSettings(**settings.get("settings", {})),
                              Ranges.from_json(settings.get("ranges", {})))


class Projector:
    def __init__(self, league: League) -> None:
        self.league = league
        self._cache: dict[tuple, tuple] = {}

    def invalidate(self) -> None:
        """Called after new games are stored: histories are rebuilt on next use."""
        self._cache.clear()

    def _season(self, conn, season: int, half_life: float):
        """History of the season so far, plus appearance counts, cached until invalidated."""
        key = (season, half_life)
        if key in self._cache:
            return self._cache[key]
        positions = {p: pos for p, pos in conn.execute(
            "SELECT player_id, position FROM players WHERE league = %s", (str(self.league),))}
        prev_games = data.games_frame(conn, self.league, [season - 1])
        prev_players = data.player_games_frame(conn, self.league, [season - 1])
        prior = season_prior(prev_players, prev_games, positions)
        games = data.games_frame(conn, self.league, [season])
        players = data.player_games_frame(conn, self.league, [season])
        history = History(prior, positions, half_life)
        for _day, day_games in (games.groupby("game_date", sort=True) if len(games) else []):
            history.update(players[players["game_id"].isin(set(day_games["game_id"]))],
                           day_games)
        appearances = _appearances(games, players)
        prev_mpg = prior.mpg
        self._cache[key] = (history, appearances, prev_mpg)
        return self._cache[key]

    def project_game(self, conn: psycopg.Connection, game_id: int, *, season: int,
                     season_type: str, home: int, away: int, home_points: float,
                     away_points: float, home_b2b: bool, away_b2b: bool, out: set[int],
                     lock: bool) -> int | None:
        """Writes a projection set for one game. Unlocked sets are skipped when nothing
        changed since the last one. Returns the set ID written, if any."""
        if self.league is not League.NBA:
            return None
        champion = load_projection_champion(conn, self.league)
        history, appearances, prev_mpg = self._season(conn, season,
                                                      champion.settings.half_life_games)
        rows, chance = [], {}
        for team, pts, opp_pts, b2b in ((home, home_points, away_points, home_b2b),
                                        (away, away_points, home_points, away_b2b)):
            roster = [p for (p,) in conn.execute(
                "SELECT player_id FROM roster_entries WHERE season = %s AND team_id = %s",
                (season, team))]
            available = [p for p in roster if p not in out]
            for p in available:
                chance[p] = _play_chance(appearances.get((team, p)), prev_mpg.get(p))
            rows += history.inputs(game_id, team, available, team_points=pts,
                                   game_total=pts + opp_pts, margin=pts - opp_pts,
                                   back_to_back=b2b)
        if not rows:
            return None
        inputs = pd.DataFrame(rows)
        # Live, nobody knows yet who will play: each player's minutes are weighted by how
        # often he has played, then the team's minutes are shared out as usual.
        inputs["play_chance"] = inputs["player_id"].map(chance).fillna(0.5)
        projected = project(inputs, champion.settings)
        projected = projected[projected["minutes"] >= MIN_SHOWN_MINUTES]
        payload = {}
        for r in projected.to_dict("records"):
            stats = {}
            for s in PROJECTED:
                lo, hi = champion.ranges.bounds(s, np.array([r[s]]))
                stats[s] = [round(float(r[s]), 1), round(float(lo[0]), 1), round(float(hi[0]), 1)]
            payload[int(r["player_id"])] = (int(r["team_id"]), stats)
        digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if not lock:
            latest = conn.execute(
                "SELECT inputs_hash FROM projection_sets WHERE game_id = %s"
                " ORDER BY created_at DESC, projection_set_id DESC LIMIT 1",
                (game_id,)).fetchone()
            if latest and latest[0] == digest:
                return None
        with conn.transaction():
            (set_id,) = conn.execute(
                "INSERT INTO projection_sets (game_id, model_version_id, is_locked,"
                " is_rehearsal, inputs_hash) VALUES (%s, %s, %s, %s, %s)"
                " RETURNING projection_set_id",
                (game_id, champion.model_version_id, lock,
                 season_type not in COUNTED_SEASON_TYPES, digest)).fetchone()
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_projections (projection_set_id, player_id, team_id,"
                    " stats) VALUES (%s, %s, %s, %s)",
                    [(set_id, pid, team, json.dumps(stats))
                     for pid, (team, stats) in payload.items()])
        return set_id

    def grade(self, conn: psycopg.Connection, game_id: int) -> int:
        """Grades the game's locked projection sets against what players actually did."""
        sets = conn.execute("SELECT projection_set_id FROM projection_sets WHERE game_id = %s"
                            " AND is_locked", (game_id,)).fetchall()
        graded = 0
        for (set_id,) in sets:
            metrics = grade_set(conn, set_id)
            if metrics is None:
                continue
            conn.execute(
                "INSERT INTO projection_grades (projection_set_id, players, metrics)"
                " VALUES (%s, %s, %s) ON CONFLICT (projection_set_id) DO UPDATE SET"
                " players = EXCLUDED.players, metrics = EXCLUDED.metrics, graded_at = now()",
                (set_id, metrics.pop("players"), json.dumps(metrics)))
            graded += 1
        return graded


    def grade_missing(self, conn: psycopg.Connection) -> int:
        """Grades locked projection sets of finished games that have no grade yet."""
        rows = conn.execute(
            """
            SELECT DISTINCT ps.game_id FROM projection_sets ps
            JOIN games g USING (game_id)
            LEFT JOIN projection_grades pg USING (projection_set_id)
            WHERE g.league = %s AND g.status = 'final' AND ps.is_locked
              AND pg.projection_set_id IS NULL
            """, (str(self.league),)).fetchall()
        return sum(self.grade(conn, game_id) for (game_id,) in rows)


def grade_set(conn: psycopg.Connection, set_id: int) -> dict | None:
    rows = conn.execute(
        """
        SELECT pp.stats, s.minutes, s.pts, s.fgm, s.fga, s.fg3m, s.fg3a, s.ftm, s.fta, s.oreb,
               s.dreb, s.reb, s.ast, s.stl, s.blk, s.tov, s.pf
        FROM player_projections pp
        JOIN projection_sets ps USING (projection_set_id)
        JOIN player_game_stats s ON s.game_id = ps.game_id AND s.player_id = pp.player_id
        WHERE pp.projection_set_id = %s AND NOT s.did_not_play AND s.minutes > 0
        """, (set_id,)).fetchall()
    if not rows:
        return None
    names = ["minutes", "pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "reb",
             "ast", "stl", "blk", "tov", "pf"]
    errors: dict[str, list[float]] = {s: [] for s in names}
    inside: dict[str, list[bool]] = {s: [] for s in names}
    for stats, *actual in rows:
        for s, value in zip(names, actual, strict=True):
            if s not in stats or value is None:
                continue
            exp, lo, hi = stats[s]
            errors[s].append(abs(exp - value))
            inside[s].append(round(lo) <= value <= round(hi))
    return {"players": len(rows),
            "mae": {s: float(np.mean(v)) for s, v in errors.items() if v},
            "in_range": {s: float(np.mean(v)) for s, v in inside.items() if v}}


def _appearances(games: pd.DataFrame, players: pd.DataFrame) -> dict[tuple[int, int], tuple]:
    """(team, player) -> (games played, team games since his first game for the team)."""
    if games.empty or players.empty:
        return {}
    team_games = pd.concat([games[["game_id", "start_time", "home_team_id"]].rename(
        columns={"home_team_id": "team_id"}), games[["game_id", "start_time", "away_team_id"]]
        .rename(columns={"away_team_id": "team_id"})])
    first = players.groupby(["team_id", "player_id"]).agg(first=("start_time", "min"),
                                                          played=("game_id", "nunique"))
    out = {}
    for (team, player), row in first.iterrows():
        since = int(((team_games["team_id"] == team)
                     & (team_games["start_time"] >= row["first"])).sum())
        out[(int(team), int(player))] = (int(row["played"]), since)
    return out


def _play_chance(appearance: tuple | None, prev_mpg: float | None) -> float:
    """Chance a healthy player plays: his appearance rate this season, starting from a
    guess based on last season's minutes."""
    prior = 0.9 if (prev_mpg or 0) >= 20 else 0.7 if (prev_mpg or 0) >= 10 else 0.4
    played, since = appearance or (0, 0)
    return (played + APPEARANCE_PRIOR_GAMES * prior) / (since + APPEARANCE_PRIOR_GAMES)
