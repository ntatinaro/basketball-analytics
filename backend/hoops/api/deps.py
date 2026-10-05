"""Shared API plumbing: database pool, response cache, and common lookups."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from hoops.leagues import COUNTED_SEASON_TYPES, League
from hoops.settings import load_settings

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def pool() -> ConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(load_settings().database_url, min_size=1, max_size=4,
                                   kwargs={"autocommit": True, "row_factory": dict_row},
                                   open=True)
    return _pool


def get_conn() -> Iterator[psycopg.Connection]:
    with pool().connection() as conn:
        yield conn


def parse_league(league: str) -> League:
    try:
        return League(league)
    except ValueError as exc:
        raise HTTPException(404, f"Unknown league '{league}'. Use nba or ncaam.") from exc


def rehearsal() -> bool:
    return load_settings().rehearsal


def season_types() -> list[str]:
    return sorted(COUNTED_SEASON_TYPES | ({"pre"} if rehearsal() else set()))


# -- response cache -------------------------------------------------------------------
# Responses are cached until the data changes. The data version is one cheap query over
# the tables that every screen depends on.

_cache: dict[tuple, tuple[Any, Any]] = {}
_cache_lock = threading.Lock()
_CACHE_LIMIT = 2000

_VERSION_SQL = """
SELECT (SELECT max(updated_at) FROM games WHERE league = %(league)s) AS games,
       (SELECT max(created_at) FROM predictions) AS predictions,
       (SELECT max(as_of) FROM team_ratings) AS ratings,
       (SELECT max(snapshot_time) FROM injury_snapshots) AS injuries,
       (SELECT max(graded_at) FROM prediction_grades) AS grades,
       (SELECT max(created_at) FROM model_versions) AS models,
       (SELECT refreshed_at FROM screen_refreshes) AS screens
"""


def data_version(conn: psycopg.Connection, league: League) -> tuple:
    return tuple(conn.execute(_VERSION_SQL, {"league": str(league)}).fetchone().values())


def cached(conn: psycopg.Connection, league: League, key: tuple,
           build: Callable[[], Any]) -> Any:
    version = data_version(conn, league)
    full_key = (str(league), *key)
    with _cache_lock:
        hit = _cache.get(full_key)
        if hit and hit[0] == version:
            return hit[1]
    value = build()
    with _cache_lock:
        if len(_cache) > _CACHE_LIMIT:
            _cache.clear()
        _cache[full_key] = (version, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# -- common lookups -------------------------------------------------------------------


def current_season(conn: psycopg.Connection, league: League,
                   now: datetime | None = None) -> int:
    """The season being played (or about to start): the latest season with a game no more
    than ten days in the future."""
    now = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT max(season) AS season FROM games WHERE league = %s AND start_time <= %s",
        (str(league), now + timedelta(days=10)),
    ).fetchone()
    if row and row["season"]:
        return row["season"]
    return now.year + 1 if now.month >= 8 else now.year


def seasons(conn: psycopg.Connection, league: League) -> list[int]:
    return [r["season"] for r in conn.execute(
        "SELECT DISTINCT season FROM games WHERE league = %s ORDER BY season DESC",
        (str(league),))]


def resolve_season(conn, league: League, season: int | None) -> int:
    return season or current_season(conn, league)


def teams_for_season(conn: psycopg.Connection, league: League, season: int) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT t.team_id, t.abbreviation, t.display_name, t.short_name, t.location, t.logo_url,
               t.color, ts.conference_name, ts.conference_abbr
        FROM teams t
        LEFT JOIN team_seasons ts ON ts.team_id = t.team_id AND ts.season = %s
        WHERE t.league = %s
        """,
        (season, str(league)),
    ).fetchall()
    return {r["team_id"]: {
        "id": r["team_id"], "abbreviation": r["abbreviation"], "name": r["display_name"],
        "short_name": r["short_name"], "location": r["location"], "logo": r["logo_url"],
        "color": r["color"], "conference": r["conference_name"],
        "conference_abbr": r["conference_abbr"],
    } for r in rows}


def latest_ratings(conn: psycopg.Connection, league: League, season: int,
                   before: datetime | None = None) -> dict[int, dict]:
    """Each team's most recent stored rating for a season (optionally before a time)."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (r.team_id) r.team_id, r.as_of, r.games_played, r.overall,
               r.offense, r.defense, r.overall_se, r.offense_se, r.defense_se, r.pace
        FROM team_ratings r JOIN teams t USING (team_id)
        JOIN model_versions m USING (model_version_id)
        WHERE t.league = %s AND r.season = %s AND (%s::timestamptz IS NULL OR r.as_of <= %s)
        ORDER BY r.team_id, (m.role = 'champion') DESC, r.as_of DESC
        """,
        (str(league), season, before, before),
    ).fetchall()
    return {r["team_id"]: dict(r) for r in rows}


def data_freshness(conn: psycopg.Connection, league: League,
                   now: datetime | None = None) -> dict:
    """When data last updated, and whether that is late enough to warn about."""
    now = now or datetime.now(UTC)
    row = conn.execute(
        """
        SELECT max(finished_at) FILTER (WHERE job_name = 'game_watcher') AS watcher,
               max(finished_at) FILTER (WHERE job_name = 'schedule_sync') AS schedule
        FROM job_runs WHERE league = %s AND status = 'succeeded'
        """,
        (str(league),),
    ).fetchone()
    games_now = conn.execute(
        "SELECT count(*) AS n FROM games WHERE league = %s AND status IN ('scheduled', 'live')"
        " AND start_time BETWEEN %s AND %s",
        (str(league), now - timedelta(hours=4), now + timedelta(minutes=45)),
    ).fetchone()["n"]
    last = max([t for t in (row["watcher"], row["schedule"]) if t], default=None)
    delayed = last is None or (now - last > timedelta(hours=6)) or (
        games_now > 0 and (row["watcher"] is None or now - row["watcher"] > timedelta(minutes=10)))
    return {"last_update": last, "delayed": bool(delayed)}
