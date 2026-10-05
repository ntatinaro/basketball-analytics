"""Accuracy metrics and the betting-market benchmark (architecture doc, section 9.2)."""

from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

EPS = 1e-6
MARKET_SPREAD_SD = 12.5      # converts a point spread to a win probability when no moneyline


def log_loss(prob: np.ndarray, outcome: np.ndarray) -> float:
    p = np.clip(np.asarray(prob, dtype=float), EPS, 1 - EPS)
    y = np.asarray(outcome, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(p) else math.nan


def brier(prob: np.ndarray, outcome: np.ndarray) -> float:
    p, y = np.asarray(prob, dtype=float), np.asarray(outcome, dtype=float)
    return float(np.mean((p - y) ** 2)) if len(p) else math.nan


def accuracy(prob: np.ndarray, outcome: np.ndarray) -> float:
    """Share of games where the favored side won (a 50% pick counts as half right)."""
    p, y = np.asarray(prob, dtype=float), np.asarray(outcome, dtype=float)
    if not len(p):
        return math.nan
    correct = np.where(p == 0.5, 0.5, ((p > 0.5) == (y == 1)).astype(float))
    return float(correct.mean())


def single_game_log_loss(prob: float, home_won: bool) -> float:
    p = min(max(prob, EPS), 1 - EPS)
    return -math.log(p if home_won else 1 - p)


def summary(prob, outcome, margin_pred=None, margin=None, total_pred=None, total=None) -> dict:
    out = {"games": int(len(prob)), "log_loss": log_loss(prob, outcome),
           "brier": brier(prob, outcome), "accuracy": accuracy(prob, outcome)}
    if margin_pred is not None:
        out["margin_mae"] = float(np.mean(np.abs(np.asarray(margin_pred) - np.asarray(margin))))
    if total_pred is not None:
        out["total_mae"] = float(np.mean(np.abs(np.asarray(total_pred) - np.asarray(total))))
    return out


def calibration_bins(prob, outcome, n_bins: int = 10) -> list[dict]:
    """Predicted vs. actual win rate, in bins of the favored side's probability."""
    p, y = np.asarray(prob, dtype=float), np.asarray(outcome, dtype=float)
    fav = np.where(p >= 0.5, p, 1 - p)
    won = np.where(p >= 0.5, y, 1 - y)
    edges = np.linspace(0.5, 1.0, n_bins // 2 + 1)
    out = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        mask = (fav >= lo) & ((fav < hi) | (hi == 1.0))
        if mask.any():
            out.append({"low": float(lo), "high": float(hi), "games": int(mask.sum()),
                        "predicted": float(fav[mask].mean()), "actual": float(won[mask].mean())})
    return out


def implied_probability(moneyline: float) -> float:
    return 100.0 / (moneyline + 100.0) if moneyline > 0 else -moneyline / (-moneyline + 100.0)


def market_home_probability(home_ml, away_ml, spread_home) -> float | None:
    """The market's home win probability with the bookmaker margin removed. Falls back to
    converting the spread when moneylines are missing."""
    if home_ml is not None and away_ml is not None and not (
            _missing(home_ml) or _missing(away_ml)):
        h, a = implied_probability(float(home_ml)), implied_probability(float(away_ml))
        return h / (h + a)
    if spread_home is not None and not _missing(spread_home):
        return float(norm.cdf(-float(spread_home) / MARKET_SPREAD_SD))
    return None


def _missing(x) -> bool:
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True
