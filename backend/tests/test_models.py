from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from hoops.evaluation import metrics
from hoops.models.config import DEFAULT, candidates
from hoops.models.context import GameContext, absence_cost, miles_between, schedule_context
from hoops.models.explainer import explain
from hoops.models.player_values import roster_strength, season_player_values
from hoops.models.predictor import Calibration, predict
from hoops.models.ratings import (
    Priors,
    fit_sub_ratings,
    fit_team_ratings,
    luck_adjustment,
    observations,
    season_priors,
)

from .synthetic import simulate_season

END = datetime(2025, 6, 1, tzinfo=UTC)
PLAIN = replace(DEFAULT, half_life_days=None, luck_discount=0.0)


def test_ratings_recover_known_strengths():
    games, teams, off, dfn = simulate_season(games_per_pair=4)
    r = fit_team_ratings(games, teams, Priors(), PLAIN, END)
    assert np.corrcoef(r.off, off)[0, 1] > 0.9
    assert np.corrcoef(r.dfn, dfn)[0, 1] > 0.9
    assert np.corrcoef(r.net, off - dfn)[0, 1] > 0.9
    assert r.home == pytest.approx(1.5, abs=0.6)
    assert r.intercept == pytest.approx(112, abs=1.0)
    # Ranges are honest: most true values sit within two standard errors.
    inside = np.abs(r.net - (off - dfn)) < 2 * r.net_se
    assert inside.mean() > 0.85


def test_no_games_means_ratings_equal_the_prior():
    games, teams, *_ = simulate_season()
    priors = Priors(off={1: 4.0}, dfn={1: -2.0})
    r = fit_team_ratings(games.iloc[0:0], teams, priors, DEFAULT, END)
    assert (r.off[0], r.dfn[0], r.net[0]) == (4.0, -2.0, 6.0)
    assert (r.games_played == 0).all()


def test_prior_fades_as_games_accumulate():
    games, teams, off, dfn = simulate_season(games_per_pair=4)
    wrong = Priors(off={t: 10.0 for t in teams})      # everyone starts "elite"
    early = fit_team_ratings(games.head(30), teams, wrong, PLAIN, END)
    late = fit_team_ratings(games, teams, wrong, PLAIN, END)
    assert np.mean(early.off) > np.mean(late.off)
    assert np.corrcoef(late.off, off)[0, 1] > 0.85


def test_recency_weighting_and_uncertainty_shrink():
    games, teams, *_ = simulate_season()
    as_of = games["start_time"].max().to_pydatetime()
    few = fit_team_ratings(games.head(100), teams, Priors(), DEFAULT, as_of)
    many = fit_team_ratings(games, teams, Priors(), DEFAULT, as_of)
    assert many.net_se.mean() < few.net_se.mean()


def test_luck_adjustment_removes_hot_shooting():
    games, *_ = simulate_season()
    obs = observations(games, DEFAULT)
    hot = obs.copy()
    hot.loc[0, "o_fg3m"] += 6                      # six extra threes in one game
    adjust = luck_adjustment(hot, 1.0) - luck_adjustment(obs, 1.0)
    assert adjust[0] > 10                           # most of the 18 points per ~99 removed
    assert luck_adjustment(obs, 0.0).sum() == 0


def test_garbage_time_toggle_uses_excluded_totals():
    games, *_ = simulate_season()
    games.loc[0, "h_pts_excl_garbage"] = games.loc[0, "h_pts"] - 20
    with_removal = observations(games, DEFAULT)
    without = observations(games, replace(DEFAULT, remove_garbage_time=False))
    assert with_removal.loc[0, "eff"] < without.loc[0, "eff"]


def test_season_priors_regress_and_apply_roster_changes():
    games, teams, *_ = simulate_season()
    final = fit_team_ratings(games, teams, Priors(), PLAIN, END)
    best = int(np.argmax(final.net))
    no_roster = season_priors(final, {}, replace(DEFAULT, roster_weight=0.0,
                                                 regress_to_mean=0.5))
    team = teams[best]
    assert no_roster.off[team] - no_roster.dfn[team] == pytest.approx(0.5 * final.net[best])
    roster = {t: 0.0 for t in teams} | {team: -10.0}   # their stars left
    gutted = season_priors(final, roster, replace(DEFAULT, roster_weight=1.0,
                                                  regress_to_mean=0.5))
    assert gutted.off[team] - gutted.dfn[team] < no_roster.off[team] - no_roster.dfn[team]


def test_predictions_behave_sensibly():
    games, teams, *_ = simulate_season()
    r = fit_team_ratings(games, teams, Priors(), PLAIN, END)
    strong, weak = teams[int(np.argmax(r.net))], teams[int(np.argmin(r.net))]
    p = predict(r, strong, weak)
    assert p.home_win_prob > 0.8 and p.margin_home > 5
    assert p.margin_low < p.margin_home < p.margin_high
    neutral = predict(r, strong, strong, neutral=True)
    assert neutral.home_win_prob == pytest.approx(0.5, abs=1e-6)
    home = predict(r, strong, strong)
    assert home.home_win_prob > 0.5
    hurt = predict(r, strong, weak, context=GameContext(home_absence=6.0))
    assert hurt.margin_home < p.margin_home and hurt.total == pytest.approx(p.total)
    tired = predict(r, strong, weak, context=GameContext(home_rest_days=1),
                    calibration=Calibration(home_b2b=-2.0))
    assert tired.margin_home == pytest.approx(p.margin_home - 2.0)


def test_sub_ratings_and_explainer():
    games, teams, *_ = simulate_season()
    games.loc[games["home_team_id"] == 1, "h_oreb"] = 20   # team 1 crashes the glass at home
    games.loc[games["away_team_id"] == 1, "a_oreb"] = 20
    as_of = games["start_time"].max().to_pydatetime()
    r = fit_team_ratings(games, teams, Priors(), DEFAULT, as_of)
    sub = fit_sub_ratings(games, teams, DEFAULT, as_of)
    table = sub.table(r)
    oreb = table[table.metric == "offensive_rebounding"].set_index("team_id")["percentile"]
    assert oreb[1] == 100.0
    names = {t: f"Team {t}" for t in teams}
    items = explain(sub, 1, 2, names, {"efg": 160, "tov_pct": -120, "oreb_pct": 35,
                                       "ft_rate": 18}, 99)
    rebounding = [m for m in items if m.metric == "oreb_pct"]
    assert rebounding and rebounding[0].beneficiary == 1 and rebounding[0].points > 3
    assert "Team 1's offensive rebounding (100th percentile)" in rebounding[0].text
    assert items == sorted(items, key=lambda m: abs(m.points), reverse=True)


def test_possessive():
    from hoops.models.explainer import possessive

    assert possessive("Detroit Pistons") == "Detroit Pistons'"
    assert possessive("Utah Jazz") == "Utah Jazz's"


def test_player_values_add_up_to_team_strength():
    rows = []
    for team, net in ((1, 6.0), (2, -6.0)):
        for game in range(1, 41):
            for k, (minutes, pts) in enumerate(((36, 30), (34, 18), (30, 12), (28, 10),
                                                (26, 8), (20, 6), (18, 5), (14, 4))):
                bonus = 2 if net > 0 else 0
                rows.append({"game_id": game * 10 + team, "season": 2025,
                             "player_id": team * 100 + k, "team_id": team,
                             "minutes": minutes, "pts": pts + bonus,
                             "fgm": pts // 2, "fga": pts, "fg3m": 1, "fg3a": 3, "ftm": 2, "fta": 3,
                             "oreb": 1, "dreb": 4, "ast": 3, "stl": 1, "blk": 0, "tov": 2,
                             "pf": 2, "team_possessions": 100.0,
                             "start_time": pd.Timestamp("2025-01-01", tz="UTC")})
    values = season_player_values(pd.DataFrame(rows), {1: 6.0, 2: -6.0})
    by_team = values.groupby("team_id").apply(lambda v: (v["value"] * v["mpg"] / 48).sum(),
                                              include_groups=False)
    assert by_team[1] > by_team[2]
    star, bench = values.set_index("player_id").loc[[100, 107], "value"]
    assert star > bench
    roster = pd.DataFrame({"team_id": [1] * 8 + [2] * 8,
                           "player_id": [100 + k for k in range(8)] + [200 + k for k in range(8)]})
    strength = roster_strength(roster, values)
    assert strength[1] > strength[2]


def test_context_rest_travel_and_absences():
    sched = pd.DataFrame({
        "game_id": [1, 2, 3],
        "game_date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-05"]).date,
        "home_team_id": [1, 2, 1], "away_team_id": [2, 1, 2],
    })
    ctx = schedule_context(sched, {1: "BOS", 2: "LAL"})
    assert ctx[2][:2] == (1, 1)          # both teams on a back-to-back
    assert ctx[2][3] == pytest.approx(miles_between((42.366, -71.062), (34.043, -118.267)))
    assert ctx[3][:2] == (3, 3)
    assert absence_cost([(6.0, 36.0)]) == pytest.approx(8.0 * 36 / 48)
    assert absence_cost([(-4.0, 20.0)]) == 0.0


def test_metrics_and_market():
    assert metrics.log_loss([0.5, 0.5], [1, 0]) == pytest.approx(np.log(2))
    assert metrics.accuracy([0.7, 0.4, 0.5], [1, 1, 0]) == pytest.approx(1.5 / 3)
    p = metrics.market_home_probability(-218, 180, -6.5)
    assert 0.65 < p < 0.68
    assert metrics.market_home_probability(None, None, -6.5) > 0.5
    assert metrics.market_home_probability(None, None, None) is None
    bins = metrics.calibration_bins([0.8, 0.8, 0.2], [1, 0, 0])
    assert bins[-1]["games"] == 3 or sum(b["games"] for b in bins) == 3


def test_candidates_are_stable_and_unique():
    a, b = candidates(20), candidates(20)
    assert [s.version for s in a] == [s.version for s in b]
    assert len({s.version for s in a}) == len(a)
    assert a[0] == DEFAULT
