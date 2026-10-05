"""The background worker: runs every scheduled job (architecture doc, section 5).

Jobs run one at a time, at low CPU priority, so the website stays responsive.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from hoops.espn.client import EspnClient
from hoops.espn.raw_store import RawStore
from hoops.ingest.load import Loader
from hoops.jobs.runs import run_logged
from hoops.jobs.tasks import GameHooks, Jobs
from hoops.leagues import League
from hoops.models.live import ModelHooks
from hoops.settings import Settings

log = logging.getLogger(__name__)
LOST_DB_EXIT_CODE = 3
DB_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)


def build_jobs(settings: Settings, league: League, hooks: GameHooks) -> Jobs:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    loader = Loader(conn, EspnClient(RawStore(settings.raw_dir)), league)
    return Jobs(loader=loader, hooks=hooks, rehearsal=settings.rehearsal)


def make_hooks(league: League, rehearsal: bool) -> GameHooks:
    return ModelHooks(league, rehearsal=rehearsal)


def make_job(jobs: Jobs, name: str, fn: Callable[[], dict],
             on_lost_db: Callable[[], None]) -> Callable[[], None]:
    """Wraps a job so it is recorded in job_runs, and so a lost database connection stops
    the worker (systemd then restarts it with a fresh connection) instead of leaving it
    running and doing nothing."""
    def job() -> None:
        try:
            run_logged(jobs.conn, name, str(jobs.league), fn)
        except DB_ERRORS:
            log.critical("lost the database connection during %s; stopping the worker", name)
            on_lost_db()
            return
        if jobs.conn.closed or jobs.conn.broken:
            log.critical("database connection is broken after %s; stopping the worker", name)
            on_lost_db()
    return job


def run(settings: Settings) -> None:
    """Runs every scheduled job. `hoops worker` has already lowered the CPU priority."""
    scheduler = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone="America/New_York",
    )
    lost_db = threading.Event()

    def stop_for_lost_db() -> None:
        lost_db.set()
        scheduler.shutdown(wait=False)

    for league in settings.leagues:
        jobs = build_jobs(settings, league, make_hooks(league, settings.rehearsal))

        def logged(name: str, fn: Callable[[], dict], jobs: Jobs = jobs) -> Callable[[], None]:
            return make_job(jobs, name, fn, stop_for_lost_db)

        scheduler.add_job(logged("reference_sync", jobs.daily_reference_sync), "cron",
                          hour=4, minute=5, id=f"{league}-reference", name=f"{league}-reference")
        scheduler.add_job(logged("schedule_sync", jobs.schedule_sync), "interval", hours=3,
                          next_run_time=datetime.now(UTC), id=f"{league}-schedule",
                          name=f"{league}-schedule")
        scheduler.add_job(logged("injury_sync", jobs.injury_sync), "interval", minutes=15,
                          id=f"{league}-injuries", name=f"{league}-injuries")
        scheduler.add_job(logged("game_watcher", jobs.game_watcher), "interval", minutes=1,
                          id=f"{league}-watcher", name=f"{league}-watcher")
        scheduler.add_job(logged("model_checkpoint", jobs.model_checkpoint), "cron", day=1,
                          hour=4, minute=45, id=f"{league}-checkpoint",
                          name=f"{league}-checkpoint")
        scheduler.add_job(logged("overnight", jobs.overnight), "cron", hour=4, minute=15,
                          id=f"{league}-overnight", name=f"{league}-overnight")
    log.info("worker started for %s (rehearsal=%s)",
             ", ".join(map(str, settings.leagues)), settings.rehearsal)
    scheduler.start()
    if lost_db.is_set():
        raise SystemExit(LOST_DB_EXIT_CODE)

