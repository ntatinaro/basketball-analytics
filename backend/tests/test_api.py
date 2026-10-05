from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hoops.api import deps
from hoops.api.app import app
from hoops.db.refresh import refresh_screen_tables
from hoops.espn import parse
from hoops.ingest.derive import tag_garbage_time
from hoops.ingest.store import Store
from hoops.leagues import RULES, League
from hoops.models.live import ModelHooks
from hoops.quality.checks import check_game

from .fixtures import load

pytestmark = pytest.mark.db


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("HOOPS_DATABASE_URL", os.environ["HOOPS_TEST_DATABASE_URL"])
    deps._pool = None
    deps.clear_cache()
    store = Store(db, League.NBA)
    for team in parse.parse_teams(load("nba_teams.json.gz")):
        store.upsert_team(team)
    store.set_conferences(2026, parse.parse_standings_conferences(load(
        "nba_standings_2025.json.gz")))
    for name in ("nba_summary_2026.json.gz", "nba_summary_2026_stale_running_score.json.gz"):
        summary = parse.parse_summary(load(name))
        store.write_summary(summary, check_game(summary),
                            tag_garbage_time(summary, RULES[League.NBA]),
                            datetime(2026, 4, 11, tzinfo=UTC))
    refresh_screen_tables(db)
    ModelHooks(League.NBA).refit(db, datetime(2026, 4, 12, tzinfo=UTC))
    with TestClient(app) as c:
        yield c
    if deps._pool is not None:
        deps._pool.close()
        deps._pool = None


def test_meta_and_unknown_league(client):
    meta = client.get("/api/nba/meta").json()
    assert meta["current_season"] == 2026 and 2026 in meta["seasons"]
    assert "Eastern Conference" in meta["conferences"]
    assert meta["delayed"] is True          # no worker has run in this test database
    assert client.get("/api/wnba/meta").status_code == 404


def test_games_by_date_and_detail(client):
    day = client.get("/api/nba/games", params={"date": "2026-04-10"}).json()
    detroit_game = next(g for g in day["games"] if g["away"]["abbreviation"] == "DET")
    assert (detroit_game["home_score"], detroit_game["away_score"]) == (100, 118)
    assert detroit_game["home"]["conference"] == "Eastern Conference"
    detail = client.get(f"/api/nba/games/{detroit_game['id']}").json()
    assert detail["default_tab"] == "box"
    players = detail["box_score"]["away"]["players"]
    assert sum(p["pts"] or 0 for p in players) == 118
    # Late-night games belong to the viewer's local date.
    pacific = client.get("/api/nba/games", params={"date": "2026-04-10",
                                                  "tz": "America/Los_Angeles"}).json()
    assert len(pacific["games"]) >= 1
    assert client.get("/api/nba/games", params={"date": "2026-04-10",
                                               "tz": "Mars/Base"}).status_code == 400
    assert client.get("/api/nba/games/999999").status_code == 404


def test_teams_table_and_team_page(client):
    teams = client.get("/api/nba/teams").json()["teams"]
    assert len(teams) == 30
    assert [t["rank"] for t in teams if t["rank"]] == sorted(t["rank"] for t in teams if t["rank"])
    detroit = next(t for t in teams if t["abbreviation"] == "DET")
    page = client.get(f"/api/nba/teams/{detroit['id']}").json()
    assert page["team"]["wins"] == 1
    assert len(page["switcher"]) == 30
    assert {s["metric"] for s in page["overview"]["sub_ratings"]} >= {"pace",
                                                                       "shooting_efficiency"}
    assert page["roster"] and all(r["team"] == "DET" for r in page["roster"])
    assert page["schedule"][0]["result"]["won"] is True


def test_players_scopes_and_qualification(client):
    league = client.get("/api/nba/players").json()["players"]
    assert league and all("qualified" in p for p in league)
    team_id = league[0]["team_id"]
    team = client.get("/api/nba/players", params={"scope": "team", "team": team_id}).json()
    assert team["players"] and all(p["team_id"] == team_id for p in team["players"])
    assert client.get("/api/nba/players", params={"scope": "team"}).status_code == 400
    conf = client.get("/api/nba/players", params={"scope": "conference",
                                                  "conf": "Eastern Conference"}).json()
    assert 0 < len(conf["players"]) <= len(league)


def test_player_page_and_search(client):
    found = client.get("/api/nba/search/players", params={"q": "cade cuningham"}).json()
    assert found["results"][0]["name"] == "Cade Cunningham"
    page = client.get(f"/api/nba/players/{found['results'][0]['id']}").json()
    assert page["player"]["team"] == "DET"
    assert len(page["game_log"]) == 1 and page["game_log"][0]["result"].startswith("W")
    teams = client.get("/api/nba/search/teams", params={"q": "pistons"}).json()["results"]
    assert teams[0]["abbreviation"] == "DET"
    assert client.get("/api/nba/search/teams", params={"q": "DET"}).json()["results"][0][
        "abbreviation"] == "DET"


def test_predictions_lock_and_cache_invalidation(client, db):
    teams = {r[0]: r[1] for r in db.execute("SELECT abbreviation, team_id FROM teams")}
    start = datetime.now(UTC) + timedelta(hours=2)
    game_id = db.execute(
        "INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,"
        " away_team_id, status) VALUES ('nba', 'x1', 2026, 'regular', %s, %s, %s, 'scheduled')"
        " RETURNING game_id", (start, teams["DET"], teams["CHA"])).fetchone()[0]
    before = client.get(f"/api/nba/games/{game_id}").json()
    assert before["prediction"] is None and before["default_tab"] == "preview"

    hooks = ModelHooks(League.NBA)
    hooks.lock(db, game_id, datetime.now(UTC))
    after = client.get(f"/api/nba/games/{game_id}").json()     # cache sees the new data
    assert after["prediction"]["locked"] is True
    assert len(after["preview"]["explainer"]) == 3
    assert 0 < after["prediction"]["home_win_prob"] < 1


def test_screen_table_refresh_reaches_the_api(client, db):
    before = len(client.get("/api/nba/players").json()["players"])
    db.execute("DELETE FROM player_game_stats")
    refresh_screen_tables(db)
    assert before > 0
    assert client.get("/api/nba/players").json()["players"] == []


def test_report_card_shape(client):
    card = client.get("/api/nba/report-card").json()
    assert card["season"] == 2026
    assert card["live"]["games"] == 0 and card["backtests"] == []


def test_data_delayed_note_follows_worker_health(db):
    from hoops.api.deps import data_freshness

    now = datetime(2026, 4, 10, 23, 30, tzinfo=UTC)
    teams = [r[0] for r in db.execute("SELECT team_id FROM teams LIMIT 2")] or []
    if len(teams) < 2:
        for i in (1, 2):
            teams.append(db.execute(
                "INSERT INTO teams (league, espn_id, abbreviation, display_name)"
                " VALUES ('nba', %s, %s, %s) RETURNING team_id", (f"t{i}", f"T{i}", f"Team {i}"),
            ).fetchone()[0])
    db.execute(
        "INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,"
        " away_team_id, status) VALUES ('nba', 'live-1', 2026, 'regular', %s, %s, %s, 'live')",
        (now - timedelta(minutes=20), teams[0], teams[1]),
    )

    def watcher_run(minutes_ago: int, status: str) -> None:
        finished = now - timedelta(minutes=minutes_ago)
        db.execute("INSERT INTO job_runs (job_name, league, started_at, finished_at, status)"
                   " VALUES ('game_watcher', 'nba', %s, %s, %s)", (finished, finished, status))

    import psycopg
    from psycopg.rows import dict_row

    api_conn = psycopg.connect(os.environ["HOOPS_TEST_DATABASE_URL"], autocommit=True,
                               row_factory=dict_row)
    # ESPN outage: the watcher has been failing during a live game.
    watcher_run(30, "succeeded")
    for m in (5, 4, 3, 2, 1):
        watcher_run(m, "failed")
    assert data_freshness(api_conn, League.NBA, now)["delayed"] is True
    # Recovery: one successful run clears the note.
    watcher_run(0, "succeeded")
    assert data_freshness(api_conn, League.NBA, now)["delayed"] is False
    api_conn.close()
