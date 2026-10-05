from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from hoops.models.projections import (
    COUNTING,
    MAX_MINUTES,
    TEAM_MINUTES,
    History,
    ProjectionSettings,
    SeasonPrior,
    actuals,
    candidates,
    evaluate,
    fit_ranges,
    naive,
    position_group,
    project,
    season_prior,
)

RATES = {"pts": 0.5, "fgm": 0.2, "fga": 0.42, "fg3m": 0.06, "fg3a": 0.17, "ftm": 0.07,
         "fta": 0.09, "oreb": 0.05, "dreb": 0.15, "ast": 0.11, "stl": 0.03, "blk": 0.02,
         "tov": 0.06, "pf": 0.08}


def player_rows(game_id, team_id, minutes: dict[int, float], scale=1.0):
    return [{"game_id": game_id, "team_id": team_id, "player_id": p, "minutes": m,
             **{s: RATES[s] * m * scale for s in COUNTING}} for p, m in minutes.items()]


def team_game(game_id, home, away, home_rows, away_rows):
    row = {"game_id": game_id, "home_team_id": home, "away_team_id": away}
    for side, rows in (("h", home_rows), ("a", away_rows)):
        for s in COUNTING:
            row[f"{side}_{s}"] = sum(r[s] for r in rows)
    row["total"] = row["h_pts"] + row["a_pts"]
    return row


def history_with_games(n_games=12, half_life=10.0):
    """Team 1 plays a steady 8-man rotation; team 2 mirrors it."""
    rotation = {1: 36.0, 2: 34.0, 3: 32.0, 4: 30.0, 5: 30.0, 6: 30.0, 7: 25.0, 8: 24.0}
    other = {p + 100: m for p, m in rotation.items()}
    prior = SeasonPrior(mpg={**rotation, **other},
                        position_rates={"G": RATES, "F": RATES, "C": RATES},
                        team_avg={**{s: RATES[s] * 241 for s in COUNTING},
                                  "game_total": 2 * RATES["pts"] * 241})
    h = History(prior, {}, half_life)
    for g in range(n_games):
        home, away = player_rows(g, 1, rotation), player_rows(g, 2, other)
        h.update(pd.DataFrame(home + away), pd.DataFrame([team_game(g, 1, 2, home, away)]))
    return h, rotation


def inputs_for(h, players, **kw):
    args = dict(team_points=120.5, game_total=241.0, margin=0.0, back_to_back=False) | kw
    return pd.DataFrame(h.inputs(99, 1, players, **args))


def test_position_groups():
    assert [position_group(p) for p in ("PG", "SG", "G", "SF", "PF", "F", "C", None)] == [
        "G", "G", "G", "F", "F", "F", "C", "F"]


def test_minutes_share_the_team_total_and_absences_move_to_teammates():
    h, rotation = history_with_games()
    full = project(inputs_for(h, list(rotation)), ProjectionSettings(blowout=0.0))
    assert full["minutes"].sum() == pytest.approx(TEAM_MINUTES, abs=0.5)
    assert full.set_index("player_id")["minutes"][1] == pytest.approx(36, abs=1.5)

    without_star = project(inputs_for(h, [p for p in rotation if p != 1]),
                           ProjectionSettings(blowout=0.0))
    assert without_star["minutes"].sum() == pytest.approx(TEAM_MINUTES, abs=0.5)
    assert without_star["minutes"].max() <= MAX_MINUTES
    before = full.set_index("player_id")["pts"]
    after = without_star.set_index("player_id")["pts"]
    assert all(after[p] > before[p] for p in after.index)      # more minutes, more points


def test_regulars_absorb_more_absent_minutes_with_higher_power():
    h, rotation = history_with_games()
    players = [p for p in rotation if p != 1]
    flat = project(inputs_for(h, players), ProjectionSettings(minutes_power=1.0, blowout=0.0))
    steep = project(inputs_for(h, players), ProjectionSettings(minutes_power=2.0, blowout=0.0))
    gain = lambda df: df.set_index("player_id")["minutes"] - pd.Series(rotation)  # noqa: E731
    assert gain(steep)[2] > gain(flat)[2] and gain(steep)[8] < gain(flat)[8]


def test_blowouts_and_back_to_backs_cut_heavy_minutes():
    h, rotation = history_with_games()
    calm = project(inputs_for(h, list(rotation)), ProjectionSettings(blowout=0.01))
    blowout = project(inputs_for(h, list(rotation), margin=20.0),
                      ProjectionSettings(blowout=0.01))
    assert blowout.set_index("player_id")["minutes"][1] < calm.set_index("player_id")[
        "minutes"][1]
    tired = project(inputs_for(h, list(rotation), back_to_back=True),
                    ProjectionSettings(b2b_minutes=0.9, blowout=0.0))
    assert tired.set_index("player_id")["minutes"][1] < calm.set_index("player_id")[
        "minutes"][1]


def test_scoring_follows_the_teams_predicted_points():
    h, rotation = history_with_games()
    s = ProjectionSettings(blowout=0.0, team_share=0.0)
    normal = project(inputs_for(h, list(rotation)), s)
    hot = project(inputs_for(h, list(rotation), team_points=132.55), s)   # +10%
    assert hot["pts"].sum() / normal["pts"].sum() == pytest.approx(1.10, rel=0.01)
    assert hot["dreb"].sum() == pytest.approx(normal["dreb"].sum(), rel=0.01)  # pace unchanged


def test_team_share_pulls_player_totals_to_the_team_total():
    h, rotation = history_with_games()
    inputs = inputs_for(h, list(rotation))
    inputs[[f"s_{s}" for s in COUNTING]] *= 1.3            # players looked 30% better
    loose = project(inputs, ProjectionSettings(team_share=0.0, blowout=0.0))
    tight = project(inputs, ProjectionSettings(team_share=1.0, blowout=0.0))
    team = inputs["t_pts"].iloc[0] * inputs["f_pts"].iloc[0]
    assert tight["pts"].sum() == pytest.approx(team, rel=0.01)
    assert loose["pts"].sum() > tight["pts"].sum()


def test_play_chance_scales_minutes_but_not_rates():
    h, rotation = history_with_games()
    inputs = inputs_for(h, list(rotation))
    inputs["play_chance"] = [1.0] * 7 + [0.2]
    out = project(inputs, ProjectionSettings(blowout=0.0)).set_index("player_id")
    assert out["minutes"][8] < 10
    assert out["pts"][8] / out["minutes"][8] == pytest.approx(RATES["pts"], rel=0.1)


def test_new_players_start_from_position_rates_and_default_minutes():
    h, _ = history_with_games()
    row = inputs_for(h, [555]).iloc[0]
    assert row["w"] == 0 and row["prior_mpg"] == 10.0     # NEW_PLAYER_MPG
    assert row["p_pts"] == RATES["pts"]


def test_season_prior_shrinks_toward_position_rates():
    pg = pd.DataFrame(player_rows(1, 1, {1: 30.0, 2: 5.0}))
    pg.loc[pg["player_id"] == 2, "pts"] = 10.0               # 2 pts/min in 5 minutes
    games = pd.DataFrame([team_game(1, 1, 2, player_rows(1, 1, {1: 30.0}),
                                    player_rows(1, 2, {3: 30.0}))])
    prior = season_prior(pg, games, {1: "G", 2: "G"})
    assert prior.mpg[1] == 30.0
    # 2 points a minute over 5 minutes is pulled almost all the way to the position rate
    assert prior.rates[2]["pts"] == pytest.approx(prior.position_rates["G"]["pts"], abs=0.05)
    assert prior.team_avg["pts"] > 0


def test_ranges_are_calibrated_to_whole_number_coverage():
    rng = np.random.default_rng(0)
    mu = rng.uniform(0.5, 25, 20000)
    proj = pd.DataFrame({"game_id": range(len(mu)), "player_id": 1})
    act = proj.copy()
    for s in ["minutes", *COUNTING, "reb"]:
        proj[s] = mu
        act[s] = rng.poisson(mu)
    ranges = fit_ranges(proj, act)
    result = evaluate(proj, act, proj, ranges)
    assert 0.75 <= result["coverage_80"]["pts"] <= 0.86


def test_evaluate_scores_against_the_baseline():
    h, rotation = history_with_games()
    inputs = inputs_for(h, list(rotation))
    proj = project(inputs, ProjectionSettings(blowout=0.0))
    base = naive(inputs)
    act = actuals(pd.DataFrame(player_rows(99, 1, rotation)))
    result = evaluate(proj, act, base)
    assert result["players"] == 8 and result["score"] > 0


def test_candidates_are_unique_and_include_the_defaults():
    pool = candidates(20)
    assert pool[0] == ProjectionSettings()
    assert len({s.version for s in pool}) == len(pool) == 20
