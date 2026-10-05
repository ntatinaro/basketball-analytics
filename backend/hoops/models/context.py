"""Game context for the predictor: rest, travel distance, and absent players."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

# Approximate arena locations (latitude, longitude) by ESPN team abbreviation.
NBA_ARENAS = {
    "ATL": (33.757, -84.396), "BOS": (42.366, -71.062), "BKN": (40.683, -73.976),
    "CHA": (35.225, -80.839), "CHI": (41.881, -87.674), "CLE": (41.496, -81.688),
    "DAL": (32.790, -96.810), "DEN": (39.749, -105.008), "DET": (42.341, -83.055),
    "GS": (37.768, -122.388), "HOU": (29.751, -95.362), "IND": (39.764, -86.156),
    "LAC": (33.945, -118.341), "LAL": (34.043, -118.267), "MEM": (35.138, -90.051),
    "MIA": (25.781, -80.188), "MIL": (43.045, -87.917), "MIN": (44.980, -93.276),
    "NO": (29.949, -90.082), "NY": (40.751, -73.993), "OKC": (35.463, -97.515),
    "ORL": (28.539, -81.384), "PHI": (39.901, -75.172), "PHX": (33.446, -112.071),
    "POR": (45.532, -122.667), "SAC": (38.580, -121.500), "SA": (29.427, -98.438),
    "TOR": (43.643, -79.379), "UTAH": (40.768, -111.901), "WSH": (38.898, -77.021),
}
MAX_REST_DAYS = 4


def miles_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * math.asin(math.sqrt(h))


@dataclass
class GameContext:
    home_rest_days: int = MAX_REST_DAYS
    away_rest_days: int = MAX_REST_DAYS
    home_travel_miles: float = 0.0
    away_travel_miles: float = 0.0
    home_absence: float = 0.0          # net rating points lost to absences (>= 0 = worse)
    away_absence: float = 0.0
    home_out: list[str] = field(default_factory=list)
    away_out: list[str] = field(default_factory=list)

    def features(self) -> tuple[float, float, float]:
        """(home back-to-back, away back-to-back, travel difference in 1000s of miles)."""
        return (float(self.home_rest_days <= 1), float(self.away_rest_days <= 1),
                (self.away_travel_miles - self.home_travel_miles) / 1000.0)


def schedule_context(schedule: pd.DataFrame, abbreviations: dict[int, str]) -> dict[int, tuple]:
    """Rest days and travel miles for both teams of every game in `schedule`.

    `schedule` has game_id, game_date, home_team_id, away_team_id (all games of a season,
    including unplayed ones). Returns game_id -> (home rest, away rest, home miles, away miles).
    """
    location = {t: NBA_ARENAS.get(a) for t, a in abbreviations.items()}
    last_seen: dict[int, tuple[date, tuple[float, float] | None]] = {}
    out: dict[int, tuple] = {}
    for g in schedule.sort_values(["game_date", "game_id"]).itertuples(index=False):
        venue = location.get(g.home_team_id)
        values = []
        for team in (g.home_team_id, g.away_team_id):
            prev = last_seen.get(team)
            if prev is None:
                rest, miles = MAX_REST_DAYS, 0.0
            else:
                rest = min((g.game_date - prev[0]).days, MAX_REST_DAYS)
                miles = miles_between(prev[1], venue) if prev[1] and venue else 0.0
            values.append((rest, miles))
            last_seen[team] = (g.game_date, venue)
        out[g.game_id] = (values[0][0], values[1][0], values[0][1], values[1][1])
    return out


def absence_cost(out_players: list[tuple[float, float]], replacement: float = -2.0) -> float:
    """Net rating points a team loses when players are out.

    Each player is (value, expected minutes per game). Their minutes go to replacements at
    `replacement` value. Team net ~ sum of value x minutes / 48, so the loss is
    (value - replacement) x minutes / 48, counted only for players better than replacement.
    """
    return sum(max(value - replacement, 0.0) * mpg / 48.0 for value, mpg in out_players)
