"""Writes parsed ESPN data to Postgres. Every write is keyed by ESPN ID, so re-running
any load replaces rather than duplicates."""

from __future__ import annotations

from datetime import date, datetime

import psycopg

from hoops.espn.parse import (
    BettingLine,
    Game,
    GameSummary,
    Injury,
    PlayerBox,
    RosterPlayer,
    TeamRef,
)
from hoops.ingest.derive import GarbageTotals
from hoops.leagues import League
from hoops.quality.checks import QualityResult


class Store:
    def __init__(self, conn: psycopg.Connection, league: League) -> None:
        self.conn = conn
        self.league = league
        self._team_ids: dict[str, int] = {}
        self._player_ids: dict[str, int] = {}

    # -- teams and players ------------------------------------------------------------

    def upsert_team(self, team: TeamRef, *, is_division_1: bool = True) -> int:
        (team_id,) = self.conn.execute(
            """
            INSERT INTO teams (league, espn_id, abbreviation, display_name, short_name,
                               location, name, logo_url, color, is_division_1)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (league, espn_id) DO UPDATE SET
                abbreviation = EXCLUDED.abbreviation,
                display_name = EXCLUDED.display_name,
                short_name = COALESCE(EXCLUDED.short_name, teams.short_name),
                location = COALESCE(EXCLUDED.location, teams.location),
                name = COALESCE(EXCLUDED.name, teams.name),
                logo_url = COALESCE(EXCLUDED.logo_url, teams.logo_url),
                color = COALESCE(EXCLUDED.color, teams.color),
                is_division_1 = EXCLUDED.is_division_1,
                updated_at = now()
            RETURNING team_id
            """,
            (str(self.league), team.espn_id, team.abbreviation, team.display_name,
             team.short_name, team.location, team.name, team.logo_url, team.color,
             is_division_1),
        ).fetchone()
        self._team_ids[team.espn_id] = team_id
        return team_id

    def team_id(self, espn_id: str) -> int | None:
        if espn_id not in self._team_ids:
            row = self.conn.execute(
                "SELECT team_id FROM teams WHERE league = %s AND espn_id = %s",
                (str(self.league), espn_id),
            ).fetchone()
            if row is None:
                return None
            self._team_ids[espn_id] = row[0]
        return self._team_ids[espn_id]

    def known_team_espn_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute(
            "SELECT espn_id FROM teams WHERE league = %s", (str(self.league),)
        )}

    def upsert_player(self, espn_id: str, display_name: str, *, short_name: str | None = None,
                      position: str | None = None, headshot_url: str | None = None) -> int:
        cached = self._player_ids.get(espn_id)
        # The cache only saves ID lookups; new details (roster syncs) are always written.
        if cached is not None and short_name is None and position is None and headshot_url is None:
            return cached
        (player_id,) = self.conn.execute(
            """
            INSERT INTO players (league, espn_id, display_name, short_name, position, headshot_url)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (league, espn_id) DO UPDATE SET
                display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), players.display_name),
                short_name = COALESCE(EXCLUDED.short_name, players.short_name),
                position = COALESCE(EXCLUDED.position, players.position),
                headshot_url = COALESCE(EXCLUDED.headshot_url, players.headshot_url),
                updated_at = now()
            RETURNING player_id
            """,
            (str(self.league), espn_id, display_name or espn_id, short_name, position,
             headshot_url),
        ).fetchone()
        self._player_ids[espn_id] = player_id
        return player_id

    def update_roster_details(self, team_espn_id: str, season: int,
                              roster: list[RosterPlayer]) -> None:
        team_id = self.team_id(team_espn_id)
        with self.conn.transaction():
            for r in roster:
                player_id = self.upsert_player(r.player_espn_id, r.display_name,
                                               short_name=r.short_name, position=r.position,
                                               headshot_url=r.headshot_url)
                self.conn.execute(
                    """
                    UPDATE players SET height_inches = COALESCE(%s, height_inches),
                        weight_pounds = COALESCE(%s, weight_pounds),
                        birth_date = COALESCE(%s, birth_date),
                        class_year = COALESCE(%s, class_year), updated_at = now()
                    WHERE player_id = %s
                    """,
                    (r.height_inches, r.weight_pounds,
                     r.birth_date.date() if r.birth_date else None, r.class_year, player_id),
                )
                if team_id is not None:
                    self.conn.execute(
                        """
                        INSERT INTO roster_entries (player_id, team_id, season, jersey)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (player_id, team_id, season)
                        DO UPDATE SET jersey = COALESCE(EXCLUDED.jersey, roster_entries.jersey)
                        """,
                        (player_id, team_id, season, r.jersey),
                    )

    def set_conferences(self, season: int, conferences: dict[str, tuple[str, str | None]]) -> int:
        written = 0
        with self.conn.transaction():
            for espn_id, (name, abbr) in conferences.items():
                team_id = self.team_id(espn_id)
                if team_id is None:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO team_seasons (team_id, season, conference_name, conference_abbr)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (team_id, season) DO UPDATE SET
                        conference_name = EXCLUDED.conference_name,
                        conference_abbr = EXCLUDED.conference_abbr
                    """,
                    (team_id, season, name, abbr),
                )
                written += 1
        return written

    # -- games ------------------------------------------------------------------------

    def upsert_game(self, game: Game) -> int:
        home_id = self.upsert_team(game.home)
        away_id = self.upsert_team(game.away)
        (game_id,) = self.conn.execute(
            """
            INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,
                               away_team_id, neutral_site, conference_game, status,
                               home_score, away_score, periods_played)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (league, espn_id) DO UPDATE SET
                season = EXCLUDED.season, season_type = EXCLUDED.season_type,
                start_time = EXCLUDED.start_time, home_team_id = EXCLUDED.home_team_id,
                away_team_id = EXCLUDED.away_team_id, neutral_site = EXCLUDED.neutral_site,
                conference_game = EXCLUDED.conference_game, status = EXCLUDED.status,
                home_score = EXCLUDED.home_score, away_score = EXCLUDED.away_score,
                periods_played = COALESCE(EXCLUDED.periods_played, games.periods_played),
                updated_at = now()
            RETURNING game_id
            """,
            (str(self.league), game.espn_id, game.season, game.season_type, game.start_time,
             home_id, away_id, game.neutral_site, game.conference_game, game.status,
             game.home_score, game.away_score, game.periods_played),
        ).fetchone()
        return game_id

    def write_summary(
        self,
        summary: GameSummary,
        quality: QualityResult,
        garbage: dict[str, GarbageTotals] | None,
        fetched_at: datetime,
    ) -> int:
        """Replaces everything stored for one game with the summary's contents."""
        game = summary.game
        with self.conn.transaction():
            game_id = self.upsert_game(game)
            teams = {game.home.espn_id: self.team_id(game.home.espn_id),
                     game.away.espn_id: self.team_id(game.away.espn_id)}
            self._write_team_stats(game_id, summary, quality, garbage, teams)
            self._write_player_stats(game_id, summary, teams)
            self._write_plays(game_id, summary, teams)
            for line in summary.lines:
                self.write_line(game_id, line, "timing_unknown", fetched_at)
            self.conn.execute("DELETE FROM data_quality_issues WHERE game_id = %s", (game_id,))
            with self.conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO data_quality_issues (game_id, check_group, check_name, detail)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT (game_id, check_name) DO NOTHING",
                    [(game_id, i.group, i.name, i.detail) for i in quality.issues],
                )
            self.conn.execute(
                "UPDATE games SET team_quality_ok = %s, player_quality_ok = %s,"
                " pbp_quality_ok = %s WHERE game_id = %s",
                (quality.team_ok, quality.player_ok, quality.pbp_ok, game_id),
            )
        return game_id

    def _write_team_stats(self, game_id, summary, quality, garbage, teams) -> None:
        self.conn.execute("DELETE FROM team_game_stats WHERE game_id = %s", (game_id,))
        if not quality.team_ok:
            return
        game = summary.game
        rows = []
        for box in summary.team_box:
            is_home = box.team_espn_id == game.home.espn_id
            opponent = game.away.espn_id if is_home else game.home.espn_id
            g = garbage.get(box.team_espn_id) if (garbage and quality.pbp_ok) else None
            pts_ex = box.pts - g.points if g else None
            poss_ex = max(box.possessions - g.possessions, 1.0) if g else None
            rows.append((
                game_id, teams[box.team_espn_id], teams[opponent], is_home, box.pts, box.fgm,
                box.fga, box.fg3m, box.fg3a, box.ftm, box.fta, box.oreb, box.dreb, box.ast,
                box.stl, box.blk, box.tov, box.pf, box.possessions, pts_ex, poss_ex,
            ))
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO team_game_stats (game_id, team_id, opponent_id, is_home, pts, fgm,
                    fga, fg3m, fg3a, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf, possessions,
                    pts_excl_garbage, possessions_excl_garbage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s)
                """,
                rows,
            )

    def _write_player_stats(self, game_id: int, summary: GameSummary,
                            teams: dict[str, int]) -> None:
        self.conn.execute("DELETE FROM player_game_stats WHERE game_id = %s", (game_id,))
        game_day: date = summary.game.start_time.date()
        rows = []
        for p in summary.player_box:
            if p.team_espn_id not in teams:
                continue
            player_id = self.upsert_player(p.player_espn_id, p.display_name,
                                           short_name=p.short_name, position=p.position,
                                           headshot_url=p.headshot_url)
            rows.append(self._player_row(game_id, player_id, teams[p.team_espn_id], p))
            if not p.did_not_play:
                self.conn.execute(
                    """
                    INSERT INTO roster_entries (player_id, team_id, season, jersey, first_game,
                                                last_game)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (player_id, team_id, season) DO UPDATE SET
                        jersey = COALESCE(EXCLUDED.jersey, roster_entries.jersey),
                        first_game = LEAST(roster_entries.first_game, EXCLUDED.first_game),
                        last_game = GREATEST(roster_entries.last_game, EXCLUDED.last_game)
                    """,
                    (player_id, teams[p.team_espn_id], summary.game.season, p.jersey,
                     game_day, game_day),
                )
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO player_game_stats (game_id, player_id, team_id, starter, did_not_play,
                    dnp_reason, ejected, minutes, pts, fgm, fga, fg3m, fg3a, ftm, fta, oreb,
                    dreb, reb, ast, stl, blk, tov, pf, plus_minus)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (game_id, player_id) DO NOTHING
                """,
                rows,
            )

    @staticmethod
    def _player_row(game_id: int, player_id: int, team_id: int, p: PlayerBox) -> tuple:
        return (game_id, player_id, team_id, p.starter, p.did_not_play, p.dnp_reason, p.ejected,
                p.minutes, p.pts, p.fgm, p.fga, p.fg3m, p.fg3a, p.ftm, p.fta, p.oreb, p.dreb,
                p.reb, p.ast, p.stl, p.blk, p.tov, p.pf, p.plus_minus)

    def _write_plays(self, game_id: int, summary: GameSummary, teams: dict[str, int]) -> None:
        self.conn.execute("DELETE FROM plays WHERE game_id = %s", (game_id,))
        if not summary.plays:
            return
        with self.conn.cursor() as cur, cur.copy(
            "COPY plays (game_id, sequence, espn_play_id, period, clock_seconds, team_id,"
            " type_id, type_text, text, scoring_play, points, shooting_play, home_score,"
            " away_score, x, y, player_ids, wallclock, is_garbage_time) FROM STDIN"
        ) as copy:
            for p in summary.plays:
                player_ids = [self._player_ids[e] for e in p.participant_espn_ids
                              if e in self._player_ids]
                copy.write_row((
                    game_id, p.sequence, p.espn_play_id, p.period, p.clock_seconds,
                    teams.get(p.team_espn_id) if p.team_espn_id else None, p.type_id,
                    p.type_text, p.text, p.scoring_play, p.points, p.shooting_play,
                    p.home_score, p.away_score, p.x, p.y, player_ids, p.wallclock,
                    p.is_garbage_time,
                ))

    def write_line(self, game_id: int, line: BettingLine, kind: str,
                   captured_at: datetime) -> None:
        self.conn.execute(
            """
            INSERT INTO betting_lines (game_id, provider, line_kind, spread_home, total,
                                       home_moneyline, away_moneyline, captured_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (game_id, provider, line_kind) DO UPDATE SET
                spread_home = EXCLUDED.spread_home, total = EXCLUDED.total,
                home_moneyline = EXCLUDED.home_moneyline,
                away_moneyline = EXCLUDED.away_moneyline, captured_at = EXCLUDED.captured_at
            """,
            (game_id, line.provider, kind, line.spread_home, line.total, line.home_moneyline,
             line.away_moneyline, captured_at),
        )

    def write_injuries(self, snapshot_time: datetime, injuries: list[Injury]) -> int:
        written = 0
        with self.conn.transaction():
            for injury in injuries:
                player_id = self.upsert_player(injury.player_espn_id, injury.player_name)
                team_id = self.team_id(injury.team_espn_id) if injury.team_espn_id else None
                self.conn.execute(
                    """
                    INSERT INTO injury_snapshots (snapshot_time, player_id, team_id, status,
                                                  detail, espn_updated)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    (snapshot_time, player_id, team_id, injury.status, injury.detail,
                     injury.espn_updated),
                )
                written += 1
        return written
