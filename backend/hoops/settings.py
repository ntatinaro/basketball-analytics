"""Settings, read from environment variables. `deploy/.env.example` lists them all."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hoops.leagues import League


@dataclass(frozen=True)
class Settings:
    database_url: str
    raw_dir: Path
    log_level: str
    leagues: tuple[League, ...]
    rehearsal: bool
    admin_password: str | None = None   # unset: the admin panel is switched off
    secret_key: str | None = None       # signs admin sessions


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "HOOPS_DATABASE_URL", "postgresql://hoops:hoops@localhost:5432/hoops"
        ),
        raw_dir=Path(os.environ.get("HOOPS_RAW_DIR", "data/raw")),
        log_level=os.environ.get("HOOPS_LOG_LEVEL", "INFO"),
        leagues=tuple(League(x.strip()) for x in
                      os.environ.get("HOOPS_LEAGUES", "nba").split(",") if x.strip()),
        rehearsal=os.environ.get("HOOPS_REHEARSAL", "0").lower() in ("1", "true", "yes"),
        admin_password=os.environ.get("HOOPS_ADMIN_PASSWORD") or None,
        secret_key=os.environ.get("HOOPS_SECRET_KEY") or None,
    )
