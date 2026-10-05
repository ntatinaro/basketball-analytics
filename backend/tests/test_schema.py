from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from hoops.db.migrate import migrate, migration_files

pytestmark = pytest.mark.db

V1_TABLES = {
    "teams", "team_seasons", "players", "roster_entries", "games", "team_game_stats",
    "player_game_stats", "plays", "injury_snapshots", "betting_lines", "model_versions",
    "training_runs", "exam_results", "player_values", "team_ratings", "team_sub_ratings",
    "predictions", "prediction_grades", "job_runs", "data_quality_issues",
}


def test_migrations_create_tables_and_rerun_cleanly(db):
    tables = {
        r[0] for r in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert tables >= V1_TABLES
    assert migrate(db) == []
    applied = [r[0] for r in db.execute("SELECT name FROM schema_migrations ORDER BY name")]
    assert applied == [name for name, _ in migration_files()]


def _game_and_model(db) -> tuple[int, int]:
    teams = [
        db.execute(
            "INSERT INTO teams (league, espn_id, abbreviation, display_name)"
            " VALUES ('nba', %s, %s, %s) RETURNING team_id",
            (espn_id, abbr, abbr),
        ).fetchone()[0]
        for espn_id, abbr in (("1", "AAA"), ("2", "BBB"))
    ]
    (game_id,) = db.execute(
        "INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,"
        " away_team_id, status) VALUES ('nba', '100', 2027, 'regular', %s, %s, %s, 'scheduled')"
        " RETURNING game_id",
        (datetime(2026, 10, 20, 23, 30, tzinfo=UTC), *teams),
    ).fetchone()
    (model_id,) = db.execute(
        "INSERT INTO model_versions (league, model_name, version) VALUES"
        " ('nba', 'game_predictor', 'v1') RETURNING model_version_id"
    ).fetchone()
    return game_id, model_id


def _predict(db, game_id, model_id, *, locked=False, shadow=False, rehearsal=False) -> int:
    return db.execute(
        "INSERT INTO predictions (game_id, model_version_id, home_win_prob, margin_home, total,"
        " is_locked, is_shadow, is_rehearsal, inputs_hash)"
        " VALUES (%s, %s, 0.6, 3.5, 224, %s, %s, %s, 'h') RETURNING prediction_id",
        (game_id, model_id, locked, shadow, rehearsal),
    ).fetchone()[0]


def test_predictions_are_append_only(db):
    game_id, model_id = _game_and_model(db)
    pid = _predict(db, game_id, model_id)
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute("UPDATE predictions SET home_win_prob = 0.9 WHERE prediction_id = %s", (pid,))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute("DELETE FROM predictions WHERE prediction_id = %s", (pid,))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        db.execute("TRUNCATE predictions CASCADE")
    assert db.execute("SELECT home_win_prob FROM predictions").fetchone()[0] == 0.6


def test_one_public_lock_per_game(db):
    game_id, model_id = _game_and_model(db)
    _predict(db, game_id, model_id, locked=True)
    # Shadow and rehearsal locks don't count as the public lock.
    _predict(db, game_id, model_id, locked=True, shadow=True)
    _predict(db, game_id, model_id, locked=True, rehearsal=True)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _predict(db, game_id, model_id, locked=True)


def test_one_champion_per_model(db):
    db.execute(
        "INSERT INTO model_versions (league, model_name, version, role) VALUES"
        " ('nba', 'team_ratings', 'a', 'champion'), ('nba', 'team_ratings', 'b', 'challenger'),"
        " ('ncaam', 'team_ratings', 'a', 'champion')"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "UPDATE model_versions SET role = 'champion' WHERE league = 'nba' AND version = 'b'"
        )
