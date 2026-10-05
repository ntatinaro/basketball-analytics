"""Loads ESPN data for one league: teams, schedules, finished games, rosters, injuries."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import psycopg

from hoops.espn import parse
from hoops.espn.client import EspnClient, EspnError
from hoops.ingest.derive import tag_garbage_time
from hoops.ingest.store import Store
from hoops.leagues import RULES, League
from hoops.quality.checks import check_game

log = logging.getLogger(__name__)


def season_window(league: League, season: int) -> tuple[date, date]:
    """Calendar dates that can hold a season's games (inclusive). Wide on purpose; games
    are filtered by ESPN's season label."""
    if league is League.NBA:
        return date(season - 1, 9, 25), date(season, 6, 30)
    return date(season - 1, 10, 25), date(season, 4, 15)


class Loader:
    def __init__(self, conn: psycopg.Connection, client: EspnClient, league: League) -> None:
        self.conn = conn
        self.client = client
        self.league = league
        self.rules = RULES[league]
        self.store = Store(conn, league)
        self._league_teams: set[str] | None = None

    # -- teams ------------------------------------------------------------------------

    def sync_teams(self) -> int:
        """Stores the league's teams (all 30 NBA teams, or every Division I team)."""
        teams = parse.parse_teams(self.client.teams(self.league).body)
        with self.conn.transaction():
            for team in teams:
                self.store.upsert_team(team)
        self._league_teams = {t.espn_id for t in teams}
        log.info("%s: %d teams", self.league, len(teams))
        return len(teams)

    def league_teams(self) -> set[str]:
        if self._league_teams is None:
            self._league_teams = self.store.known_team_espn_ids()
            if not self._league_teams:
                self.sync_teams()
        return self._league_teams

    def sync_conferences(self, season: int) -> int:
        body = self.client.standings(self.league, season).body
        return self.store.set_conferences(season, parse.parse_standings_conferences(body))

    def sync_rosters(self, season: int) -> int:
        """Current rosters with player details (height, weight, birth date, class)."""
        count = 0
        for espn_id in sorted(self.league_teams()):
            try:
                roster = parse.parse_roster(self.client.roster(self.league, espn_id).body)
            except EspnError as exc:
                log.warning("roster for team %s failed: %s", espn_id, exc)
                continue
            self.store.update_roster_details(espn_id, season, roster)
            count += len(roster)
        return count

    # -- games ------------------------------------------------------------------------

    def keep_game(self, game: parse.Game) -> bool:
        """Drops games we never store: NBA All-Star games (special team IDs) and college
        games involving a non-Division I team."""
        teams = self.league_teams()
        return game.home.espn_id in teams and game.away.espn_id in teams

    def sync_day(self, day: date, *, use_cache: bool = False) -> list[parse.Game]:
        games = [g for g in parse.parse_scoreboard(
            self.client.scoreboard(self.league, day, use_cache=use_cache).body
        ) if self.keep_game(g)]
        with self.conn.transaction():
            for game in games:
                self.store.upsert_game(game)
        return games

    def load_game(self, espn_id: str, *, use_cache: bool = False) -> int | None:
        """Fetches, checks, and stores one game's summary. Returns the game ID."""
        raw = self.client.summary(self.league, espn_id, use_cache=use_cache)
        summary = parse.parse_summary(raw.body)
        if not self.keep_game(summary.game):
            return None
        quality = check_game(summary)
        garbage = tag_garbage_time(summary, self.rules) if summary.plays else None
        return self.store.write_summary(summary, quality, garbage, raw.fetched_at)

    def sync_schedule(self, season: int, *, use_cache: bool = True) -> int:
        """Every game in a season. Days more than two days old are read from the raw cache
        when available; recent and future days are always fetched."""
        start, end = season_window(self.league, season)
        today = datetime.now(UTC).date()
        seen = 0
        day = start
        while day <= end:
            cached = use_cache and day < today - timedelta(days=2)
            seen += sum(g.season == season for g in self.sync_day(day, use_cache=cached))
            day += timedelta(days=1)
        log.info("%s %d: %d games in schedule", self.league, season, seen)
        return seen

    def games_to_load(self, season: int, *, reload: bool = False) -> list[str]:
        """Finished games whose summaries have not been stored yet."""
        return [r[0] for r in self.conn.execute(
            "SELECT espn_id FROM games WHERE league = %s AND season = %s AND status = 'final'"
            " AND (%s OR team_quality_ok IS NULL) ORDER BY start_time",
            (str(self.league), season, reload),
        )]

    def backfill(self, season: int, *, reload: bool = False) -> tuple[int, int]:
        """Loads a whole season: teams, conferences, schedule, and every finished game.
        Returns (games loaded, failures)."""
        self.league_teams()
        self.sync_conferences(season)
        self.sync_schedule(season)
        pending = self.games_to_load(season, reload=reload)
        loaded = failed = 0
        for i, espn_id in enumerate(pending, 1):
            try:
                if self.load_game(espn_id, use_cache=True) is not None:
                    loaded += 1
            except (EspnError, KeyError, ValueError, TypeError) as exc:
                failed += 1
                log.error("%s game %s failed: %s", self.league, espn_id, exc)
            if i % 200 == 0:
                log.info("%s %d: %d/%d games", self.league, season, i, len(pending))
        log.info("%s %d: %d games loaded, %d failed", self.league, season, loaded, failed)
        self.backfill_close_lines(season)
        return loaded, failed

    def backfill_close_lines(self, season: int) -> int:
        """Stores ESPN's labeled closing lines for finished games that have no line at all.
        Game summaries carry no odds for many past games; the odds endpoint does."""
        pending = self.conn.execute(
            """
            SELECT g.game_id, g.espn_id FROM games g
            WHERE g.league = %s AND g.season = %s AND g.status = 'final'
              AND g.season_type <> 'pre'
              AND NOT EXISTS (SELECT 1 FROM betting_lines b WHERE b.game_id = g.game_id)
            ORDER BY g.start_time
            """,
            (str(self.league), season),
        ).fetchall()
        found = 0
        for i, (game_id, espn_id) in enumerate(pending, 1):
            try:
                raw = self.client.odds(self.league, espn_id, use_cache=True)
            except EspnError as exc:
                log.info("%s game %s: no odds (%s)", self.league, espn_id, exc)
                continue
            lines = parse.parse_close_lines(raw.body)
            for line in lines:
                self.store.write_line(game_id, line, "espn_close", raw.fetched_at)
            found += bool(lines)
            if i % 200 == 0:
                log.info("%s %d: closing lines %d/%d games", self.league, season, i,
                         len(pending))
        log.info("%s %d: closing lines for %d of %d games", self.league, season, found,
                 len(pending))
        return found

    def sync_injuries(self) -> int:
        injuries = parse.parse_injuries(self.client.injuries(self.league).body)
        return self.store.write_injuries(datetime.now(UTC), injuries)
