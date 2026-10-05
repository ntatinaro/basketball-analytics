"""Command-line entry point: `hoops <command>`."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date

import psycopg

from hoops.db.migrate import migrate
from hoops.db.refresh import refresh_screen_tables
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

    p = sub.add_parser("close-lines", help="load ESPN closing lines for past seasons")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--seasons", type=parse_seasons, required=True)

    p = sub.add_parser("quality-report", help="print the per-season data quality report")
    p.add_argument("--league", type=League, choices=list(League), required=True)

    p = sub.add_parser("sync-rosters", help="load current rosters and player details")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--season", type=int, required=True)

    sub.add_parser("worker", help="run the background worker (all scheduled jobs)")

    p = sub.add_parser("exams", help="run rolling exams and choose the champion model")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--warmup", type=int, required=True, help="warm-up season, e.g. 2022")
    p.add_argument("--seasons", type=parse_seasons, required=True,
                   help="scored seasons, e.g. 2023-2026")
    p.add_argument("--candidates", type=int, default=40)
    p.add_argument("--report", type=str, help="also write the full report to this JSON file")

    p = sub.add_parser("rebuild-ratings", help="rebuild stored rating history for seasons")
    p.add_argument("--league", type=League, choices=list(League), required=True)
    p.add_argument("--seasons", type=parse_seasons, required=True)

    sub.add_parser("api", help="run the API server")

    p = sub.add_parser("refresh-predictions", help="refit ratings and predict upcoming games")
    p.add_argument("--league", type=League, choices=list(League), required=True)

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
    if args.command == "api":
        import uvicorn

        uvicorn.run("hoops.api.app:app", host="0.0.0.0", port=8000, proxy_headers=True,
                    log_level=settings.log_level.lower())
        return

    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        if args.command == "migrate":
            applied = migrate(conn)
            print("applied:", ", ".join(applied) if applied else "nothing")
            return
        if args.command == "quality-report":
            print(format_report(conn, args.league))
            return
        if args.command == "exams":
            from hoops.evaluation.exams import run_exams
            from hoops.evaluation.report import format_exam_report

            report = run_exams(conn, args.league, args.warmup, args.seasons, args.candidates)
            if args.report:
                with open(args.report, "w") as f:
                    json.dump(report, f, indent=2, default=str)
            print(format_exam_report(report))
            return
        if args.command == "rebuild-ratings":
            from hoops.models.history import rebuild

            print(f"{rebuild(conn, args.league, args.seasons)} rating rows written")
            return
        if args.command == "refresh-predictions":
            from hoops.models.live import ModelHooks

            hooks = ModelHooks(args.league, rehearsal=settings.rehearsal)
            hooks.refit(conn)
            print(f"{hooks.refresh(conn)} predictions written")
            return

        loader = Loader(conn, EspnClient(RawStore(settings.raw_dir)), args.league)
        if args.command == "backfill":
            loader.sync_teams()
            for season in args.seasons:
                loaded, failed = loader.backfill(season, reload=args.reload)
                print(f"{args.league} {season}: {loaded} games loaded, {failed} failed")
            refresh_screen_tables(conn)
        elif args.command == "close-lines":
            for season in args.seasons:
                found = loader.backfill_close_lines(season)
                print(f"{args.league} {season}: closing lines for {found} games")
        elif args.command == "sync-rosters":
            print(f"{loader.sync_rosters(args.season)} roster entries")
        elif args.command == "sync-day":
            print(f"{len(loader.sync_day(args.date))} games")


if __name__ == "__main__":
    main()
