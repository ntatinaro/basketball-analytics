"""Model settings: the knobs that rolling exams tune (architecture doc, section 9.2).

Few settings on purpose: with four scored seasons, every extra knob is a chance to fool
ourselves. `complexity` breaks ties in favor of simpler candidates.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import random
from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class ModelSettings:
    half_life_days: float | None = 45.0   # recency weighting; None = all games equal
    prior_games: float = 8.0               # how many games the starting rating counts as
    regress_to_mean: float = 0.33          # last season's rating pulled this far to average
    roster_weight: float = 0.5             # share of the starting rating from current rosters
    luck_discount: float = 0.5             # how much opponent 3P%/FT% luck is removed (0..1)
    remove_garbage_time: bool = True
    absence_weight: float = 1.0            # scale on the "Out" player adjustment
    use_rest_travel: bool = True

    @property
    def complexity(self) -> int:
        """Rough count of active moving parts, for tie-breaking toward simpler methods."""
        return (int(self.half_life_days is not None) + int(self.roster_weight > 0)
                + int(self.luck_discount > 0) + int(self.remove_garbage_time)
                + int(self.absence_weight > 0) + int(self.use_rest_travel))

    def to_json(self) -> dict:
        return asdict(self)

    @property
    def version(self) -> str:
        digest = hashlib.sha1(json.dumps(self.to_json(), sort_keys=True).encode()).hexdigest()
        return f"v1-{digest[:8]}"


DEFAULT = ModelSettings()

GRID = {
    "half_life_days": [None, 60.0, 30.0],
    "prior_games": [4.0, 8.0, 15.0],
    "regress_to_mean": [0.25, 0.4],
    "roster_weight": [0.0, 0.5, 1.0],
    "luck_discount": [0.0, 0.5, 1.0],
    "remove_garbage_time": [False, True],
    "absence_weight": [0.0, 1.0],
    "use_rest_travel": [False, True],
}


def candidates(n: int = 40, seed: int = 7) -> list[ModelSettings]:
    """The default plus a fixed random sample of the grid (the same list every run)."""
    keys = list(GRID)
    combos = [dict(zip(keys, values, strict=True)) for values in itertools.product(*GRID.values())]
    rng = random.Random(seed)
    picked = rng.sample(combos, k=min(n - 1, len(combos)))
    out = [DEFAULT] + [replace(DEFAULT, **c) for c in picked]
    unique: dict[str, ModelSettings] = {}
    for s in out:
        unique.setdefault(s.version, s)
    return list(unique.values())
