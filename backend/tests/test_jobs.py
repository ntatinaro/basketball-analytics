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

    def after_finals(self, conn, game_ids):
        self.finished.extend(game_ids)

    def after_injuries(self, conn, league):
        self.injury_updates += 1

    def overnight(self, conn, league):
        pass

    def refresh(self, conn, league):
        return 0


def fake_espn(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/summary"):
        return httpx.Response(200, json=load("nba_summary_2026.json.gz"))
    if path.endswith("/teams"):
        return httpx.Response(200, json=load("nba_teams.json.gz"))
    if path.endswith("/injuries"):
        return httpx.Response(200, json=load("nba_injuries.json.gz"))
    if path.endswith("/odds"):
        if "/events/401704835/" in path:
            return httpx.Response(200, json=load("nba_odds_2025.json.gz"))
        return httpx.Response(200, json={"items": []})
    return httpx.Response(200, json={"events": []})   # scoreboards: nothing new


def jobs_with(db, tmp_path, transport, hooks=None) -> Jobs:
    """Jobs whose ESPN client goes through `transport` (teams must already be stored)."""
    client = EspnClient(RawStore(tmp_path / "outage"), min_interval=0, max_retries=1,
                        sleep=lambda _: None,
                        client=httpx.Client(transport=httpx.MockTransport(transport)))
    return Jobs(loader=Loader(db, client, League.NBA), hooks=hooks or RecordingHooks())


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


def test_close_line_backfill_fills_only_missing_finals(db, jobs):
    with_odds = add_game(db, "401704835", NOW - timedelta(days=3), "final")
    add_game(db, "401704836", NOW - timedelta(days=3), "final")        # no odds at ESPN
    add_game(db, "401704837", NOW + timedelta(days=1), "scheduled")
    assert jobs.loader.backfill_close_lines(2026) == 1
    rows = db.execute("SELECT game_id, line_kind, home_moneyline FROM betting_lines").fetchall()
    assert rows == [(with_odds, "espn_close", 130)]
    assert jobs.loader.backfill_close_lines(2026) == 0                # already stored


class FailingLockHooks(RecordingHooks):
    def __init__(self, fail_for: int):
        super().__init__()
        self.fail_for = fail_for

    def lock(self, conn, game_id, now):
        if game_id == self.fail_for:
            raise RuntimeError("model error")
        super().lock(conn, game_id, now)


@pytest.mark.db
def test_espn_down_at_lock_time_still_locks(db, jobs, tmp_path):
    first = add_game(db, "900", NOW + timedelta(minutes=20), "scheduled")
    second = add_game(db, "904", NOW + timedelta(minutes=25), "scheduled")
    down = jobs_with(db, tmp_path, lambda request: httpx.Response(503))

    details = down.game_watcher(NOW)

    assert sorted(down.hooks.locked) == sorted([first, second])
    assert details["locked"] == 2
    assert any(e.startswith("pregame line") for e in details["errors"])
    assert any(e.startswith("scoreboard") for e in details["errors"])


@pytest.mark.db
def test_one_failing_lock_does_not_skip_the_others(db, jobs, tmp_path):
    first = add_game(db, "900", NOW + timedelta(minutes=20), "scheduled")
    second = add_game(db, "904", NOW + timedelta(minutes=25), "scheduled")
    j = jobs_with(db, tmp_path, fake_espn, hooks=FailingLockHooks(fail_for=first))
    details = j.game_watcher(NOW)
    assert j.hooks.locked == [second] and details["locked"] == 1
    assert any(e.startswith(f"lock {first}") for e in details["errors"])


@pytest.mark.db
def test_one_bad_final_does_not_block_the_rest(db, jobs, tmp_path):
    bad = add_game(db, "401000001", NOW - timedelta(hours=4), "final")
    good = add_game(db, "401811026", NOW - timedelta(hours=3), "final")
    bad_fetches = []

    def transport(request):
        if request.url.path.endswith("/summary") and request.url.params.get("event") == "401000001":
            bad_fetches.append(1)
            return httpx.Response(404)
        return fake_espn(request)

    j = jobs_with(db, tmp_path, transport)
    for minute in range(5):
        details = j.game_watcher(NOW + timedelta(minutes=minute))
        if minute == 0:
            assert details["finished"] == 1
            assert any(e.startswith("final 401000001") for e in details["errors"])
    assert j.hooks.finished == [good]                    # graded once, despite the bad game
    assert len(bad_fetches) == 3 and j.load_failures[bad] == 3   # then left for overnight

    overnight = j.overnight(NOW + timedelta(hours=8))
    assert len(bad_fetches) == 4 and j.load_failures == {}
    assert any(e.startswith("reload 401000001") for e in overnight["errors"])


@pytest.mark.db
def test_worker_stops_when_the_database_connection_is_lost(db):
    import os
    from types import SimpleNamespace

    import psycopg

    from hoops.worker import make_job

    conn = psycopg.connect(os.environ["HOOPS_TEST_DATABASE_URL"], autocommit=True)
    stopped = []
    job = make_job(SimpleNamespace(conn=conn, league=League.NBA), "demo", lambda: {"ok": 1},
                   lambda: stopped.append(1))
    job()
    assert stopped == []
    db.execute("SELECT pg_terminate_backend(%s)", (conn.info.backend_pid,))   # DB restart
    job()
    assert stopped == [1]
