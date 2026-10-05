from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from hoops.api import admin, deps
from hoops.api.app import app

pytestmark = pytest.mark.db


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("HOOPS_DATABASE_URL", os.environ["HOOPS_TEST_DATABASE_URL"])
    monkeypatch.setenv("HOOPS_ADMIN_PASSWORD", "correct horse")
    monkeypatch.setenv("HOOPS_SECRET_KEY", "test-secret")
    deps._pool = None
    deps.clear_cache()
    admin._failures.clear()
    # The session cookie is Secure, so talk to the app over https.
    with TestClient(app, base_url="https://testserver") as c:
        yield c
    if deps._pool is not None:
        deps._pool.close()
        deps._pool = None


def login(client, password="correct horse"):
    return client.post("/api/admin/login", json={"password": password})


def test_admin_is_off_without_a_password(client, monkeypatch):
    monkeypatch.delenv("HOOPS_ADMIN_PASSWORD")
    assert client.get("/api/admin/me").json() == {"enabled": False, "admin": False}
    assert client.get("/api/admin/health").status_code == 404
    assert login(client).status_code == 404


def test_login_logout_and_protected_views(client, db):
    assert client.get("/api/admin/health").status_code == 401
    assert login(client, "wrong").status_code == 401
    assert login(client).status_code == 200
    assert client.get("/api/admin/me").json() == {"enabled": True, "admin": True}

    db.execute("INSERT INTO job_runs (job_name, league, finished_at, status, error)"
               " VALUES ('game_watcher', 'nba', now(), 'failed', 'EspnError: 503\nmore')")
    health = client.get("/api/admin/health").json()
    watcher = next(j for j in health["jobs"] if j["job_name"] == "game_watcher")
    assert watcher["failures_24h"] == 1 and health["recent"][0]["error"] == "EspnError: 503"
    (team,) = db.execute("INSERT INTO teams (league, espn_id, abbreviation, display_name)"
                         " VALUES ('nba', '1', 'AAA', 'A') RETURNING team_id").fetchone()
    (game,) = db.execute(
        "INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,"
        " away_team_id, status, team_quality_ok, player_quality_ok, pbp_quality_ok)"
        " VALUES ('nba', 'q1', 2026, 'regular', now(), %s, %s, 'final', false, true, true)"
        " RETURNING game_id", (team, team)).fetchone()
    db.execute("INSERT INTO data_quality_issues (game_id, check_group, check_name, detail)"
               " VALUES (%s, 'team', 'team_box_missing', 'no team box')", (game,))
    quality = client.get("/api/admin/quality").json()
    assert quality["threshold"] == 0.02
    assert quality["seasons"][0]["team"] == 1 and quality["seasons"][0]["flagged"] == ["team"]
    assert quality["seasons"][0]["issues"][0]["check"] == "team_box_missing"
    assert quality["flagged_games"][0]["check_name"] == "team_box_missing"
    models = client.get("/api/admin/models").json()
    assert set(models) >= {"versions", "exams", "training_runs", "live_accuracy"}

    client.post("/api/admin/logout")
    assert client.get("/api/admin/health").status_code == 401


def test_failed_logins_are_limited(client):
    for _ in range(admin.MAX_FAILURES):
        assert login(client, "guess").status_code == 401
    assert login(client).status_code == 429          # even the right password, for now


def test_sessions_cannot_be_forged_or_outlive_their_expiry():
    expires = int(time.time()) + 60
    good = admin._sign(expires)
    assert admin._valid(good)
    assert not admin._valid(good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert not admin._valid(f"{expires + 999}.{good.split('.')[1]}")
    assert not admin._valid(admin._sign(int(time.time()) - 1))
    assert not admin._valid("nonsense") and not admin._valid(None)


def test_ncaa_absences_entry(client, db):
    (team,) = db.execute("INSERT INTO teams (league, espn_id, abbreviation, display_name)"
                         " VALUES ('ncaam', '1', 'DUKE', 'Duke') RETURNING team_id").fetchone()
    (player,) = db.execute("INSERT INTO players (league, espn_id, display_name)"
                           " VALUES ('ncaam', '9', 'A Guard') RETURNING player_id").fetchone()
    db.execute("INSERT INTO roster_entries (player_id, team_id, season) VALUES (%s, %s, 2027)",
               (player, team))
    login(client)
    bad = client.post("/api/admin/absences", json={
        "player_id": player, "starts_on": "2026-11-10", "ends_on": "2026-11-01"})
    assert bad.status_code == 400
    created = client.post("/api/admin/absences", json={
        "player_id": player, "starts_on": "2026-11-10", "note": "ankle"}).json()
    rows = client.get("/api/admin/absences").json()["absences"]
    assert rows[0]["team"] == "Duke" and rows[0]["status"] == "Out" and rows[0]["ends_on"] is None
    ended = client.post(f"/api/admin/absences/{created['absence_id']}/end",
                        json={"ends_on": "2026-11-20"})
    assert ended.status_code == 200
    assert client.post("/api/admin/absences", json={"player_id": 999999,
                                                    "starts_on": "2026-11-10"}).status_code == 404
