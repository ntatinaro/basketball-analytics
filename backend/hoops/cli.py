"""Command-line entry point: `hoops <command>`."""

from __future__ import annotations

import argparse
import logging

import psycopg

from hoops.db.migrate import migrate
from hoops.settings import load_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hoops")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")
    args = parser.parse_args(argv)

    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.command == "migrate":
        with psycopg.connect(settings.database_url) as conn:
            applied = migrate(conn)
        print("applied:", ", ".join(applied) if applied else "nothing")


if __name__ == "__main__":
    main()
