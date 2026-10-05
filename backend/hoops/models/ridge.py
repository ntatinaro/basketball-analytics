"""Opponent-adjusted two-way fits (architecture doc, section 9.1).

Each observation is one team's offense against one opponent's defense:

    y = intercept + off[team] + dfn[opponent] + home * home_sign + noise

`off` and `dfn` are pulled toward prior values with a penalty measured in "games": a
prior weight of 8 means the prior counts like 8 games of evidence. The prior fades
naturally as real games accumulate. Observation weights allow recent games to count more.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TwoWayFit:
    intercept: float
    off: np.ndarray        # per team
    dfn: np.ndarray        # per team (added to the opponent's y)
    home: float
    off_se: np.ndarray
    dfn_se: np.ndarray
    net_se: np.ndarray     # standard error of off - dfn, including their covariance
    residual_sd: float


def fit_two_way(
    off_idx: np.ndarray,
    dfn_idx: np.ndarray,
    home_sign: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    n_teams: int,
    prior_off: np.ndarray,
    prior_dfn: np.ndarray,
    prior_weight: float,
    home_prior: float = 0.0,
    home_prior_weight: float = 20.0,
    noise_sd: float | None = None,
) -> TwoWayFit:
    """Weighted ridge regression toward priors, solved in closed form.

    Parameters are [intercept, off_0..off_n-1, dfn_0..dfn_n-1, home]. The intercept is not
    penalized. Standard errors come from noise_sd^2 * inverse(X'WX + penalty), with the noise
    estimated from the weighted residuals unless given.
    """
    n_obs = len(y)
    p = 2 * n_teams + 2
    rows = np.arange(n_obs)
    x = np.zeros((n_obs, p))
    x[:, 0] = 1.0
    x[rows, 1 + off_idx] = 1.0
    x[rows, 1 + n_teams + dfn_idx] = 1.0
    x[:, -1] = home_sign

    penalty = np.zeros(p)
    penalty[1:1 + 2 * n_teams] = prior_weight
    penalty[-1] = home_prior_weight
    target = np.zeros(p)
    target[1:1 + n_teams] = prior_off
    target[1 + n_teams:1 + 2 * n_teams] = prior_dfn
    target[-1] = home_prior
    if n_obs == 0:
        # No games yet: the prior is the answer; the intercept is unknown, so use 0.
        penalty[0] = 1.0

    xw = x * weights[:, None]
    a = x.T @ xw + np.diag(penalty)
    b = xw.T @ y + penalty * target
    beta = np.linalg.solve(a, b)

    if noise_sd is None:
        resid = y - x @ beta
        dof = max(weights.sum() - 1.0, 1.0)
        noise_sd = float(np.sqrt((weights * resid**2).sum() / dof)) if n_obs > 3 else 10.0
    cov = noise_sd**2 * np.linalg.inv(a)
    off_slice = slice(1, 1 + n_teams)
    dfn_slice = slice(1 + n_teams, 1 + 2 * n_teams)
    off_var = np.diag(cov)[off_slice]
    dfn_var = np.diag(cov)[dfn_slice]
    cross = np.array([cov[1 + i, 1 + n_teams + i] for i in range(n_teams)])
    return TwoWayFit(
        intercept=float(beta[0]),
        off=beta[off_slice].copy(),
        dfn=beta[dfn_slice].copy(),
        home=float(beta[-1]),
        off_se=np.sqrt(off_var),
        dfn_se=np.sqrt(dfn_var),
        net_se=np.sqrt(np.maximum(off_var + dfn_var - 2 * cross, 0.0)),
        residual_sd=noise_sd,
    )


def fit_additive_pair(
    a_idx: np.ndarray,
    b_idx: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    n_teams: int,
    prior: np.ndarray,
    prior_weight: float,
) -> tuple[float, np.ndarray]:
    """y = intercept + t[a] + t[b], for symmetric quantities such as pace."""
    n_obs = len(y)
    x = np.zeros((n_obs, n_teams + 1))
    x[:, 0] = 1.0
    rows = np.arange(n_obs)
    x[rows, 1 + a_idx] += 1.0
    x[rows, 1 + b_idx] += 1.0
    penalty = np.full(n_teams + 1, prior_weight)
    penalty[0] = 0.0 if n_obs else 1.0
    target = np.concatenate([[0.0], prior])
    xw = x * weights[:, None]
    beta = np.linalg.solve(x.T @ xw + np.diag(penalty), xw.T @ y + penalty * target)
    return float(beta[0]), beta[1:]
