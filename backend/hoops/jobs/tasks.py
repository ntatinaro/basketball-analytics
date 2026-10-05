"""The worker's jobs (architecture doc, section 5).

Model steps (locking, grading, refitting) plug in through `GameHooks`, so the data jobs
work on their own and the models can be developed and tested separately.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import psycopg

from hoops.db.refresh import refresh_screen_tables
from hoops.espn import parse
from hoops.espn.client import EspnError
from hoops.ingest.load import Loader
from hoops.leagues import COUNTED_SEASON_TYPES, League

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
LOCK_BEFORE_TIP = timedelta(minutes=30)
WATCH_AHEAD = timedelta(minutes=45)       # watch games starting this soon
WATCH_BEHIND = timedelta(hours=8)         # and games that started this long ago
MAX_LOAD_FAILURES = 3                     # a final failing this often waits for overnight
RAW_KEEP = timedelta(days=7)              # superseded raw responses older than this go
# ESPN outages and unexpected payloads: recorded per game, never allowed to stop a job.
FETCH_ERRORS = (EspnError, KeyError, ValueError, TypeError)


def current_season(today: date) -> int:
    """Seasons are named by the year they end in; a new season starts in August."""
    return today.year + 1 if today.month >= 8 else today.year


def eastern_date(moment: datetime) -> date:
    return moment.astimezone(EASTERN).date()


class GameHooks(Protocol):
    def lock(self, conn: psycopg.Connection, game_id: int, now: datetime) -> None: ...

    def after_finals(self, conn: psycopg.Connection, game_ids: list[int]) -> None: ...

    def after_injuries(self, conn: psycopg.Connection, league: League) -> None: ...

    def overnight(self, conn: psycopg.Connection, league: League) -> None: ...

    def refresh(self, conn: psycopg.Connection, league: League) -> int: ...


class NoModelHooks:
    """Used until models are attached: data jobs still run."""

    def lock(self, conn, game_id, now) -> None:
        pass

    def after_finals(self, conn, game_ids) -> None:
        pass

    def after_injuries(self, conn, league) -> None:
        pass

    def overnight(self, conn, league) -> None:
        pass

    def refresh(self, conn, league) -> int:
        return 0


@dataclass
class Jobs:
    loader: Loader
    hooks: GameHooks
    rehearsal: bool = False          # treat preseason games as predictable, for testing
    load_failures: dict[int, int] = field(default_factory=dict)   # game_id -> failed loads

    @property
    def conn(self) -> psycopg.Connection:
        return self.loader.conn

    @property
    def league(self) -> League:
        return self.loader.league

    def season_types(self) -> list[str]:
        types = set(COUNTED_SEASON_TYPES)
        if self.rehearsal:
            types.add("pre")
        return sorted(types)

    # -- schedule ---------------------------------------------------------------------

    def schedule_sync(self, now: datetime | None = None) -> dict:
        """Scoreboards for the past 2 and next 7 days (Eastern dates)."""
        now = now or datetime.now(UTC)
        today = eastern_date(now)
        games = 0
        for offset in range(-2, 8):
            games += len(self.loader.sync_day(today + timedelta(days=offset)))
        predicted = self.hooks.refresh(self.conn, self.league)
        return {"games": games, "predictions_written": predicted}

    def daily_reference_sync(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        season = current_season(eastern_date(now))
        teams = self.loader.sync_teams()
        conferences = self.loader.sync_conferences(season)
        rosters = self.loader.sync_rosters(season)
        return {"teams": teams, "conferences": conferences, "roster_entries": rosters}

    # -- injuries ---------------------------------------------------------------------

    def has_games_today(self, now: datetime) -> bool:
        start = datetime.combine(eastern_date(now), datetime.min.time(), EASTERN)
        row = self.conn.execute(
            "SELECT 1 FROM games WHERE league = %s AND start_time >= %s AND start_time < %s"
            " AND season_type = ANY(%s) LIMIT 1",
            (str(self.league), start, start + timedelta(days=1), self.season_types()),
        ).fetchone()
        return row is not None

    def injury_sync(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        if not self.has_games_today(now):
            return {"skipped": "no games today"}
        count = self.loader.sync_injuries()
        self.hooks.after_injuries(self.conn, self.league)
        return {"injuries": count}

    # -- game watcher -----------------------------------------------------------------

    def watched_games(self, now: datetime) -> list[tuple]:
        return self.conn.execute(
            """
            SELECT game_id, espn_id, start_time, status FROM games
            WHERE league = %s AND season_type = ANY(%s)
              AND start_time BETWEEN %s AND %s AND status IN ('scheduled', 'live')
            ORDER BY start_time
            """,
            (str(self.league), self.season_types(), now - WATCH_BEHIND, now + WATCH_AHEAD),
        ).fetchall()

    def unlocked_games_near_tip(self, now: datetime) -> list[tuple[int, str]]:
        return self.conn.execute(
            """
            SELECT g.game_id, g.espn_id FROM games g
            WHERE g.league = %s AND g.season_type = ANY(%s) AND g.status = 'scheduled'
              AND g.start_time BETWEEN %s AND %s
              AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.game_id = g.game_id
                              AND p.is_locked AND NOT p.is_shadow)
            """,
            (str(self.league), self.season_types(), now - timedelta(hours=2),
             now + LOCK_BEFORE_TIP),
        ).fetchall()

    def finished_games_to_load(self, now: datetime) -> list[tuple[int, str]]:
        """Final games not yet stored in full, from the last 3 days (catches up after downtime)."""
        return self.conn.execute(
            """
            SELECT game_id, espn_id FROM games
            WHERE league = %s AND status = 'final' AND team_quality_ok IS NULL
              AND start_time > %s ORDER BY start_time
            """,
            (str(self.league), now - timedelta(days=3)),
        ).fetchall()

    def game_watcher(self, now: datetime | None = None) -> dict:
        """Locks games near tip-off and stores finished ones. Every game is handled on its
        own: an ESPN outage or one bad game never stops the others, and locking needs
        nothing from ESPN."""
        now = now or datetime.now(UTC)
        watched = self.watched_games(now)
        details: dict = {"watched": len(watched), "locked": 0, "finished": 0, "errors": []}
        for day in sorted({eastern_date(g[2]) for g in watched}):
            self._isolated(details, f"scoreboard {day}",
                           lambda day=day: self.loader.sync_day(day), FETCH_ERRORS)
        for game_id, espn_id in self.unlocked_games_near_tip(now):
            self._isolated(details, f"pregame line {espn_id}",
                           lambda g=game_id, e=espn_id: self.capture_pregame_line(g, e),
                           FETCH_ERRORS)
            if self._isolated(details, f"lock {game_id}",
                              lambda g=game_id: self.hooks.lock(self.conn, g, now)):
                details["locked"] += 1
        finished = []
        for game_id, espn_id in self.finished_games_to_load(now):
            if self.load_failures.get(game_id, 0) >= MAX_LOAD_FAILURES:
                continue                     # retried by the overnight job
            if not self._isolated(details, f"final {espn_id}",
                                  lambda e=espn_id: self.loader.load_game(e), FETCH_ERRORS):
                self.load_failures[game_id] = self.load_failures.get(game_id, 0) + 1
                continue
            self.load_failures.pop(game_id, None)
            self.capture_close_line(game_id, espn_id)
            finished.append(game_id)
        if finished:
            refresh_screen_tables(self.conn)
            self._isolated(details, "after finals",
                           lambda: self.hooks.after_finals(self.conn, finished))
        details["finished"] = len(finished)
        if not details["errors"]:
            del details["errors"]
        return details

    @staticmethod
    def _isolated(details: dict, label: str, fn: Callable[[], object],
                  errors: tuple[type[BaseException], ...] = (Exception,)) -> bool:
        """Runs one step, recording a failure in the job details instead of raising. A lost
        database connection is not caught: the worker exits so systemd restarts it."""
        try:
            fn()
            return True
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise
        except errors as exc:
            log.warning("%s failed: %s", label, exc)
            details.setdefault("errors", []).append(f"{label}: {type(exc).__name__}: {exc}")
            return False

    def capture_pregame_line(self, game_id: int, espn_id: str) -> None:
        raw = self.loader.client.summary(self.league, espn_id)
        for line in parse.parse_summary(raw.body).lines:
            self.loader.store.write_line(game_id, line, "captured_pregame", raw.fetched_at)

    def capture_close_line(self, game_id: int, espn_id: str) -> None:
        try:
            raw = self.loader.client.odds(self.league, espn_id)
        except Exception:  # noqa: BLE001 - closing lines are optional
            log.info("no closing line for game %s", espn_id)
            return
        for line in parse.parse_close_lines(raw.body):
            self.loader.store.write_line(game_id, line, "espn_close", raw.fetched_at)

    # -- overnight --------------------------------------------------------------------

    def overnight(self, now: datetime | None = None) -> dict:
        """Re-pulls the previous day's finished games for official corrections, retries
        finals that failed to load, grades anything ungraded, and prunes raw responses."""
        now = now or datetime.now(UTC)
        yesterday = eastern_date(now) - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time(), EASTERN)
        rows = self.conn.execute(
            "SELECT game_id, espn_id FROM games WHERE league = %s AND status = 'final'"
            " AND ((start_time >= %s AND start_time < %s) OR team_quality_ok IS NULL)",
            (str(self.league), start, start + timedelta(days=1)),
        ).fetchall()
        details: dict = {"reloaded": 0, "errors": []}
        for _game_id, espn_id in rows:
            if self._isolated(details, f"reload {espn_id}",
                              lambda e=espn_id: self.loader.load_game(e), FETCH_ERRORS):
                details["reloaded"] += 1
        self.load_failures.clear()
        refresh_screen_tables(self.conn)
        self.hooks.overnight(self.conn, self.league)
        store, league = self.loader.client.raw_store, str(self.league)
        details["raw_pruned"] = sum(
            store.prune(league, endpoint, now - RAW_KEEP, keep_latest=True)
            for endpoint in ("scoreboard", "summary", "injuries", "odds"))
        if not details["errors"]:
            del details["errors"]
        return details
