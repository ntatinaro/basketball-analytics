from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hoops.leagues import League
from hoops.models.live import ModelHooks

from .synthetic import simulate_season

pytestmark = pytest.mark.db
NOW = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)


def insert_season(db, games, team_map, season, *, finished=True) -> list[int]:
    ids = []
    for g in games.itertuples(index=False):
        status = "final" if finished else "scheduled"
        (game_id,) = db.execute(
            """
            INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,
                away_team_id, status, home_score, away_score, team_quality_ok,
                player_quality_ok, pbp_quality_ok)
            VALUES ('nba', %s, %s, 'regular', %s, %s, %s, %s, %s, %s, true, true, true)
            RETURNING game_id
            """,
            (f"{season}-{g.game_id}", season, g.start_time.to_pydatetime(),
             team_map[g.home_team_id], team_map[g.away_team_id], status,
             int(g.h_pts) if finished else None, int(g.a_pts) if finished else None),
        ).fetchone()
        ids.append(game_id)
        if finished:
            for side, team, opp, home in (("h", g.home_team_id, g.away_team_id, True),
                                          ("a", g.away_team_id, g.home_team_id, False)):
                db.execute(
                    """
                    INSERT INTO team_game_stats (game_id, team_id, opponent_id, is_home, pts,
                        fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf,
                        possessions, pts_excl_garbage, possessions_excl_garbage)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s)
                    """,
                    (game_id, team_map[team], team_map[opp], home,
                     *(int(getattr(g, f"{side}_{c}")) for c in
                       ("pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast",
                        "stl", "blk", "tov", "pf")),
                     float(getattr(g, f"{side}_possessions")),
                     int(getattr(g, f"{side}_pts")), float(getattr(g, f"{side}_possessions"))),
                )
    return ids


@pytest.fixture
def league(db):
    team_map = {}
    for t in range(1, 7):
        team_map[t] = db.execute(
            "INSERT INTO teams (league, espn_id, abbreviation, display_name)"
            " VALUES ('nba', %s, %s, %s) RETURNING team_id",
            (str(t), ["BOS", "LAL", "MIA", "DEN", "NY", "PHX"][t - 1], f"Team {t}"),
        ).fetchone()[0]
    prev, *_ = simulate_season(n_teams=6, games_per_pair=16, seed=1, season=2025,
                               start=datetime(2024, 10, 22, tzinfo=UTC))
    insert_season(db, prev, team_map, 2025)
    cur, *_ = simulate_season(n_teams=6, games_per_pair=6, seed=2, season=2026,
                              start=datetime(2025, 12, 1, tzinfo=UTC))
    insert_season(db, cur, team_map, 2026)
    return team_map


def add_upcoming(db, team_map, minutes_from_now: int) -> int:
    return insert_season_game(db, team_map, NOW + timedelta(minutes=minutes_from_now))


def insert_season_game(db, team_map, start) -> int:
    return db.execute(
        "INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,"
        " away_team_id, status) VALUES ('nba', %s, 2026, 'regular', %s, %s, %s, 'scheduled')"
        " RETURNING game_id",
        (f"up-{start.isoformat()}", start, team_map[1], team_map[2]),
    ).fetchone()[0]


def test_refit_stores_ratings_and_sub_ratings(db, league):
    hooks = ModelHooks(League.NBA)
    state = hooks.refit(db, NOW)
    assert len(state.team_ids) == 6
    assert db.execute("SELECT count(*) FROM team_ratings").fetchone()[0] == 6
    assert db.execute("SELECT count(DISTINCT metric) FROM team_sub_ratings").fetchone()[0] == 9
    role = db.execute("SELECT role FROM model_versions").fetchone()[0]
    assert role == "champion"     # default champion registered when no exams have run


def test_predict_lock_grade_cycle(db, league):
    hooks = ModelHooks(League.NBA)
    game_id = add_upcoming(db, league, 20)

    assert hooks.refresh(db, now=NOW) == 1
    assert hooks.refresh(db, now=NOW) == 0          # nothing changed, nothing written

    hooks.lock(db, game_id, NOW)
    locked = db.execute(
        "SELECT home_win_prob, margin_low, margin_high, inputs FROM predictions"
        " WHERE game_id = %s AND is_locked", (game_id,)).fetchone()
    prob, low, high, inputs = locked
    assert 0 < prob < 1 and low < high
    assert len(inputs["explainer"]) == 3
    assert inputs["home"]["offense"] > 90

    db.execute("UPDATE games SET status = 'final', home_score = 110, away_score = 100"
               " WHERE game_id = %s", (game_id,))
    assert hooks.grade(db, game_id) == 1
    won, margin, log_loss = db.execute(
        "SELECT home_won, actual_margin, log_loss FROM prediction_grades").fetchone()
    assert won is True and margin == 10 and log_loss > 0


def test_preseason_games_only_predicted_in_rehearsal(db, league):
    start = NOW + timedelta(hours=2)
    game_id = insert_season_game(db, league, start)
    db.execute("UPDATE games SET season_type = 'pre' WHERE game_id = %s", (game_id,))
    assert ModelHooks(League.NBA).refresh(db, now=NOW) == 0
    assert ModelHooks(League.NBA, rehearsal=True).refresh(db, now=NOW) == 1
    assert db.execute("SELECT is_rehearsal FROM predictions WHERE game_id = %s",
                      (game_id,)).fetchone()[0] is True
