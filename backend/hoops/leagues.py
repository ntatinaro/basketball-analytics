"""League rules and the capability matrix (architecture doc, section 6).

Seasons are named by the calendar year they end in: the 2025-26 season is 2026.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class League(StrEnum):
    NBA = "nba"
    NCAAM = "ncaam"


@dataclass(frozen=True)
class GarbageTimeWindow:
    """A lead of at least `min_lead` with at most `max_seconds_left` in the final period."""

    max_seconds_left: int
    min_lead: int


@dataclass(frozen=True)
class LeagueRules:
    espn_slug: str
    periods: int
    period_minutes: int
    overtime_minutes: int
    shot_clock_seconds: int
    garbage_time: tuple[GarbageTimeWindow, ...]
    first_season: int

    @property
    def regulation_seconds(self) -> int:
        return self.periods * self.period_minutes * 60

    def seconds_left_in_regulation(self, period: int, clock_seconds: float) -> float:
        """Game seconds left in regulation; 0 once overtime starts."""
        if period > self.periods:
            return 0.0
        return (self.periods - period) * self.period_minutes * 60 + clock_seconds

    def is_garbage_time(self, period: int, clock_seconds: float, lead: int) -> bool:
        """Garbage time applies only in the final regulation period, never in overtime."""
        if period != self.periods:
            return False
        return any(
            clock_seconds <= w.max_seconds_left and abs(lead) >= w.min_lead
            for w in self.garbage_time
        )


# Owner's definition: a lead of 25+ in the last 6 minutes, or 15+ in the last 3.
_GARBAGE_TIME = (GarbageTimeWindow(6 * 60, 25), GarbageTimeWindow(3 * 60, 15))

# Both leagues load data from 2021-22 onward, after the bubble seasons.
FIRST_SEASON = 2022

RULES: dict[League, LeagueRules] = {
    League.NBA: LeagueRules(
        espn_slug="nba",
        periods=4,
        period_minutes=12,
        overtime_minutes=5,
        shot_clock_seconds=24,
        garbage_time=_GARBAGE_TIME,
        first_season=FIRST_SEASON,
    ),
    League.NCAAM: LeagueRules(
        espn_slug="mens-college-basketball",
        periods=2,
        period_minutes=20,
        overtime_minutes=5,
        shot_clock_seconds=30,
        garbage_time=_GARBAGE_TIME,
        first_season=FIRST_SEASON,
    ),
}

# Season types whose games count for ratings, predictions, and grading.
# Preseason and the All-Star game never count. NBA Cup games are regular-season games.
COUNTED_SEASON_TYPES = frozenset({"regular", "play_in", "post"})

# Features check these at runtime and hide themselves when a capability is missing.
CAPABILITIES: dict[League, frozenset[str]] = {
    League.NBA: frozenset({
        "box_scores", "play_by_play", "live_play_by_play", "espn_win_probability",
        "injury_feed", "lineups",
    }),
    League.NCAAM: frozenset({
        "box_scores", "play_by_play", "live_play_by_play_partial", "espn_win_probability",
        "manual_absences", "bracket",
    }),
}


def has_capability(league: League, capability: str) -> bool:
    return capability in CAPABILITIES[league]
