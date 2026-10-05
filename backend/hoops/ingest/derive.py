"""Values derived from play-by-play when a game is stored."""

from __future__ import annotations

from dataclasses import dataclass

from hoops.espn.parse import GameSummary
from hoops.leagues import LeagueRules


@dataclass(frozen=True)
class GarbageTotals:
    """Points scored and possessions used by one team's offense during garbage time."""

    points: int
    possessions: float


def tag_garbage_time(summary: GameSummary, rules: LeagueRules) -> dict[str, GarbageTotals]:
    """Marks garbage-time plays and totals what each team did during them.

    A play is garbage time when, before it happens, the lead and clock meet the owner's
    definition (rules.garbage_time). Possessions are counted the same way as the box-score
    estimate: field goal attempts - offensive rebounds + turnovers + 0.44 x free throws.
    """
    home_id, away_id = summary.game.home.espn_id, summary.game.away.espn_id
    counts = {
        team: {"points": 0, "fga": 0, "oreb": 0, "tov": 0, "fta": 0}
        for team in (home_id, away_id)
    }
    home_before = away_before = 0
    for play in summary.plays:
        lead = home_before - away_before
        play.is_garbage_time = rules.is_garbage_time(play.period, play.clock_seconds, lead)
        team = play.team_espn_id
        if play.is_garbage_time and team in counts:
            c = counts[team]
            c["points"] += play.points
            c["fga"] += play.is_field_goal_attempt
            c["oreb"] += play.is_offensive_rebound
            c["tov"] += play.is_turnover
            c["fta"] += play.is_free_throw
        home_before, away_before = play.home_score, play.away_score
    return {
        team: GarbageTotals(
            points=c["points"],
            possessions=c["fga"] - c["oreb"] + c["tov"] + 0.44 * c["fta"],
        )
        for team, c in counts.items()
    }
