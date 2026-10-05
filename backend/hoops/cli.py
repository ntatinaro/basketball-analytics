"""Command-line entry point: `hoops <command>`."""

from __future__ import annotations

import argparse
import logging
from datetime import date

import psycopg

from hoops.db.migrate import migrate
from hoops.espn.client import EspnClient
from hoops.espn.raw_store import RawStore
from hoops.ingest.load import Loader
from hoops.leagues import League
from hoops.quality.report import format_report
from hoops.settings import load_settings


def parse_seasons(text: str) -> list[int]:
    """'2022-2026' -> [2022, ..., 2026]; '2024,2026' -> [2024, 2026]. Seasons are named by
    the year they end in."""
    seasons: list[int] = []
    for part in text.split(","):
        if "-" in part:
            first, last = (int(x) for x in part.split("-", 1))
            seasons.extend(range(first, last + 1))
        else:
            seasons.append(int(part))
    return seasons


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hoops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")

    p = sub.add_parser("backfill", help="load past seasons: schedules and finished games")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--seasons", type=parse_seasons, required=True,
                   help="season end years, e.g. 2022-2026")
    p.add_argument("--reload", action="store_true",
                   help="re-process games already loaded (from the raw cache)")

    p = sub.add_parser("quality-report", help="print the per-season data quality report")
    p.add_argument("--league", type=League, choices=list(League), required=True)

    p = sub.add_parser("sync-rosters", help="load current rosters and player details")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--season", type=int, required=True)

    sub.add_parser("worker", help="run the background worker (all scheduled jobs)")

    p = sub.add_parser("sync-day", help="refresh one day's games from the scoreboard")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--date", type=date.fromisoformat, default=date.today())
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "worker":
        from hoops.worker import run

        run(settings)
        return

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        if args.command == "migrate":
            applied = migrate(conn)
            print("applied:", ", ".join(applied) if applied else "nothing")
            return
        if args.command == "quality-report":
            print(format_report(conn, args.league))
            return

        loader = Loader(conn, EspnClient(RawStore(settings.raw_dir)), args.league)
        if args.command == "backfill":
            loader.sync_teams()
            for season in args.seasons:
                loaded, failed = loader.backfill(season, reload=args.reload)
                print(f"{args.league} {season}: {loaded} games loaded, {failed} failed")
        elif args.command == "sync-rosters":
            print(f"{loader.sync_rosters(args.season)} roster entries")
        elif args.command == "sync-day":
            print(f"{len(loader.sync_day(args.date))} games")


if __name__ == "__main__":
    main()
