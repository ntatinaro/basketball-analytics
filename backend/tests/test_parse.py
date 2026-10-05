from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hoops.espn import parse

from .fixtures import load


@pytest.mark.parametrize(("display", "seconds"),
                         [("11:34", 694.0), ("0:00", 0.0), ("45.3", 45.3), (None, 0.0)])
def test_parse_clock(display, seconds):
    assert parse.parse_clock(display) == pytest.approx(seconds)


def test_play_order_comes_from_the_list_not_sequence_numbers():
    raw = load("nba_summary_2026.json.gz")["plays"]
    sequence_numbers = [int(p["sequenceNumber"]) for p in raw]
    assert sequence_numbers != sorted(sequence_numbers)   # ESPN's numbers jump around
    plays = parse.parse_summary(load("nba_summary_2026.json.gz")).plays
    assert [p.sequence for p in plays] == list(range(len(plays)))
    assert (plays[-1].home_score, plays[-1].away_score) == (100, 118)
    assert all(p.x is None or p.x > -1000 for p in plays)


def test_scores_rebuilt_when_espn_running_score_is_stale():
    raw = load("nba_summary_2026_stale_running_score.json.gz")
    espn = raw["plays"]
    assert any(  # ESPN's running score goes backwards at least once in this game
        int(b["homeScore"]) < int(a["homeScore"]) or int(b["awayScore"]) < int(a["awayScore"])
        for a, b in zip(espn, espn[1:], strict=False)
    )
    summary = parse.parse_summary(raw)
    plays = summary.plays
    assert all(a.home_score <= b.home_score and a.away_score <= b.away_score
               for a, b in zip(plays, plays[1:], strict=False))
    assert (plays[-1].home_score, plays[-1].away_score) == (summary.game.home_score,
                                                          summary.game.away_score)


def test_summary_counts_free_throws_scored_as_zero_in_old_data():
    raw = load("nba_summary_2019_ft_zero.json.gz")
    assert any(p.get("scoringPlay") and int(p.get("scoreValue") or 0) == 0
               and "free throw" in p["type"]["text"].lower() for p in raw["plays"])
    summary = parse.parse_summary(raw)
    last = summary.plays[-1]
    assert (last.home_score, last.away_score) == (summary.game.home_score,
                                                  summary.game.away_score)


def test_summary_box_scores_and_lines():
    summary = parse.parse_summary(load("nba_summary_2026.json.gz"))
    detroit = next(b for b in summary.team_box if b.team_espn_id == "8")
    assert (detroit.pts, detroit.fgm, detroit.fga) == (118, 45, 89)
    assert (detroit.fg3m, detroit.ftm) == (9, 19)
    assert detroit.points_from_shots == 118
    assert detroit.possessions == pytest.approx(89 - 16 + 15 + 0.44 * 26)
    for team in summary.team_box:
        players = [p for p in summary.player_box if p.team_espn_id == team.team_espn_id]
        assert sum(p.pts or 0 for p in players) == team.pts
    line = next(x for x in summary.lines if x.provider == "DraftKings")
    assert (line.spread_home, line.total, line.home_moneyline) == (-6.5, 225.5, -218)
    assert summary.espn_win_prob, "ESPN win probability series is present"


def test_college_summary():
    summary = parse.parse_summary(load("ncaam_summary_2026.json.gz"))
    assert summary.game.conference_game is True
    assert max(p.period for p in summary.plays) == 2
    assert any("subbing in" in p.text.lower() for p in summary.plays if p.is_substitution)


def test_scoreboard_season_types():
    play_in = parse.parse_scoreboard(load("nba_scoreboard_playin_20250415.json.gz"))
    assert {g.season_type for g in play_in} == {"play_in"}
    assert play_in[0].status == "final"
    assert play_in[0].start_time.tzinfo == UTC


def test_all_star_teams_are_not_league_teams():
    league = {t.espn_id for t in parse.parse_teams(load("nba_teams.json.gz"))}
    assert len(league) == 30
    all_star = parse.parse_scoreboard(load("nba_scoreboard_allstar_20250216.json.gz"))
    assert all_star and all(g.home.espn_id not in league for g in all_star)


def test_division_one_list_finds_non_division_one_opponents():
    d1 = {t.espn_id for t in parse.parse_teams(load("ncaam_teams_d1.json.gz"))}
    assert 350 <= len(d1) <= 370
    games = parse.parse_scoreboard(load("ncaam_scoreboard_20251110.json.gz"))
    outside = [g for g in games if g.home.espn_id not in d1 or g.away.espn_id not in d1]
    assert 0 < len(outside) < len(games)


def test_injuries_take_player_id_from_link_when_missing():
    injuries = parse.parse_injuries(load("nba_injuries.json.gz"))
    assert injuries and all(i.player_espn_id.isdigit() for i in injuries)
    assert {i.status for i in injuries} <= {"Out", "Day-To-Day", "Questionable", "Doubtful",
                                           "Probable", "Suspension"}


def test_standings_conferences_and_roster():
    conferences = parse.parse_standings_conferences(load("nba_standings_2025.json.gz"))
    assert len(conferences) == 30
    assert {abbr for _, abbr in conferences.values()} == {"East", "West"}
    roster = parse.parse_roster(load("nba_roster_bos.json.gz"))
    assert roster and all(r.height_inches for r in roster)
    assert all(isinstance(r.birth_date, datetime) for r in roster if r.birth_date)


def test_close_lines_from_odds_endpoint_skip_live_odds():
    # Game 401704835 (2024-25): the summary has no odds; the odds endpoint has the close.
    lines = parse.parse_close_lines(load("nba_odds_2025.json.gz"))
    assert len(lines) == 1                       # the in-game "Live Odds" entry is skipped
    line = lines[0]
    assert (line.provider, line.spread_home, line.total) == ("ESPN BET", 3.5, 229.5)
    assert (line.home_moneyline, line.away_moneyline) == (130, -155)
