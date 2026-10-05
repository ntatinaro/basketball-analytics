"""Team ratings and sub-ratings (feature 1; architecture doc, section 9.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from hoops.models.config import ModelSettings
from hoops.models.ridge import fit_additive_pair, fit_two_way

THREE_SHRINK_ATTEMPTS = 300.0
FT_SHRINK_ATTEMPTS = 100.0
HOME_PRIOR_WEIGHT = 20.0
SUB_PRIOR_GAMES = 10.0


@dataclass
class Priors:
    """Starting values per team, relative to the league average (0 = average)."""

    off: dict[int, float] = field(default_factory=dict)
    dfn: dict[int, float] = field(default_factory=dict)      # negative = good defense
    pace: dict[int, float] = field(default_factory=dict)
    home: float = 1.2                                          # per-100 home edge, each side
    pace_mean: float = 99.0
    intercept: float = 114.0


@dataclass
class TeamRatings:
    as_of: datetime
    team_ids: list[int]
    intercept: float               # league average points per 100 possessions
    off: np.ndarray                # offense vs. average (+ = better)
    dfn: np.ndarray                # points allowed vs. average (- = better)
    off_se: np.ndarray
    dfn_se: np.ndarray
    net_se: np.ndarray
    home: float                    # per-100 home edge for each side
    pace_mean: float
    pace: np.ndarray
    games_played: np.ndarray
    residual_sd: float

    def index(self, team_id: int) -> int:
        return self.team_ids.index(team_id)

    @property
    def net(self) -> np.ndarray:
        return self.off - self.dfn

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "team_id": self.team_ids,
            "overall": self.net,
            "offense": self.intercept + self.off,
            "defense": self.intercept + self.dfn,
            "overall_se": self.net_se,
            "offense_se": self.off_se,
            "defense_se": self.dfn_se,
            "pace": self.pace_mean + self.pace,
            "games_played": self.games_played,
        })


# -- observations ---------------------------------------------------------------------


def observations(games: pd.DataFrame, settings: ModelSettings) -> pd.DataFrame:
    """Two rows per game: each team's offense against the other's defense."""
    rows = []
    for us, them, sign in (("h", "a", 1.0), ("a", "h", -1.0)):
        side = pd.DataFrame({
            "game_id": games["game_id"].to_numpy(),
            "game_date": games["game_date"].to_numpy(),
            "start_time": games["start_time"].to_numpy(),
            "team": games["home_team_id" if us == "h" else "away_team_id"].to_numpy(),
            "opp": games["home_team_id" if them == "h" else "away_team_id"].to_numpy(),
            "home_sign": sign * games["home_sign"].to_numpy(),
            "poss": games["poss"].to_numpy(),
            "pace": games["pace"].to_numpy(),
        })
        for c in ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "tov", "stl",
                  "pts_excl_garbage", "possessions_excl_garbage"):
            side[f"o_{c}"] = games[f"{us}_{c}"].to_numpy()
        for c in ("dreb", "stl", "possessions_excl_garbage"):
            side[f"d_{c}"] = games[f"{them}_{c}"].to_numpy()
        rows.append(side)
    obs = pd.concat(rows, ignore_index=True)

    pts, poss = obs["o_pts"].astype(float), obs["poss"].astype(float)
    if settings.remove_garbage_time:
        excl_poss = (obs["o_possessions_excl_garbage"] + obs["d_possessions_excl_garbage"]) / 2
        usable = obs["o_pts_excl_garbage"].notna() & excl_poss.notna() & (excl_poss > 20)
        pts = pts.where(~usable, obs["o_pts_excl_garbage"])
        poss = poss.where(~usable, excl_poss)
    obs["eff"] = 100 * pts / poss.clip(lower=1.0)
    return obs


def luck_adjustment(obs: pd.DataFrame, discount: float) -> np.ndarray:
    """Points per 100 to remove for shooting luck: 3s and free throws made above or below
    the offense's own expected percentage (its season rate, shrunk toward the league)."""
    if discount <= 0 or obs.empty:
        return np.zeros(len(obs))
    league3 = obs["o_fg3m"].sum() / max(obs["o_fg3a"].sum(), 1)
    league_ft = obs["o_ftm"].sum() / max(obs["o_fta"].sum(), 1)
    team = obs.groupby("team")[["o_fg3m", "o_fg3a", "o_ftm", "o_fta"]].sum()
    p3 = ((team["o_fg3m"] + league3 * THREE_SHRINK_ATTEMPTS)
          / (team["o_fg3a"] + THREE_SHRINK_ATTEMPTS))
    pft = (team["o_ftm"] + league_ft * FT_SHRINK_ATTEMPTS) / (team["o_fta"] + FT_SHRINK_ATTEMPTS)
    expected3 = obs["o_fg3a"] * obs["team"].map(p3)
    expected_ft = obs["o_fta"] * obs["team"].map(pft)
    extra = 3 * (obs["o_fg3m"] - expected3) + (obs["o_ftm"] - expected_ft)
    return (discount * 100 * extra / obs["poss"].clip(lower=1.0)).to_numpy()


def recency_weights(obs: pd.DataFrame, as_of: datetime, half_life: float | None) -> np.ndarray:
    if half_life is None or obs.empty:
        return np.ones(len(obs))
    age = (pd.Timestamp(as_of) - pd.to_datetime(obs["start_time"], utc=True)).dt.total_seconds()
    days = np.clip(age.to_numpy() / 86400.0, 0, None)
    return 0.5 ** (days / half_life)


# -- fitting --------------------------------------------------------------------------


def fit_team_ratings(
    games: pd.DataFrame,
    team_ids: list[int],
    priors: Priors,
    settings: ModelSettings,
    as_of: datetime,
) -> TeamRatings:
    """Ratings from a season's games that finished before `as_of`."""
    index = {t: i for i, t in enumerate(team_ids)}
    n = len(team_ids)
    prior_off = np.array([priors.off.get(t, 0.0) for t in team_ids])
    prior_dfn = np.array([priors.dfn.get(t, 0.0) for t in team_ids])
    prior_pace = np.array([priors.pace.get(t, 0.0) for t in team_ids])

    if games.empty:
        return TeamRatings(
            as_of=as_of, team_ids=team_ids, intercept=priors.intercept, off=prior_off,
            dfn=prior_dfn, off_se=np.full(n, 3.0), dfn_se=np.full(n, 3.0),
            net_se=np.full(n, 4.0), home=priors.home, pace_mean=priors.pace_mean,
            pace=prior_pace, games_played=np.zeros(n, dtype=int), residual_sd=12.0,
        )

    obs = observations(games, settings)
    y = obs["eff"].to_numpy() - luck_adjustment(obs, settings.luck_discount)
    weights = recency_weights(obs, as_of, settings.half_life_days)
    off_idx = obs["team"].map(index).to_numpy()
    dfn_idx = obs["opp"].map(index).to_numpy()
    fit = fit_two_way(off_idx, dfn_idx, obs["home_sign"].to_numpy(), y, weights, n,
                      prior_off, prior_dfn, settings.prior_games, priors.home,
                      HOME_PRIOR_WEIGHT)

    game_weights = recency_weights(games, as_of, settings.half_life_days)
    pace_mean, pace = fit_additive_pair(
        games["home_team_id"].map(index).to_numpy(), games["away_team_id"].map(index).to_numpy(),
        games["pace"].to_numpy(), game_weights, n, prior_pace / 2, settings.prior_games,
    )
    played = np.bincount(off_idx, minlength=n)
    return TeamRatings(
        as_of=as_of, team_ids=team_ids, intercept=fit.intercept, off=fit.off, dfn=fit.dfn,
        off_se=fit.off_se, dfn_se=fit.dfn_se, net_se=fit.net_se, home=fit.home,
        pace_mean=pace_mean, pace=2 * pace, games_played=played,
        residual_sd=fit.residual_sd,
    )


def season_priors(
    final: TeamRatings | None,
    roster_net: dict[int, float],
    settings: ModelSettings,
) -> Priors:
    """Starting values for a new season from last season's final ratings and (optionally)
    current rosters. Roster changes move offense and defense equally."""
    if final is None:
        return Priors()
    keep = 1.0 - settings.regress_to_mean
    priors = Priors(home=final.home, pace_mean=final.pace_mean, intercept=final.intercept)
    roster_mean = np.mean(list(roster_net.values())) if roster_net else 0.0
    for i, team in enumerate(final.team_ids):
        off, dfn = keep * final.off[i], keep * final.dfn[i]
        if settings.roster_weight > 0 and team in roster_net:
            net = off - dfn
            target = (1 - settings.roster_weight) * net + settings.roster_weight * (
                keep * (roster_net[team] - roster_mean))
            shift = (target - net) / 2
            off, dfn = off + shift, dfn - shift
        priors.off[team], priors.dfn[team] = off, dfn
        priors.pace[team] = 0.5 * final.pace[i]
    return priors


# -- sub-ratings ----------------------------------------------------------------------

# name: (label, which side is the team's skill, higher is better?)
SUB_RATINGS = {
    "shooting_efficiency": ("Shooting efficiency", "off", True),
    "three_point_shooting": ("Three-point shooting", "off", True),
    "ball_security": ("Ball security", "off", False),
    "forcing_turnovers": ("Forcing turnovers", "dfn", True),
    "offensive_rebounding": ("Offensive rebounding", "off", True),
    "defensive_rebounding": ("Defensive rebounding", "dfn", False),
    "getting_to_the_line": ("Getting to the line", "off", True),
    "interior_defense": ("Interior defense", "dfn", False),
    "pace": ("Pace", "pace", True),
}

# The observation metric behind each sub-rating.
_METRIC_OF = {
    "shooting_efficiency": "efg", "three_point_shooting": "three_pct", "ball_security": "tov_pct",
    "forcing_turnovers": "tov_pct", "offensive_rebounding": "oreb_pct",
    "defensive_rebounding": "oreb_pct", "getting_to_the_line": "ft_rate",
    "interior_defense": "two_pct",
}


def metric_values(obs: pd.DataFrame) -> pd.DataFrame:
    fga = obs["o_fga"].clip(lower=1)
    two_att = (obs["o_fga"] - obs["o_fg3a"]).clip(lower=1)
    return pd.DataFrame({
        "efg": (obs["o_fgm"] + 0.5 * obs["o_fg3m"]) / fga,
        "three_pct": obs["o_fg3m"] / obs["o_fg3a"].clip(lower=1),
        "tov_pct": obs["o_tov"] / obs["poss"].clip(lower=1),
        "oreb_pct": obs["o_oreb"] / (obs["o_oreb"] + obs["d_dreb"]).clip(lower=1),
        "ft_rate": obs["o_fta"] / fga,
        "two_pct": (obs["o_fgm"] - obs["o_fg3m"]) / two_att,
    })


@dataclass
class SubRatings:
    team_ids: list[int]
    league: dict[str, float]                       # league average per metric
    off: dict[str, np.ndarray]                     # per metric, offense deviation
    dfn: dict[str, np.ndarray]                     # per metric, allowed deviation

    def value(self, name: str, ratings: TeamRatings) -> np.ndarray:
        """The team's rate for a sub-rating (e.g. eFG% 0.55), or pace."""
        if name == "pace":
            return ratings.pace_mean + ratings.pace
        metric = _METRIC_OF[name]
        side = SUB_RATINGS[name][1]
        dev = self.off[metric] if side == "off" else self.dfn[metric]
        return self.league[metric] + dev

    def table(self, ratings: TeamRatings) -> pd.DataFrame:
        rows = []
        for name, (_, _, higher_better) in SUB_RATINGS.items():
            values = self.value(name, ratings)
            order = values if higher_better else -values
            pct = 100 * (pd.Series(order).rank(method="average") - 1) / max(len(order) - 1, 1)
            for team, v, p in zip(self.team_ids, values, pct, strict=True):
                rows.append({"team_id": team, "metric": name, "value": float(v),
                             "percentile": float(p)})
        return pd.DataFrame(rows)


def fit_sub_ratings(games: pd.DataFrame, team_ids: list[int], settings: ModelSettings,
                    as_of: datetime) -> SubRatings:
    index = {t: i for i, t in enumerate(team_ids)}
    n = len(team_ids)
    zero = np.zeros(n)
    metrics = sorted(set(_METRIC_OF.values()))
    if games.empty:
        return SubRatings(team_ids, {m: 0.0 for m in metrics},
                          {m: zero.copy() for m in metrics}, {m: zero.copy() for m in metrics})
    obs = observations(games, settings)
    values = metric_values(obs)
    weights = recency_weights(obs, as_of, settings.half_life_days)
    off_idx = obs["team"].map(index).to_numpy()
    dfn_idx = obs["opp"].map(index).to_numpy()
    league, off, dfn = {}, {}, {}
    for m in metrics:
        fit = fit_two_way(off_idx, dfn_idx, obs["home_sign"].to_numpy(), values[m].to_numpy(),
                          weights, n, zero, zero, SUB_PRIOR_GAMES, 0.0, HOME_PRIOR_WEIGHT)
        league[m], off[m], dfn[m] = fit.intercept, fit.off, fit.dfn
    return SubRatings(team_ids, league, off, dfn)
