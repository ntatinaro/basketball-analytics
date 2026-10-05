"""Raw response store (architecture doc, section 4.4, rule 1).

Every ESPN response is saved before parsing, as a gzipped JSON envelope:

    {root}/{league}/{endpoint}/{params_key}/{fetched_at}.json.gz

so any response can be re-processed later without downloading it again.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawResponse:
    league: str
    endpoint: str
    params: dict[str, Any]
    url: str
    status: int
    fetched_at: datetime
    body: Any


def params_key(params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha1(canonical.encode()).hexdigest()[:10]
    slug = "_".join(f"{k}-{params[k]}" for k in sorted(params))
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "", slug)[:80]
    return f"{slug}__{digest}" if slug else digest


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, league: str, endpoint: str, params: dict[str, Any]) -> Path:
        return self.root / league / endpoint / params_key(params)

    def save(self, response: RawResponse) -> Path:
        directory = self._dir(response.league, response.endpoint, response.params)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{response.fetched_at.strftime('%Y%m%dT%H%M%S%fZ')}.json.gz"
        envelope = {
            "league": response.league,
            "endpoint": response.endpoint,
            "params": response.params,
            "url": response.url,
            "status": response.status,
            "fetched_at": response.fetched_at.isoformat(),
            "body": response.body,
        }
        tmp = path.with_name(path.name + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(envelope, f, separators=(",", ":"))
        tmp.replace(path)
        return path

    def latest(self, league: str, endpoint: str, params: dict[str, Any]) -> RawResponse | None:
        directory = self._dir(league, endpoint, params)
        files = sorted(directory.glob("*.json.gz")) if directory.is_dir() else []
        if not files:
            return None
        with gzip.open(files[-1], "rt", encoding="utf-8") as f:
            env = json.load(f)
        return RawResponse(
            league=env["league"],
            endpoint=env["endpoint"],
            params=env["params"],
            url=env["url"],
            status=env["status"],
            fetched_at=datetime.fromisoformat(env["fetched_at"]).astimezone(UTC),
            body=env["body"],
        )

    def prune(self, league: str, endpoint: str, older_than: datetime) -> int:
        """Delete stored responses for an endpoint fetched before `older_than`."""
        removed = 0
        base = self.root / league / endpoint
        if not base.is_dir():
            return 0
        stamp = older_than.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        for path in base.glob("*/*.json.gz"):
            if path.name < stamp:
                path.unlink()
                removed += 1
        return removed
