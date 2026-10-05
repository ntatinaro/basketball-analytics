"""The worker's jobs (architecture doc, section 5).

Model steps (locking, grading, refitting) plug in through `GameHooks`, so the data jobs
work on their own and the models can be developed and tested separately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import psycopg

from hoops.db.refresh import refresh_screen_tables
from hoops.espn import parse
from hoops.ingest.load import Loader
from hoops.leagues import COUNTED_SEASON_TYPES, League

log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")
LOCK_BEFORE_TIP = timedelta(minutes=30)
WATCH_AHEAD = timedelta(minutes=45)       # watch games starting this soon
WATCH_BEHIND = timedelta(hours=8)         # and games that started this long ago


def current_season(today: date) -> int:
    """Seasons are named by the year they end in; a new season starts in August."""
    return today.year + 1 if today.month >= 8 else today.year


def eastern_date(moment: datetime) -> date:
    return moment.astimezone(EASTERN).date()


class GameHooks(Protocol):
    def lock(self, conn: psycopg.Connection, game_id: int, now: datetime) -> None: ...

    def after_final(self, conn: psycopg.Connection, game_id: int) -> None: ...

    def after_injuries(self, conn: psycopg.Connection, league: League) -> None: ...

    def overnight(self, conn: psycopg.Connection, league: League) -> None: ...

    def refresh(self, conn: psycopg.Connection, league: League) -> int: ...


class NoModelHooks:
    """Used until models are attached: data jobs still run."""

    def lock(self, conn, game_id, now) -> None:
        pass

    def after_final(self, conn, game_id) -> None:
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
        now = now or datetime.now(UTC)
        watched = self.watched_games(now)
        details = {"watched": len(watched), "locked": 0, "finished": 0}
        if watched:
            for day in sorted({eastern_date(g[2]) for g in watched}):
                self.loader.sync_day(day)
        for game_id, espn_id in self.unlocked_games_near_tip(now):
            self.capture_pregame_line(game_id, espn_id)
            self.hooks.lock(self.conn, game_id, now)
            details["locked"] += 1
        finished = []
        for game_id, espn_id in self.finished_games_to_load(now):
            self.loader.load_game(espn_id)
            self.capture_close_line(game_id, espn_id)
            finished.append(game_id)
        if finished:
            refresh_screen_tables(self.conn)
        for game_id in finished:
            self.hooks.after_final(self.conn, game_id)
        details["finished"] = len(finished)
        return details

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
        """Re-pulls the previous day's finished games for official corrections."""
        now = now or datetime.now(UTC)
        yesterday = eastern_date(now) - timedelta(days=1)
        start = datetime.combine(yesterday, datetime.min.time(), EASTERN)
        rows = self.conn.execute(
            "SELECT espn_id FROM games WHERE league = %s AND status = 'final'"
            " AND start_time >= %s AND start_time < %s",
            (str(self.league), start, start + timedelta(days=1)),
        ).fetchall()
        for (espn_id,) in rows:
            self.loader.load_game(espn_id)
        refresh_screen_tables(self.conn)
        self.hooks.overnight(self.conn, self.league)
        return {"reloaded": len(rows)}
