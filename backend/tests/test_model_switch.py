from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from hoops.api import deps
from hoops.api.app import app
from hoops.leagues import League
from hoops.models.history import rebuild
from hoops.models.live import CHECKPOINT_MIN_GAMES, ModelHooks, load_champion

from .test_live import add_challenger, graded_pairs, league  # noqa: F401  (league: fixture)

pytestmark = pytest.mark.db


def test_public_history_survives_a_model_switch(db, league, monkeypatch):  # noqa: F811
    hooks = ModelHooks(League.NBA)
    old = load_champion(db, League.NBA).model_version_id
    rebuild(db, League.NBA, [2025, 2026])                  # history written by the old champion
    db.execute("INSERT INTO backtest_predictions (model_version_id, game_id, season,"
               " home_win_prob, margin_home, total) SELECT %s, game_id, season, 0.6, 2, 220"
               " FROM games WHERE season = 2025", (old,))
    past_game = db.execute("SELECT game_id FROM games WHERE season = 2025 LIMIT 1").fetchone()[0]
    days = {s: db.execute("SELECT count(*) FROM team_ratings WHERE team_id = %s AND season = %s"
                          " AND model_version_id = %s", (league[1], s, old)).fetchone()[0]
            for s in (2025, 2026)}

    new = add_challenger(db, half_life_days=90.0)
    graded_pairs(db, league, old, new, CHECKPOINT_MIN_GAMES, 0.62, 0.60)
    assert hooks.checkpoint(db)["to"] == new

    monkeypatch.setenv("HOOPS_DATABASE_URL", os.environ["HOOPS_TEST_DATABASE_URL"])
    deps._pool = None
    deps.clear_cache()
    with TestClient(app) as client:
        card = client.get("/api/nba/report-card", params={"season": 2026}).json()
        assert [b["season"] for b in card["backtests"]] == [2025]
        assert card["model_switches"][0]["games"] == CHECKPOINT_MIN_GAMES
        game = client.get(f"/api/nba/games/{past_game}").json()
        assert game["prediction"]["source"] == "backtest"
        team = league[1]
        for season in (2025, 2026):          # old champion's history; new champion's rebuild
            page = client.get(f"/api/nba/teams/{team}", params={"season": season}).json()
            assert len(page["overview"]["trend"]) == days[season] > 0, season
    deps._pool.close()
    deps._pool = None
