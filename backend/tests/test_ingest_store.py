from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hoops.espn import parse
from hoops.ingest.derive import tag_garbage_time
from hoops.ingest.store import Store
from hoops.leagues import RULES, League
from hoops.quality.checks import check_game

from .fixtures import load

pytestmark = pytest.mark.db
FETCHED = datetime(2026, 10, 1, tzinfo=UTC)


def write(db, name="nba_summary_2026.json.gz", league=League.NBA, mutate=None):
    summary = parse.parse_summary(load(name))
    if mutate:
        mutate(summary)
    quality = check_game(summary)
    garbage = tag_garbage_time(summary, RULES[league])
    return Store(db, league).write_summary(summary, quality, garbage, FETCHED), summary


def test_game_round_trip(db):
    game_id, summary = write(db)
    row = db.execute(
        "SELECT season, season_type, status, home_score, away_score, team_quality_ok,"
        " player_quality_ok, pbp_quality_ok FROM games WHERE game_id = %s", (game_id,)
    ).fetchone()
    assert row == (2026, "regular", "final", 100, 118, True, True, True)
    stats = db.execute(
        "SELECT t.abbreviation, s.pts, s.is_home, s.pts_excl_garbage FROM team_game_stats s"
        " JOIN teams t USING (team_id) WHERE game_id = %s ORDER BY t.abbreviation", (game_id,)
    ).fetchall()
    assert [r[:3] for r in stats] == [("CHA", 100, True), ("DET", 118, False)]
    assert all(r[3] is not None and r[3] < r[1] for r in stats)   # garbage time removed
    n_plays, final_home, final_away = db.execute(
        "SELECT count(*), max(home_score), max(away_score) FROM plays WHERE game_id = %s",
        (game_id,),
    ).fetchone()
    assert (n_plays, final_home, final_away) == (len(summary.plays), 100, 118)
    assert db.execute("SELECT count(*) FROM betting_lines WHERE game_id = %s",
                      (game_id,)).fetchone()[0] >= 1


def test_rewriting_a_game_replaces_instead_of_duplicating(db):
    first, _ = write(db)
    counts = [db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("teams", "players", "games", "plays", "player_game_stats")]
    second, _ = write(db)
    assert first == second
    assert counts == [db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("teams", "players", "games", "plays", "player_game_stats")]


def test_failing_team_check_keeps_game_out_of_team_stats(db):
    def drop_box(s):
        s.team_box = []

    game_id, _ = write(db, mutate=drop_box)
    assert db.execute("SELECT team_quality_ok FROM games WHERE game_id = %s",
                      (game_id,)).fetchone()[0] is False
    assert db.execute("SELECT count(*) FROM team_game_stats WHERE game_id = %s",
                      (game_id,)).fetchone()[0] == 0
    assert db.execute("SELECT check_name FROM data_quality_issues WHERE game_id = %s",
                      (game_id,)).fetchall() == [("team_box_missing",)]


def test_failing_pbp_falls_back_to_whole_game(db):
    def truncate(s):
        s.plays = s.plays[:-40]

    game_id, _ = write(db, mutate=truncate)
    rows = db.execute("SELECT pts_excl_garbage, possessions_excl_garbage FROM team_game_stats"
                      " WHERE game_id = %s", (game_id,)).fetchall()
    assert rows and all(r == (None, None) for r in rows)


def test_college_game_and_injuries(db):
    game_id, _ = write(db, name="ncaam_summary_2026.json.gz", league=League.NCAAM)
    assert db.execute("SELECT league FROM games WHERE game_id = %s",
                      (game_id,)).fetchone()[0] == "ncaam"
    store = Store(db, League.NBA)
    injuries = parse.parse_injuries(load("nba_injuries.json.gz"))
    assert store.write_injuries(FETCHED, injuries) == len(injuries)
    assert db.execute("SELECT count(*) FROM injury_snapshots").fetchone()[0] == len(injuries)


def test_player_details_update_even_when_the_player_is_cached(db):
    store = Store(db, League.NBA)
    pid = store.upsert_player("77", "Some Player")
    assert store.upsert_player("77", "Some Player", position="C", headshot_url="h.png") == pid
    assert db.execute("SELECT position, headshot_url FROM players WHERE player_id = %s",
                      (pid,)).fetchone() == ("C", "h.png")
