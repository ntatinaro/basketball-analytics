"""Per-season data quality report (architecture doc, section 8)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from hoops.leagues import COUNTED_SEASON_TYPES, League

THRESHOLD = 0.02   # more than 2% failing in a group is reported to the owner


@dataclass(frozen=True)
class SeasonQuality:
    season: int
    games: int
    team_failures: int
    player_failures: int
    pbp_failures: int
    lineup_issues: int

    def rate(self, failures: int) -> float:
        return failures / self.games if self.games else 0.0

    @property
    def flagged_groups(self) -> list[str]:
        return [name for name, n in (("team", self.team_failures),
                                     ("player", self.player_failures),
                                     ("pbp", self.pbp_failures))
                if self.rate(n) > THRESHOLD]


def season_quality(conn: psycopg.Connection, league: League) -> list[SeasonQuality]:
    rows = conn.execute(
        """
        SELECT g.season, count(*),
               count(*) FILTER (WHERE NOT g.team_quality_ok),
               count(*) FILTER (WHERE NOT g.player_quality_ok),
               count(*) FILTER (WHERE NOT g.pbp_quality_ok),
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM data_quality_issues i
                   WHERE i.game_id = g.game_id AND i.check_name = 'lineups_inconsistent'))
        FROM games g
        WHERE g.league = %s AND g.status = 'final' AND g.team_quality_ok IS NOT NULL
          AND g.season_type = ANY(%s)
        GROUP BY g.season ORDER BY g.season
        """,
        (str(league), sorted(COUNTED_SEASON_TYPES)),
    ).fetchall()
    return [SeasonQuality(*r) for r in rows]


def issue_breakdown(conn: psycopg.Connection, league: League, season: int) -> list[tuple]:
    return conn.execute(
        """
        SELECT i.check_group, i.check_name, count(*)
        FROM data_quality_issues i JOIN games g USING (game_id)
        WHERE g.league = %s AND g.season = %s
        GROUP BY 1, 2 ORDER BY 1, 3 DESC
        """,
        (str(league), season),
    ).fetchall()


def format_report(conn: psycopg.Connection, league: League) -> str:
    lines = [f"Data quality report: {league}",
             f"{'season':<9}{'games':>6}{'team':>14}{'player':>14}{'pbp':>14}{'lineups':>10}"]
    for q in season_quality(conn, league):
        team, player, pbp = (_cell(q, n) for n in
                             (q.team_failures, q.player_failures, q.pbp_failures))
        flag = "  <-- over 2%: " + ", ".join(q.flagged_groups) if q.flagged_groups else ""
        lines.append(f"{q.season - 1}-{q.season % 100:02d} {q.games:>6}{team:>14}{player:>14}"
                     f"{pbp:>14}{q.lineup_issues:>10}{flag}")
        for group, name, n in issue_breakdown(conn, league, q.season):
            lines.append(f"            {group:<7} {name:<28} {n}")
    return "\n".join(lines)


def _cell(q: SeasonQuality, failures: int) -> str:
    return f"{failures} ({100 * q.rate(failures):.1f}%)"
