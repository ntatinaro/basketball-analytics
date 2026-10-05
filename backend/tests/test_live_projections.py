from __future__ import annotations

import psycopg
import pytest

from hoops.leagues import League
from hoops.models.live import ModelHooks

from .test_live import NOW, add_upcoming, league  # noqa: F401  (league is a fixture)

pytestmark = pytest.mark.db
STATS = "pts, fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf"


def add_players(db, team_map, per_team: int = 8) -> dict[int, list[int]]:
    """Gives teams 1 and 2 eight players each, who split every 2026 game evenly."""
    players: dict[int, list[int]] = {}
    for t in (1, 2):
        team = team_map[t]
        players[team] = []
        for i in range(per_team):
            (pid,) = db.execute(
                "INSERT INTO players (league, espn_id, display_name, position)"
                " VALUES ('nba', %s, %s, %s) RETURNING player_id",
                (f"p{t}-{i}", f"Player {t}-{i}", "GFC"[i % 3])).fetchone()
            db.execute("INSERT INTO roster_entries (player_id, team_id, season)"
                       " VALUES (%s, %s, 2026)", (pid, team))
            players[team].append(pid)
    db.execute(f"""
        INSERT INTO player_game_stats (game_id, player_id, team_id, starter, did_not_play,
            minutes, reb, {STATS})
        SELECT t.game_id, r.player_id, t.team_id, true, false, 30, (t.oreb + t.dreb) / 8,
               {", ".join(f"t.{s} / 8" for s in STATS.split(", "))}
        FROM team_game_stats t JOIN games g USING (game_id)
        JOIN roster_entries r ON r.team_id = t.team_id AND r.season = 2026
        WHERE g.season = 2026 AND g.status = 'final'
    """)
    return players


def latest_set(db, game_id, locked=False):
    return db.execute(
        "SELECT projection_set_id, is_locked FROM projection_sets WHERE game_id = %s"
        " AND (%s = false OR is_locked) ORDER BY projection_set_id DESC LIMIT 1",
        (game_id, locked)).fetchone()


def projected(db, set_id) -> dict[int, dict]:
    return {p: s for p, s in db.execute(
        "SELECT player_id, stats FROM player_projections WHERE projection_set_id = %s",
        (set_id,))}


def test_projections_follow_predictions_through_lock_and_grading(db, league):  # noqa: F811
    players = add_players(db, league)
    hooks = ModelHooks(League.NBA)
    game_id = add_upcoming(db, league, 20)

    hooks.refresh(db, now=NOW)
    set_id, locked = latest_set(db, game_id)
    rows = projected(db, set_id)
    assert not locked and len(rows) == 16
    home = [rows[p] for p in players[league[1]]]
    assert sum(r["minutes"][0] for r in home) == pytest.approx(241, abs=3)
    for r in rows.values():
        expected, low, high = r["pts"]
        assert low <= expected <= high
        assert "plus_minus" not in r

    hooks.refresh(db, now=NOW)                        # nothing changed: no new set
    assert latest_set(db, game_id)[0] == set_id

    # An "Out" player drops out and his teammates pick up his minutes.
    star = players[league[1]][0]
    db.execute("INSERT INTO injury_snapshots (snapshot_time, player_id, team_id, status)"
               " VALUES (%s, %s, %s, 'Out')", (NOW, star, league[1]))
    hooks.refresh(db, now=NOW)
    new_id, _ = latest_set(db, game_id)
    after = projected(db, new_id)
    assert new_id != set_id and star not in after
    mate = players[league[1]][1]
    assert after[mate]["minutes"][0] > rows[mate]["minutes"][0]

    hooks.lock(db, game_id, NOW)
    locked_id, is_locked = latest_set(db, game_id, locked=True)
    assert is_locked

    db.execute("UPDATE games SET status = 'final', home_score = 110, away_score = 100"
               " WHERE game_id = %s", (game_id,))
    db.execute(f"""
        INSERT INTO player_game_stats (game_id, player_id, team_id, starter, did_not_play,
            minutes, reb, {STATS})
        SELECT %s, r.player_id, r.team_id, true, false, 34, 5, 14, 5, 11, 1, 4, 3, 4, 1, 4,
               3, 1, 0, 2, 2
        FROM roster_entries r WHERE r.team_id = %s AND r.player_id <> %s
    """, (game_id, league[1], star))
    assert hooks.projector.grade(db, game_id) == 1
    players_graded, metrics = db.execute(
        "SELECT players, metrics FROM projection_grades WHERE projection_set_id = %s",
        (locked_id,)).fetchone()
    assert players_graded == 7
    assert metrics["mae"]["pts"] >= 0 and 0 <= metrics["in_range"]["pts"] <= 1


def test_projections_are_append_only(db, league):  # noqa: F811
    add_players(db, league)
    hooks = ModelHooks(League.NBA)
    game_id = add_upcoming(db, league, 20)
    hooks.refresh(db, now=NOW)
    set_id, _ = latest_set(db, game_id)
    for sql in ("UPDATE projection_sets SET is_locked = true WHERE projection_set_id = %s",
                "DELETE FROM player_projections WHERE projection_set_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException), db.transaction():
            db.execute(sql, (set_id,))


def test_projection_failures_never_block_predictions(db, league, monkeypatch):  # noqa: F811
    hooks = ModelHooks(League.NBA)
    game_id = add_upcoming(db, league, 20)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks.projector, "project_game", broken)
    assert hooks.refresh(db, now=NOW) == 1
    assert db.execute("SELECT count(*) FROM predictions WHERE game_id = %s",
                      (game_id,)).fetchone()[0] == 1
