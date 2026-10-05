from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from hoops.espn.client import EspnClient, EspnError
from hoops.espn.raw_store import RawResponse, RawStore, params_key
from hoops.leagues import League


def make_client(tmp_path, handler, **kwargs):
    sleeps: list[float] = []
    client = EspnClient(
        RawStore(tmp_path),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
        clock=lambda: 0.0,
        **kwargs,
    )
    return client, sleeps


def test_response_saved_before_return_and_cache_reused(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"events": []})

    client, _ = make_client(tmp_path, handler)
    assert client.summary(League.NBA, "1").body == {"events": []}
    assert len(list(tmp_path.rglob("*.json.gz"))) == 1
    assert client.summary(League.NBA, "1", use_cache=True).body == {"events": []}
    assert len(calls) == 1


def test_college_scoreboard_asks_for_all_division_one_games(tmp_path):
    seen = []
    client, _ = make_client(tmp_path, lambda r: seen.append(r.url) or httpx.Response(200, json={}))
    client.scoreboard(League.NCAAM, datetime(2026, 1, 3).date())
    assert seen[0].params["groups"] == "50" and seen[0].params["limit"] == "500"


def test_retries_with_exponential_backoff(tmp_path):
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, json={})])
    client, sleeps = make_client(tmp_path, lambda r: next(responses), min_interval=0)
    client.injuries(League.NBA)
    assert sleeps == [2, 4]


def test_gives_up_after_max_retries(tmp_path):
    def handler(request):
        raise httpx.ConnectTimeout("slow", request=request)

    client, sleeps = make_client(tmp_path, handler, max_retries=2, min_interval=0)
    with pytest.raises(EspnError, match="gave up after 2 retries"):
        client.injuries(League.NBA)
    assert sleeps == [2, 4]


def test_client_errors_are_stored_and_raised_without_retry(tmp_path):
    client, sleeps = make_client(tmp_path, lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(EspnError, match="HTTP 404"):
        client.summary(League.NBA, "x")
    assert sleeps == []
    assert len(list(tmp_path.rglob("*.json.gz"))) == 1


def test_throttle_spaces_requests(tmp_path):
    now = [0.0]
    sleeps: list[float] = []
    client = EspnClient(
        RawStore(tmp_path), min_interval=1.0,
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))),
        sleep=sleeps.append, clock=lambda: now[0],
    )
    client.injuries(League.NBA)
    now[0] = 0.25
    client.injuries(League.NBA)
    assert sleeps == [pytest.approx(0.75)]


def test_params_key_is_order_independent():
    assert params_key({"a": 1, "b": "x"}) == params_key({"b": "x", "a": 1})
    assert params_key({"a": 1}) != params_key({"a": 2})


def test_prune_removes_only_old_files(tmp_path):
    store = RawStore(tmp_path)
    now = datetime.now(UTC)
    for age_days in (10, 1):
        store.save(RawResponse("nba", "live", {"event": "1"}, "u", 200,
                               now - timedelta(days=age_days), {}))
    assert store.prune("nba", "live", now - timedelta(days=7)) == 1
    assert len(list(tmp_path.rglob("*.json.gz"))) == 1
