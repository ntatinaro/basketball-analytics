"""Applies the numbered SQL files in `migrations/` that have not run yet."""

from __future__ import annotations

import logging
from importlib import resources

import psycopg

log = logging.getLogger(__name__)


def migration_files() -> list[tuple[str, str]]:
    folder = resources.files("hoops.db") / "migrations"
    files = sorted((f for f in folder.iterdir() if f.name.endswith(".sql")), key=lambda f: f.name)
    return [(f.name, f.read_text()) for f in files]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply pending migrations. Returns the names applied.

    All pending migrations run in one transaction: if any fails, none are applied.
    A lock keeps two processes (say, a deploy and a worker restart) from migrating at once.
    """
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('hoops_migrate'))")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for name, sql in migration_files():
            if name in done:
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            log.info("applied migration %s", name)
            applied.append(name)
    return applied
