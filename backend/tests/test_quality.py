from __future__ import annotations

import copy

from hoops.espn import parse
from hoops.ingest.derive import tag_garbage_time
from hoops.leagues import RULES, League
from hoops.quality.checks import LINEUP_CHECK, check_game

from .fixtures import load


def summary(name="nba_summary_2026.json.gz"):
    return parse.parse_summary(load(name))


def test_clean_games_pass_every_check():
    for name in ("nba_summary_2026.json.gz", "ncaam_summary_2026.json.gz"):
        result = check_game(summary(name))
        assert (result.team_ok, result.player_ok, result.pbp_ok) == (True, True, True)
        assert not [i for i in result.issues if i.name != LINEUP_CHECK]


def test_missing_team_box_fails_only_team_group():
    s = summary()
    s.team_box = []
    result = check_game(s)
    assert not result.team_ok
    assert result.player_ok and result.pbp_ok


def test_missing_player_points_fail_only_player_group():
    s = summary()
    scorer = next(p for p in s.player_box if (p.pts or 0) > 0)
    scorer.pts = 0
    result = check_game(s)
    assert result.team_ok and not result.player_ok and result.pbp_ok


def test_incomplete_play_by_play_fails_only_pbp_group():
    s = summary()
    s.plays = s.plays[:-40]
    result = check_game(s)
    assert result.team_ok and result.player_ok and not result.pbp_ok


def test_backwards_clock_fails_pbp():
    s = summary()
    s.plays[10] = copy.copy(s.plays[10])
    s.plays[10].clock_seconds = s.plays[9].clock_seconds + 30
    assert not check_game(s).pbp_ok


def test_lineup_problems_are_recorded_but_do_not_fail_pbp():
    s = summary()
    sub = next(p for p in s.plays if p.is_substitution)
    sub.participant_espn_ids = ["nobody", "also-nobody"]
    result = check_game(s)
    assert result.pbp_ok
    assert any(i.name == LINEUP_CHECK for i in result.issues)


def test_garbage_time_totals():
    s = summary()
    totals = tag_garbage_time(s, RULES[League.NBA])
    tagged = [p for p in s.plays if p.is_garbage_time]
    assert tagged, "Detroit led by 20+ late, so some plays are garbage time"
    assert all(p.period == 4 and p.clock_seconds <= 6 * 60 for p in tagged)
    detroit = totals["8"]
    assert detroit.points == sum(p.points for p in tagged if p.team_espn_id == "8")
    assert 0 < detroit.possessions < 20


def test_no_garbage_time_in_close_game():
    s = summary()
    for p in s.plays:   # pretend the game was tied throughout
        p.home_score = p.away_score = 0
    totals = tag_garbage_time(s, RULES[League.NBA])
    assert not any(p.is_garbage_time for p in s.plays)
    assert all(t.points == 0 and t.possessions == 0 for t in totals.values())
