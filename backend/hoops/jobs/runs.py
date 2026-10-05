"""Records every job run in `job_runs` (architecture doc, section 5)."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable
from typing import Any

import psycopg

log = logging.getLogger(__name__)


def run_logged(
    conn: psycopg.Connection,
    job_name: str,
    league: str | None,
    fn: Callable[[], dict[str, Any] | None],
) -> bool:
    """Runs `fn`, recording start, end, status, details, and any error.

    Never raises: a failing job is logged and recorded, and the scheduler moves on.
    Returns True on success. `conn` must be in autocommit mode; jobs group their own
    writes in transactions, which roll back on error.
    """
    (run_id,) = conn.execute(
        "INSERT INTO job_runs (job_name, league) VALUES (%s, %s) RETURNING job_run_id",
        (job_name, league),
    ).fetchone()
    try:
        details = fn() or {}
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded, whatever it is
        log.exception("job %s (%s) failed", job_name, league)
        conn.execute(
            "UPDATE job_runs SET finished_at = now(), status = 'failed', error = %s"
            " WHERE job_run_id = %s",
            (f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}", run_id),
        )
        return False
    conn.execute(
        "UPDATE job_runs SET finished_at = now(), status = 'succeeded', details = %s"
        " WHERE job_run_id = %s",
        (json.dumps(details, default=str), run_id),
    )
    return True


def last_success(conn: psycopg.Connection, job_name: str, league: str | None = None):
    row = conn.execute(
        "SELECT max(finished_at) FROM job_runs WHERE job_name = %s AND status = 'succeeded'"
        " AND (%s::text IS NULL OR league = %s)",
        (job_name, league, league),
    ).fetchone()
    return row[0] if row else None
