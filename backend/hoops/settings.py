"""Settings, read from environment variables. `deploy/.env.example` lists them all."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    raw_dir: Path
    log_level: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "HOOPS_DATABASE_URL", "postgresql://hoops:hoops@localhost:5432/hoops"
        ),
        raw_dir=Path(os.environ.get("HOOPS_RAW_DIR", "data/raw")),
        log_level=os.environ.get("HOOPS_LOG_LEVEL", "INFO"),
    )
