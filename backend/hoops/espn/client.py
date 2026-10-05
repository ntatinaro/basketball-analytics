"""ESPN site API client: polite rate limiting, retries, and raw storage.

Endpoints are listed in the architecture doc, section 4.1.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from hoops.espn.raw_store import RawResponse, RawStore
from hoops.leagues import RULES, League

log = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/{slug}"
SITE_WEB = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/{slug}"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/basketball/{slug}/standings"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/{slug}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EspnError(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"ESPN request failed: {reason} ({url})")
        self.url = url
        self.reason = reason


class EspnClient:
    """Fetches ESPN JSON. Every response is saved to the raw store before it is returned."""

    def __init__(
        self,
        raw_store: RawStore,
        *,
        min_interval: float = 0.4,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.raw_store = raw_store
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.timeout = timeout
        self.http = client or httpx.Client(headers=HEADERS, follow_redirects=True)
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self._lock = threading.Lock()

    # -- core ---------------------------------------------------------------------------

    def get(
        self,
        league: League,
        endpoint: str,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = False,
    ) -> RawResponse:
        """GET a JSON resource. With `use_cache`, a stored 200 response is returned instead
        of a network call. Use it only for data that no longer changes (finished games)."""
        params = params or {}
        if use_cache:
            cached = self.raw_store.latest(str(league), endpoint, params)
            if cached is not None and cached.status == 200:
                return cached

        reason = "no attempt made"
        for attempt in range(self.max_retries + 1):
            if attempt:
                delay = self.backoff_base * 2 ** (attempt - 1)
                log.warning("ESPN %s retry %d in %.0fs (%s)", endpoint, attempt, delay, reason)
                self._sleep(delay)
            self._throttle()
            try:
                resp = self.http.get(url, params=params, timeout=self.timeout)
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code in RETRYABLE_STATUS:
                reason = f"HTTP {resp.status_code}"
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {"_non_json_body": resp.text[:2000]}
            raw = RawResponse(
                league=str(league),
                endpoint=endpoint,
                params=params,
                url=str(resp.url),
                status=resp.status_code,
                fetched_at=datetime.now(UTC),
                body=body,
            )
            self.raw_store.save(raw)
            if resp.status_code != 200:
                raise EspnError(str(resp.url), f"HTTP {resp.status_code}")
            return raw
        raise EspnError(url, f"gave up after {self.max_retries} retries: {reason}")

    def _throttle(self) -> None:
        with self._lock:
            if self._last_request is not None:
                wait = self.min_interval - (self._clock() - self._last_request)
                if wait > 0:
                    self._sleep(wait)
            self._last_request = self._clock()

    # -- endpoints ----------------------------------------------------------------------

    @staticmethod
    def _slug(league: League) -> str:
        return RULES[league].espn_slug

    def scoreboard(self, league: League, day: date, *, use_cache: bool = False) -> RawResponse:
        params: dict[str, Any] = {"dates": day.strftime("%Y%m%d")}
        if league is League.NCAAM:
            params.update(groups=50, limit=500)  # otherwise only featured games come back
        url = SITE.format(slug=self._slug(league)) + "/scoreboard"
        return self.get(league, "scoreboard", url, params, use_cache=use_cache)

    def summary(self, league: League, event_id: str, *, use_cache: bool = False) -> RawResponse:
        url = SITE_WEB.format(slug=self._slug(league)) + "/summary"
        return self.get(league, "summary", url, {"event": event_id}, use_cache=use_cache)

    def teams(self, league: League) -> RawResponse:
        params: dict[str, Any] = {"limit": 500}
        if league is League.NCAAM:
            params["groups"] = 50  # Division I
        url = SITE.format(slug=self._slug(league)) + "/teams"
        return self.get(league, "teams", url, params)

    def standings(self, league: League, season: int, *, use_cache: bool = False) -> RawResponse:
        url = STANDINGS.format(slug=self._slug(league))
        return self.get(league, "standings", url, {"season": season}, use_cache=use_cache)

    def roster(self, league: League, team_espn_id: str) -> RawResponse:
        url = SITE.format(slug=self._slug(league)) + f"/teams/{team_espn_id}/roster"
        return self.get(league, "roster", url, {"team": team_espn_id})

    def injuries(self, league: League) -> RawResponse:
        url = SITE.format(slug=self._slug(league)) + "/injuries"
        return self.get(league, "injuries", url)

    def odds(self, league: League, event_id: str) -> RawResponse:
        url = (CORE.format(slug=self._slug(league))
               + f"/events/{event_id}/competitions/{event_id}/odds")
        return self.get(league, "odds", url, {"event": event_id})
