"""Game predictor (feature 2; architecture doc, section 9.1)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm

from hoops.models.context import GameContext
from hoops.models.ratings import TeamRatings

RANGE_Z = norm.ppf(0.9)    # the shown range is the middle 80% of likely margins


@dataclass(frozen=True)
class Calibration:
    """Fitted on tuning seasons: spread of real results around predictions, and the size
    of the rest and travel effects in points."""

    margin_sd: float = 12.5
    total_sd: float = 18.0
    home_b2b: float = -1.5
    away_b2b: float = -1.5
    travel_per_1000: float = -0.3

    def to_json(self) -> dict:
        return {k: float(v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class Prediction:
    home_win_prob: float
    margin_home: float
    total: float
    margin_low: float
    margin_high: float
    home_points: float
    away_points: float
    base_margin: float           # before rest, travel, and absences
    absence_shift: float         # how much absences moved the margin


def predict(
    ratings: TeamRatings,
    home_team: int,
    away_team: int,
    *,
    neutral: bool = False,
    context: GameContext | None = None,
    calibration: Calibration | None = None,
    use_rest_travel: bool = True,
    absence_weight: float = 1.0,
) -> Prediction:
    ctx = context or GameContext()
    calibration = calibration or Calibration()
    h, a = ratings.index(home_team), ratings.index(away_team)
    home_edge = 0.0 if neutral else ratings.home
    poss = ratings.pace_mean + (ratings.pace[h] + ratings.pace[a]) / 2
    home_eff = ratings.intercept + ratings.off[h] + ratings.dfn[a] + home_edge
    away_eff = ratings.intercept + ratings.off[a] + ratings.dfn[h] - home_edge
    home_pts, away_pts = home_eff * poss / 100, away_eff * poss / 100
    base = home_pts - away_pts

    shift = 0.0
    if use_rest_travel:
        hb, ab, travel = ctx.features()
        shift += (calibration.home_b2b * hb - calibration.away_b2b * ab
                  - calibration.travel_per_1000 * travel)
    absence = absence_weight * (ctx.away_absence - ctx.home_absence) * poss / 100
    margin = base + shift + absence
    # An absence costs a team at both ends (fewer points scored, more allowed), so it moves
    # the margin but leaves the expected total about the same.
    total = home_pts + away_pts

    rating_var = (ratings.net_se[h] ** 2 + ratings.net_se[a] ** 2) * (poss / 100) ** 2
    sd = math.sqrt(calibration.margin_sd**2 + rating_var)
    prob = float(norm.cdf(margin / sd))
    return Prediction(
        home_win_prob=min(max(prob, 0.001), 0.999),
        margin_home=margin,
        total=total,
        margin_low=margin - RANGE_Z * sd,
        margin_high=margin + RANGE_Z * sd,
        home_points=home_pts + (margin - base) / 2,
        away_points=away_pts - (margin - base) / 2,
        base_margin=base,
        absence_shift=absence,
    )
