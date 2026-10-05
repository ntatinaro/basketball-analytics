"""Matchup explainer (feature 3): the two or three biggest mismatches, in points.

Each offense skill is paired with the matching defensive skill of the opponent. The gap
from league average is converted to points with sensitivities fitted from game data
(how much scoring per 100 possessions moves with each factor).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from hoops.models.ratings import SubRatings, metric_values

# metric: (offense skill label, defense skill label, higher offense value is better?)
PAIRS = {
    "efg": ("shooting efficiency", "shooting defense", True),
    "tov_pct": ("ball security", "ability to force turnovers", False),
    "oreb_pct": ("offensive rebounding", "defensive rebounding", True),
    "ft_rate": ("ability to get to the line", "ability to avoid fouling", True),
}
DEFAULT_SENSITIVITY = {"efg": 160.0, "tov_pct": -120.0, "oreb_pct": 35.0, "ft_rate": 18.0}


@dataclass(frozen=True)
class Mismatch:
    beneficiary: int            # team ID that gains
    offense_team: int
    defense_team: int
    metric: str
    points: float               # expected points for the offense team (negative = defense wins)
    offense_percentile: float
    defense_percentile: float
    text: str


def fit_sensitivities(obs: pd.DataFrame) -> dict[str, float]:
    """Points per 100 possessions per unit of each factor, by least squares."""
    if len(obs) < 200:
        return dict(DEFAULT_SENSITIVITY)
    x = metric_values(obs)[list(PAIRS)].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(x, obs["eff"].to_numpy(dtype=float), rcond=None)
    return {m: float(c) for m, c in zip(PAIRS, coef[1:], strict=True)}


def _percentiles(values: np.ndarray, higher_better: bool) -> np.ndarray:
    order = values if higher_better else -values
    ranks = pd.Series(order).rank(method="average").to_numpy()
    return 100 * (ranks - 1) / max(len(values) - 1, 1)


def _ordinal(p: float) -> str:
    n = int(round(p))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def explain(sub: SubRatings, home: int, away: int, names: dict[int, str],
            sensitivity: dict[str, float], possessions: float, top: int = 3) -> list[Mismatch]:
    idx = {t: i for i, t in enumerate(sub.team_ids)}
    items: list[Mismatch] = []
    for metric, (off_label, def_label, off_higher) in PAIRS.items():
        off_pct = _percentiles(sub.off[metric], off_higher)
        # A defense is good when it holds the offense's value down (or, for turnovers, up).
        def_pct = _percentiles(sub.dfn[metric], not off_higher)
        for offense, defense in ((home, away), (away, home)):
            o, d = idx[offense], idx[defense]
            deviation = sub.off[metric][o] + sub.dfn[metric][d]
            points = sensitivity[metric] * deviation * possessions / 100
            gains = offense if points > 0 else defense
            if points > 0:
                text = (f"{names[offense]}'s {off_label} ({_ordinal(off_pct[o])} percentile)"
                        f" against {names[defense]}'s {def_label} ({_ordinal(def_pct[d])}):"
                        f" about +{points:.1f} points for {names[offense]}.")
            else:
                text = (f"{names[defense]}'s {def_label} ({_ordinal(def_pct[d])} percentile)"
                        f" against {names[offense]}'s {off_label} ({_ordinal(off_pct[o])}):"
                        f" about {abs(points):.1f} points for {names[defense]}.")
            items.append(Mismatch(gains, offense, defense, metric, points,
                                  float(off_pct[o]), float(def_pct[d]), text))
    items.sort(key=lambda m: abs(m.points), reverse=True)
    return items[:top]
