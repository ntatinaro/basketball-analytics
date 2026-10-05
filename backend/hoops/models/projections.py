"""Single-game player projections (NBA, from V1.1).

Projected minutes times per-minute rates, then scaled to the game:

- **Minutes:** a recency-weighted average of the player's minutes, starting from last
  season's minutes per game. The players available for a game share the team's 241
  minutes (absent teammates' minutes go to them), with starters losing minutes when a
  blowout is likely and heavy-minute players losing some on back-to-backs.
- **Per-minute rates:** recency-weighted, shrunk toward the player's previous-season rate
  (or the position average for players without one) for small samples.
- **The game:** scoring stats scale with the team's predicted points (pace, opponent
  defense, home court, rest, and absences, all from the game predictor); other counting
  stats scale with the predicted pace. Player totals are pulled toward the team's
  expected totals, which moves the shots of absent teammates to the players who play.
- **Ranges:** the 10th and 90th percentiles, from spreads fitted on past seasons.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

COUNTING = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl",
            "blk", "tov", "pf"]
SCORING = {"pts", "fgm", "fg3m", "ftm", "ast"}       # scale with the team's predicted points
PROJECTED = ["minutes", *COUNTING, "reb"]             # plus/minus is not projected
HEADLINE = ["minutes", "pts", "reb", "ast", "fg3m"]   # shown by default
TEAM_MINUTES = 241.0                                  # 240 plus average overtime
MAX_MINUTES = 44.0
STARTER_MINUTES = 24.0                                # blowouts cut these players' minutes
HEAVY_MINUTES = 30.0                                  # back-to-backs cut these players' minutes
NEW_PLAYER_MPG = 10.0
TEAM_HALF_LIFE = 15.0
TEAM_PRIOR_GAMES = 3.0
MODEL_NAME = "player_projections"


def position_group(position: str | None) -> str:
    p = (position or "").upper()
    if p in ("PG", "SG", "G"):
        return "G"
    if p == "C":
        return "C"
    return "F"


@dataclass(frozen=True)
class ProjectionSettings:
    half_life_games: float = 10.0
    prior_minutes: float = 300.0   # weight of the prior on per-minute rates, in minutes
    prior_games: float = 3.0       # weight of the prior on minutes, in games
    blowout: float = 0.006         # share of starter minutes lost per point of margin over 5
    team_share: float = 0.5        # 0 = add up players as they are, 1 = match team totals
    b2b_minutes: float = 0.97      # minutes multiplier on back-to-backs (30+ mpg players)
    minutes_power: float = 1.0     # how strongly absent teammates' minutes go to regulars
    bench_first: float = 0.0       # 1 = too many available minutes come off the bench first

    def to_json(self) -> dict:
        return asdict(self)

    @property
    def version(self) -> str:
        blob = json.dumps(self.to_json(), sort_keys=True).encode()
        return "p1-" + hashlib.sha1(blob).hexdigest()[:8]

    @property
    def complexity(self) -> int:
        """Number of adjustments in use (ties go to the simpler setting)."""
        return (int(self.blowout > 0) + int(self.team_share > 0) + int(self.b2b_minutes < 1)
                + int(self.minutes_power != 1) + int(self.bench_first > 0))


GRID = {
    "half_life_games": [5.0, 10.0, 20.0],
    "prior_minutes": [100.0, 300.0],
    "prior_games": [1.0, 3.0],
    "blowout": [0.0, 0.006],
    "team_share": [0.0, 0.5, 1.0],
    "b2b_minutes": [1.0, 0.97],
    "minutes_power": [1.0, 1.5, 2.0],
    "bench_first": [0.0, 1.0],
}


def candidates(n: int = 30, seed: int = 11) -> list[ProjectionSettings]:
    """A fixed, reproducible sample of the grid, always including the defaults."""
    keys = list(GRID)
    full = [dict(zip(keys, combo, strict=True))
            for combo in np.array(np.meshgrid(*GRID.values()), dtype=object).T.reshape(
                -1, len(keys))]
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(full))[: max(0, n - 1)]
    pool = [ProjectionSettings()] + [ProjectionSettings(**{k: float(v) for k, v in
                                                           full[i].items()}) for i in order]
    seen, out = set(), []
    for s in pool:
        if s.version not in seen:
            seen.add(s.version)
            out.append(s)
    return out


# -- priors from the previous season ---------------------------------------------------


@dataclass
class SeasonPrior:
    """What is known about players before a season: minutes per game and per-minute
    rates from the previous season, and position averages for everyone else."""

    mpg: dict[int, float] = field(default_factory=dict)
    rates: dict[int, dict[str, float]] = field(default_factory=dict)
    position_rates: dict[str, dict[str, float]] = field(default_factory=dict)
    team_avg: dict[str, float] = field(default_factory=dict)   # league-average team game

    def rate(self, player_id: int, group: str) -> dict[str, float]:
        return self.rates.get(player_id) or self.position_rates.get(group) or \
            self.position_rates.get("F", {})


def season_prior(player_games: pd.DataFrame, games: pd.DataFrame,
                 positions: dict[int, str], shrink_minutes: float = 200.0) -> SeasonPrior:
    """Builds the prior for the next season from one season's games."""
    prior = SeasonPrior()
    if player_games.empty:
        return prior
    pg = player_games.copy()
    pg["group"] = pg["player_id"].map(lambda p: position_group(positions.get(p)))
    by_group = pg.groupby("group")[["minutes", *COUNTING]].sum()
    for g, row in by_group.iterrows():
        prior.position_rates[g] = {s: float(row[s] / row["minutes"]) for s in COUNTING}
    totals = pg.groupby("player_id").agg(games=("game_id", "nunique"), group=("group", "first"),
                                         **{c: (c, "sum") for c in ["minutes", *COUNTING]})
    for pid, row in totals.iterrows():
        prior.mpg[int(pid)] = float(row["minutes"] / row["games"])
        base = prior.position_rates[row["group"]]
        prior.rates[int(pid)] = {
            s: float((row[s] + shrink_minutes * base[s]) / (row["minutes"] + shrink_minutes))
            for s in COUNTING}
    if not games.empty:
        prior.team_avg = {s: float((games[f"h_{s}"].mean() + games[f"a_{s}"].mean()) / 2)
                          for s in COUNTING}
        prior.team_avg["game_total"] = float(games["total"].mean())
    return prior


# -- replaying a season ------------------------------------------------------------------


@dataclass
class _PlayerState:
    weight: float = 0.0         # recency-weighted games
    minutes: float = 0.0        # recency-weighted minutes
    stats: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    games: int = 0              # unweighted, for the naive baseline
    totals: dict[str, float] = field(default_factory=lambda: defaultdict(float))


@dataclass
class _TeamState:
    weight: float = 0.0
    stats: dict[str, float] = field(default_factory=lambda: defaultdict(float))


class History:
    """Recency-weighted player and team histories within one season, updated game by game.
    `inputs()` reads them as they stand before a game."""

    def __init__(self, prior: SeasonPrior, positions: dict[int, str],
                 half_life_games: float) -> None:
        self.prior = prior
        self.positions = positions
        self.decay = 0.5 ** (1.0 / half_life_games)
        self.team_decay = 0.5 ** (1.0 / TEAM_HALF_LIFE)
        self.players: dict[int, _PlayerState] = defaultdict(_PlayerState)
        self.teams: dict[int, _TeamState] = defaultdict(_TeamState)

    def update(self, player_rows: pd.DataFrame, game_rows: pd.DataFrame) -> None:
        for r in player_rows.itertuples(index=False):
            st = self.players[r.player_id]
            st.weight = st.weight * self.decay + 1.0
            st.minutes = st.minutes * self.decay + r.minutes
            st.games += 1
            st.totals["minutes"] += r.minutes
            for s in COUNTING:
                v = getattr(r, s)
                st.stats[s] = st.stats[s] * self.decay + v
                st.totals[s] += v
        for g in game_rows.itertuples(index=False):
            for side, team in (("h", g.home_team_id), ("a", g.away_team_id)):
                st = self.teams[team]
                st.weight = st.weight * self.team_decay + 1.0
                for s in COUNTING:
                    st.stats[s] = st.stats[s] * self.team_decay + getattr(g, f"{side}_{s}")
                st.stats["game_total"] = st.stats["game_total"] * self.team_decay + g.total

    def team_average(self, team_id: int) -> dict[str, float]:
        st = self.teams.get(team_id) or _TeamState()
        avg = self.prior.team_avg
        return {s: (st.stats[s] + TEAM_PRIOR_GAMES * avg.get(s, 0.0))
                / (st.weight + TEAM_PRIOR_GAMES) for s in [*COUNTING, "game_total"]}

    def inputs(self, game_id: int, team_id: int, player_ids: list[int], *,
               team_points: float, game_total: float, margin: float, back_to_back: bool
               ) -> list[dict]:
        """One record per available player, holding everything `project` needs."""
        team = self.team_average(team_id)
        rows = []
        for pid in player_ids:
            st = self.players.get(pid) or _PlayerState()
            group = position_group(self.positions.get(pid))
            prior_rate = self.prior.rate(pid, group)
            row = {
                "game_id": game_id, "team_id": team_id, "player_id": pid,
                "w": st.weight, "m": st.minutes,
                "prior_mpg": self.prior.mpg.get(pid, NEW_PLAYER_MPG),
                "f_pts": team_points / max(team["pts"], 1.0),
                "f_pace": game_total / max(team["game_total"], 1.0),
                "abs_margin": abs(margin), "b2b": float(back_to_back),
                "naive_minutes": (st.totals["minutes"] / st.games if st.games
                                  else self.prior.mpg.get(pid, NEW_PLAYER_MPG)),
            }
            for s in COUNTING:
                row[f"s_{s}"] = st.stats[s]
                row[f"p_{s}"] = prior_rate.get(s, 0.0)
                row[f"t_{s}"] = team[s]
                row[f"naive_{s}"] = (st.totals[s] / st.games if st.games
                                     else prior_rate.get(s, 0.0) * row["naive_minutes"])
            rows.append(row)
        return rows


# -- projecting --------------------------------------------------------------------------


def _scale_minutes(base: pd.Series, keys: list[pd.Series], power: float = 1.0,
                   bench_first: bool = False) -> np.ndarray:
    """Brings each team's minutes to TEAM_MINUTES. Missing minutes (teammates out) go to
    players in proportion to minutes ** power, so with power > 1 the regulars absorb more of
    them. Surplus minutes (more players available than the rotation holds) are taken back in
    proportion to minutes, or, with `bench_first`, in proportion to the minutes a player is
    short of a full game, squared, so the deep bench gives up its minutes first, as coaches
    shorten the rotation. Capped at MAX_MINUTES."""
    m = base.to_numpy(dtype=float).copy()
    for _ in range(8):
        series = pd.Series(m, index=base.index)
        gap = TEAM_MINUTES - series.groupby(keys).transform("sum").to_numpy()
        if bench_first:
            surplus_weight = np.square(MAX_MINUTES - m) * (m > 0)
        else:
            surplus_weight = m * (m < MAX_MINUTES)
        weight = np.where(gap > 0, np.power(m, power) * (m < MAX_MINUTES), surplus_weight)
        total = pd.Series(weight, index=base.index).groupby(keys).transform("sum").to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(total > 0, weight / total, 0.0)
        m = np.clip(m + gap * share, 0.0, MAX_MINUTES)
    return m


def project(inputs: pd.DataFrame, settings: ProjectionSettings) -> pd.DataFrame:
    """Expected stat lines for every row of `inputs` (see `History.inputs`)."""
    if inputs.empty:
        return pd.DataFrame(columns=["game_id", "team_id", "player_id", *PROJECTED])
    keys = [inputs["game_id"], inputs["team_id"]]
    g = settings.prior_games
    base = (inputs["m"] + g * inputs["prior_mpg"]) / (inputs["w"] + g)
    if "play_chance" in inputs:                  # live: who will play is not known yet
        base = base * inputs["play_chance"]
    starter = base >= STARTER_MINUTES
    blowout = 1.0 - settings.blowout * np.clip(inputs["abs_margin"] - 5.0, 0.0, None)
    base = base * np.where(starter, np.clip(blowout, 0.5, 1.0), 1.0)
    heavy = (base >= HEAVY_MINUTES) & (inputs["b2b"] > 0)
    base = base * np.where(heavy, settings.b2b_minutes, 1.0)
    minutes = _scale_minutes(base, keys, settings.minutes_power, settings.bench_first > 0)

    out = inputs[["game_id", "team_id", "player_id"]].copy()
    out["minutes"] = minutes
    k = settings.prior_minutes
    for s in COUNTING:
        rate = (inputs[f"s_{s}"] + k * inputs[f"p_{s}"]) / (inputs["m"] + k)
        raw = pd.Series(minutes * rate.to_numpy(), index=inputs.index)
        factor = inputs["f_pts"] if s in SCORING else inputs["f_pace"]
        team_raw = raw.groupby(keys).transform("sum")
        with np.errstate(divide="ignore", invalid="ignore"):
            pull = np.where(team_raw > 0, inputs[f"t_{s}"] / team_raw, 1.0)
        out[s] = raw * factor * np.power(pull, settings.team_share)
    out["reb"] = out["oreb"] + out["dreb"]
    return out


def naive(inputs: pd.DataFrame) -> pd.DataFrame:
    """Baseline: the player's season-to-date averages (last season's before he plays)."""
    out = inputs[["game_id", "team_id", "player_id"]].copy()
    out["minutes"] = inputs["naive_minutes"]
    for s in COUNTING:
        out[s] = inputs[f"naive_{s}"]
    out["reb"] = out["oreb"] + out["dreb"]
    return out


# -- ranges ------------------------------------------------------------------------------


@dataclass
class Ranges:
    """10th and 90th percentiles as mean -/+ c * mean^b, per stat."""

    params: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {s: list(p) for s, p in self.params.items()}

    @classmethod
    def from_json(cls, data: dict) -> Ranges:
        return cls({s: tuple(p) for s, p in data.items()})

    def bounds(self, stat: str, mean: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c_lo, b_lo, c_hi, b_hi = self.params.get(stat, (0.5, 1.0, 0.5, 1.0))
        mu = np.clip(np.asarray(mean, dtype=float), 0.0, None)
        return np.clip(mu - c_lo * mu ** b_lo, 0.0, None), mu + c_hi * mu ** b_hi


def _pinball(q: float, actual: np.ndarray, pred: np.ndarray) -> float:
    diff = actual - pred
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def _coverage(ranges: Ranges, stat: str, mu: np.ndarray, y: np.ndarray) -> float:
    lo, hi = ranges.bounds(stat, mu)
    return float(np.mean((y >= np.round(lo)) & (y <= np.round(hi))))


def fit_ranges(projected: pd.DataFrame, actual: pd.DataFrame, max_rows: int = 60000,
               seed: int = 3, target: float = 0.8) -> Ranges:
    """Fits the 10th and 90th percentiles per stat, then narrows or widens each range so
    that, shown as whole numbers, it holds `target` of past results (counts are lumpy, so
    percentile fits alone come out too wide once rounded)."""
    joined = projected.merge(actual, on=["game_id", "player_id"], suffixes=("", "_act"))
    if len(joined) > max_rows:
        joined = joined.sample(max_rows, random_state=seed)
    ranges = Ranges()
    for s in PROJECTED:
        mu = np.clip(joined[s].to_numpy(float), 1e-6, None)
        y = joined[f"{s}_act"].to_numpy(float)
        lo = minimize(lambda p, y=y, mu=mu: _pinball(0.1, y, mu - abs(p[0]) * mu ** p[1]),
                      [0.6, 0.7], method="Nelder-Mead").x
        hi = minimize(lambda p, y=y, mu=mu: _pinball(0.9, y, mu + abs(p[0]) * mu ** p[1]),
                      [0.6, 0.7], method="Nelder-Mead").x
        fitted = (abs(float(lo[0])), float(lo[1]), abs(float(hi[0])), float(hi[1]))
        low, high = 0.2, 1.5
        for _ in range(25):                     # bisection on a common width multiplier
            mid = (low + high) / 2
            ranges.params[s] = (fitted[0] * mid, fitted[1], fitted[2] * mid, fitted[3])
            if _coverage(ranges, s, mu, y) > target:
                high = mid
            else:
                low = mid
        ranges.params[s] = (fitted[0] * high, fitted[1], fitted[2] * high, fitted[3])
    return ranges


# -- scoring -----------------------------------------------------------------------------


def actuals(player_games: pd.DataFrame) -> pd.DataFrame:
    out = player_games[["game_id", "player_id", "minutes", *COUNTING]].copy()
    out["reb"] = out["oreb"] + out["dreb"]
    return out


def evaluate(projected: pd.DataFrame, actual: pd.DataFrame, baseline: pd.DataFrame,
             ranges: Ranges | None = None) -> dict:
    """Mean absolute error per stat, the same for the baseline, a single score (average
    of headline errors relative to the baseline; lower is better), and range coverage."""
    a = actual.set_index(["game_id", "player_id"])
    p = projected.set_index(["game_id", "player_id"]).reindex(a.index)
    b = baseline.set_index(["game_id", "player_id"]).reindex(a.index)
    mae = {s: float(np.nanmean(np.abs(p[s] - a[s]))) for s in PROJECTED}
    base = {s: float(np.nanmean(np.abs(b[s] - a[s]))) for s in PROJECTED}
    out = {"players": int(len(a)), "mae": mae, "baseline_mae": base,
           "score": float(np.mean([mae[s] / base[s] if base[s] > 0 else 1.0
                                   for s in HEADLINE]))}
    if ranges is not None:
        cover = {}
        for s in PROJECTED:
            lo, hi = ranges.bounds(s, p[s].to_numpy())
            y = a[s].to_numpy(float)
            cover[s] = float(np.mean((y >= np.round(lo)) & (y <= np.round(hi))))
        out["coverage_80"] = cover
    return out
