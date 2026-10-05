"""Walk-forward backtests: every game is predicted using only games before its date.

A season chain runs in order: the warm-up season gives final ratings and player values,
which set the next season's starting ratings, and so on.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg
from scipy.stats import norm

from hoops.leagues import League
from hoops.models import data
from hoops.models.config import ModelSettings
from hoops.models.context import GameContext, absence_cost, schedule_context
from hoops.models.player_values import roster_strength, season_player_values
from hoops.models.predictor import Calibration, predict
from hoops.models.ratings import Priors, TeamRatings, fit_team_ratings, season_priors

log = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")
OPENING_ROSTER_DAYS = 21
ROTATION_GAMES = 10
ROTATION_MIN_MPG = 15.0


@dataclass
class SeasonData:
    season: int
    games: pd.DataFrame                    # finished counted games with box scores
    player_games: pd.DataFrame
    team_ids: list[int]
    abbreviations: dict[int, str]
    context: dict[int, tuple] = field(default_factory=dict)
    absences: dict[int, tuple[list, list]] = field(default_factory=dict)  # game -> (home, away)

    def opening_roster(self) -> pd.DataFrame:
        """Players who played for each team in the season's first weeks."""
        if self.player_games.empty:
            return pd.DataFrame(columns=["team_id", "player_id"])
        start = self.player_games["start_time"].min()
        early = self.player_games[self.player_games["start_time"]
                                  < start + pd.Timedelta(days=OPENING_ROSTER_DAYS)]
        return early[["team_id", "player_id"]].drop_duplicates()


def load_season(conn: psycopg.Connection, league: League, season: int) -> SeasonData:
    games = data.games_frame(conn, league, [season])
    players = data.player_games_frame(conn, league, [season])
    teams = conn.execute("SELECT team_id, abbreviation FROM teams WHERE league = %s",
                         (str(league),)).fetchall()
    abbreviations = {t: a for t, a in teams}
    team_ids = sorted(set(games["home_team_id"]) | set(games["away_team_id"])) if len(games) \
        else sorted(abbreviations)
    sd = SeasonData(season, games, players, team_ids, abbreviations)
    if len(games):
        sd.context = schedule_context(
            games[["game_id", "game_date", "home_team_id", "away_team_id"]], abbreviations)
        sd.absences = rotation_absences(games, players)
    return sd


def rotation_absences(games: pd.DataFrame, players: pd.DataFrame) -> dict[int, tuple[list, list]]:
    """Regular rotation players who did not play, per game and team (backtests only).

    Live predictions use injury reports instead; in backtests the actual availability is
    the closest stand-in, since past injury reports are not available.
    Returns game_id -> (home [(player_id, mpg)], away [(player_id, mpg)]).
    """
    played: dict[tuple[int, int], dict[int, float]] = defaultdict(dict)
    for row in players[["game_id", "team_id", "player_id", "minutes"]].itertuples(index=False):
        played[(row.game_id, row.team_id)][row.player_id] = row.minutes
    history: dict[int, deque] = defaultdict(lambda: deque(maxlen=ROTATION_GAMES))
    out: dict[int, tuple[list, list]] = {}
    for g in games.sort_values("start_time").itertuples(index=False):
        sides = []
        for team in (g.home_team_id, g.away_team_id):
            recent = history[team]
            minutes: dict[int, list[float]] = defaultdict(list)
            for game_minutes in recent:
                for pid, m in game_minutes.items():
                    minutes[pid].append(m)
            today = played.get((g.game_id, team), {})
            missing = [(pid, float(np.mean(m))) for pid, m in minutes.items()
                       if len(m) >= 3 and np.mean(m) >= ROTATION_MIN_MPG and pid not in today]
            sides.append(missing)
            recent.append(today)
        out[g.game_id] = (sides[0], sides[1])
    return out


@dataclass
class SeasonRun:
    """Walk-forward output for one season and one rating configuration."""

    season: int
    predictions: pd.DataFrame
    final: TeamRatings
    values: pd.DataFrame


def walk_forward(sd: SeasonData, priors: Priors, settings: ModelSettings,
                 previous_values: pd.DataFrame) -> pd.DataFrame:
    """Predicts every game of a season from games before its date.

    Stores the pieces needed to re-score with any calibration: base margin, absence
    shift (before weighting), rest/travel features, rating variance, total.
    """
    games = sd.games
    rows = []
    value_of = previous_values.set_index("player_id")["value"].to_dict() if len(
        previous_values) else {}
    for day, day_games in games.groupby("game_date", sort=True):
        cutoff = datetime.combine(day, time(4, 0), EASTERN)    # before any game that day
        past = games[games["start_time"] < cutoff]
        ratings = fit_team_ratings(past, sd.team_ids, priors, settings, cutoff)
        for g in day_games.itertuples(index=False):
            ctx = _context(sd, g.game_id, value_of)
            pred = predict(ratings, g.home_team_id, g.away_team_id, neutral=bool(g.neutral_site),
                           context=ctx, use_rest_travel=False, absence_weight=1.0)
            h, a = ratings.index(g.home_team_id), ratings.index(g.away_team_id)
            poss = ratings.pace_mean + (ratings.pace[h] + ratings.pace[a]) / 2
            hb, ab, travel = ctx.features()
            rows.append({
                "game_id": g.game_id, "season": sd.season, "game_date": day,
                "start_time": g.start_time, "base_margin": pred.base_margin,
                "absence_shift": pred.absence_shift, "home_b2b": hb, "away_b2b": ab,
                "travel": travel,
                "rating_var": (ratings.net_se[h] ** 2 + ratings.net_se[a] ** 2) * (poss / 100) ** 2,
                "total_pred": pred.total, "margin": g.margin, "total": g.total,
                "home_won": g.home_won,
            })
    return pd.DataFrame(rows)


def _context(sd: SeasonData, game_id: int, value_of: dict[int, float]) -> GameContext:
    hr, ar, hm, am = sd.context.get(game_id, (4, 4, 0.0, 0.0))
    home_out, away_out = sd.absences.get(game_id, ([], []))

    def cost(out: list) -> float:
        return absence_cost([(value_of[p], m) for p, m in out if p in value_of])

    return GameContext(home_rest_days=hr, away_rest_days=ar, home_travel_miles=hm,
                       away_travel_miles=am, home_absence=cost(home_out),
                       away_absence=cost(away_out))


def final_ratings(sd: SeasonData, priors: Priors, settings: ModelSettings) -> TeamRatings:
    end = sd.games["start_time"].max() + timedelta(days=1)
    return fit_team_ratings(sd.games, sd.team_ids, priors, settings, end.to_pydatetime())


def run_chain(seasons: list[SeasonData], settings: ModelSettings) -> dict[int, SeasonRun]:
    """Runs the season chain. The first season is the warm-up: no predictions are scored."""
    runs: dict[int, SeasonRun] = {}
    final: TeamRatings | None = None
    values = pd.DataFrame(columns=["player_id", "team_id", "minutes", "games", "mpg", "value"])
    for i, sd in enumerate(seasons):
        roster_net = roster_strength(sd.opening_roster(), values) if len(values) else {}
        priors = season_priors(final, roster_net, settings)
        predictions = walk_forward(sd, priors, settings, values) if i > 0 else pd.DataFrame()
        final = final_ratings(sd, priors, settings)
        team_net = dict(zip(final.team_ids, final.net, strict=True))
        values = season_player_values(sd.player_games, team_net)
        runs[sd.season] = SeasonRun(sd.season, predictions, final, values)
    return runs


# -- calibration and scoring ----------------------------------------------------------


def fit_calibration(preds: pd.DataFrame, settings: ModelSettings) -> Calibration:
    """Rest/travel effects and result spreads, fitted on tuning-season predictions."""
    margin_pred = preds["base_margin"] + settings.absence_weight * preds["absence_shift"]
    resid = preds["margin"] - margin_pred
    home_b2b = away_b2b = travel = 0.0
    if settings.use_rest_travel and len(preds) > 50:
        x = preds[["home_b2b", "away_b2b", "travel"]].to_numpy(dtype=float)
        coef, *_ = np.linalg.lstsq(x, resid.to_numpy(dtype=float), rcond=None)
        # Features enter the margin as +home_b2b, -away_b2b, -travel (see predictor).
        home_b2b, away_b2b, travel = coef[0], -coef[1], -coef[2]
        resid = resid - x @ coef
    rating_var = preds["rating_var"].mean() if len(preds) else 0.0
    margin_sd = float(np.sqrt(max(resid.var() - rating_var, 25.0))) if len(preds) > 1 else 12.5
    total_sd = float((preds["total"] - preds["total_pred"]).std()) if len(preds) > 1 else 18.0
    return Calibration(margin_sd=margin_sd, total_sd=total_sd, home_b2b=float(home_b2b),
                       away_b2b=float(away_b2b), travel_per_1000=float(travel))


def score(preds: pd.DataFrame, settings: ModelSettings, cal: Calibration) -> pd.DataFrame:
    """Applies a calibration to walk-forward pieces: margin, probability, total."""
    out = preds.copy()
    shift = 0.0
    if settings.use_rest_travel:
        shift = (cal.home_b2b * out["home_b2b"] - cal.away_b2b * out["away_b2b"]
                 - cal.travel_per_1000 * out["travel"])
    out["margin_pred"] = out["base_margin"] + shift + settings.absence_weight * out[
        "absence_shift"]
    sd = np.sqrt(cal.margin_sd**2 + out["rating_var"])
    out["prob"] = np.clip(norm.cdf(out["margin_pred"] / sd), 0.001, 0.999)
    return out
