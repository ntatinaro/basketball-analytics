"""The background worker: runs every scheduled job (architecture doc, section 5).

Jobs run one at a time, at low CPU priority, so the website stays responsive.
"""

from __future__ import annotations

import logging
import os
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


def build_jobs(settings: Settings, league: League, hooks: GameHooks) -> Jobs:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    loader = Loader(conn, EspnClient(RawStore(settings.raw_dir)), league)
    return Jobs(loader=loader, hooks=hooks, rehearsal=settings.rehearsal)


def make_hooks(league: League, rehearsal: bool) -> GameHooks:
    return ModelHooks(league, rehearsal=rehearsal)


def run(settings: Settings) -> None:
    try:
        os.nice(10)
    except OSError:
        log.warning("could not lower CPU priority")

    scheduler = BlockingScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone="America/New_York",
    )
    for league in settings.leagues:
        jobs = build_jobs(settings, league, make_hooks(league, settings.rehearsal))

        def logged(name: str, fn: Callable[[], dict], jobs: Jobs = jobs) -> Callable[[], bool]:
            return lambda: run_logged(jobs.conn, name, str(jobs.league), fn)

        scheduler.add_job(logged("reference_sync", jobs.daily_reference_sync), "cron",
                          hour=6, minute=5, id=f"{league}-reference", name=f"{league}-reference")
        scheduler.add_job(logged("schedule_sync", jobs.schedule_sync), "interval", hours=3,
                          next_run_time=datetime.now(UTC), id=f"{league}-schedule",
                          name=f"{league}-schedule")
        scheduler.add_job(logged("injury_sync", jobs.injury_sync), "interval", minutes=15,
                          id=f"{league}-injuries", name=f"{league}-injuries")
        scheduler.add_job(logged("game_watcher", jobs.game_watcher), "interval", minutes=1,
                          id=f"{league}-watcher", name=f"{league}-watcher")
        scheduler.add_job(logged("overnight", jobs.overnight), "cron", hour=4, minute=15,
                          id=f"{league}-overnight", name=f"{league}-overnight")
    log.info("worker started for %s (rehearsal=%s)",
             ", ".join(map(str, settings.leagues)), settings.rehearsal)
    scheduler.start()

