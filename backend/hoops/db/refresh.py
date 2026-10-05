"""Refreshes the precomputed screen tables after games are stored."""

from __future__ import annotations

import psycopg


def refresh_screen_tables(conn: psycopg.Connection) -> None:
    # CONCURRENTLY keeps the tables readable during the refresh (needs the unique indexes).
    for view in ("player_season_stats", "team_season_stats"):
        conn.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}")
    conn.execute("UPDATE screen_refreshes SET refreshed_at = clock_timestamp()")
