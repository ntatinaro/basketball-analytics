from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from hoops.espn.client import EspnClient
from hoops.espn.raw_store import RawStore
from hoops.ingest.load import Loader
from hoops.jobs.runs import last_success, run_logged
from hoops.jobs.tasks import Jobs, current_season, eastern_date
from hoops.leagues import League

from .fixtures import load

NOW = datetime(2026, 4, 11, 0, 0, tzinfo=UTC)   # evening of April 10, Eastern time


def test_current_season_and_eastern_date():
    assert current_season(date(2026, 10, 20)) == 2027
    assert current_season(date(2027, 4, 1)) == 2027
    assert current_season(date(2026, 7, 31)) == 2026
    assert eastern_date(datetime(2026, 4, 11, 2, 0, tzinfo=UTC)) == date(2026, 4, 10)


class RecordingHooks:
    def __init__(self):
        self.locked: list[int] = []
        self.finished: list[int] = []
        self.injury_updates = 0

    def lock(self, conn, game_id, now):
        self.locked.append(game_id)

    def after_final(self, conn, game_id):
        self.finished.append(game_id)

    def after_injuries(self, conn, league):
        self.injury_updates += 1

    def overnight(self, conn, league):
        pass


def fake_espn(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/summary"):
        return httpx.Response(200, json=load("nba_summary_2026.json.gz"))
    if path.endswith("/teams"):
        return httpx.Response(200, json=load("nba_teams.json.gz"))
    if path.endswith("/injuries"):
        return httpx.Response(200, json=load("nba_injuries.json.gz"))
    if path.endswith("/odds"):
        return httpx.Response(200, json={"items": []})
    return httpx.Response(200, json={"events": []})   # scoreboards: nothing new


@pytest.fixture
def jobs(db, tmp_path):
    client = EspnClient(RawStore(tmp_path), min_interval=0,
                        client=httpx.Client(transport=httpx.MockTransport(fake_espn)))
    loader = Loader(db, client, League.NBA)
    loader.sync_teams()
    return Jobs(loader=loader, hooks=RecordingHooks())


def add_game(db, espn_id: str, start: datetime, status: str, season_type="regular") -> int:
    teams = [r[0] for r in db.execute(
        "SELECT team_id FROM teams WHERE espn_id IN ('30', '8') ORDER BY espn_id DESC")]
    return db.execute(
        "INSERT INTO games (league, espn_id, season, season_type, start_time, home_team_id,"
        " away_team_id, status) VALUES ('nba', %s, 2026, %s, %s, %s, %s, %s) RETURNING game_id",
        (espn_id, season_type, start, teams[0], teams[1], status),
    ).fetchone()[0]


@pytest.mark.db
def test_watcher_locks_games_near_tip_and_loads_finished_games(db, jobs):
    soon = add_game(db, "900", NOW + timedelta(minutes=20), "scheduled")
    later = add_game(db, "901", NOW + timedelta(hours=3), "scheduled")
    done = add_game(db, "401811026", NOW - timedelta(hours=3), "final")

    details = jobs.game_watcher(NOW)

    assert jobs.hooks.locked == [soon]
    assert later not in jobs.hooks.locked
    assert jobs.hooks.finished == [done]
    assert details["locked"] == 1 and details["finished"] == 1
    kinds = {r[0] for r in db.execute(
        "SELECT line_kind FROM betting_lines WHERE game_id = %s", (soon,))}
    assert kinds == {"captured_pregame"}
    assert db.execute("SELECT team_quality_ok FROM games WHERE game_id = %s",
                      (done,)).fetchone()[0] is True


@pytest.mark.db
def test_preseason_games_only_watched_in_rehearsal(db, jobs):
    pre = add_game(db, "902", NOW + timedelta(minutes=10), "scheduled", season_type="pre")
    jobs.game_watcher(NOW)
    assert pre not in jobs.hooks.locked
    jobs.rehearsal = True
    jobs.game_watcher(NOW)
    assert pre in jobs.hooks.locked


@pytest.mark.db
def test_injury_sync_runs_only_on_game_days(db, jobs):
    assert jobs.injury_sync(NOW) == {"skipped": "no games today"}
    add_game(db, "903", NOW + timedelta(hours=1), "scheduled")
    assert jobs.injury_sync(NOW)["injuries"] > 0
    assert jobs.hooks.injury_updates == 1


@pytest.mark.db
def test_run_logged_records_success_and_failure(db):
    assert run_logged(db, "demo", "nba", lambda: {"n": 3}) is True

    def boom():
        raise RuntimeError("ESPN down")

    assert run_logged(db, "demo", "nba", boom) is False
    rows = db.execute("SELECT status, details, error FROM job_runs ORDER BY job_run_id").fetchall()
    assert rows[0][:2] == ("succeeded", {"n": 3})
    assert rows[1][0] == "failed" and "ESPN down" in rows[1][2]
    assert last_success(db, "demo", "nba") is not None
