from __future__ import annotations

import gzip
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "espn"


def load(name: str) -> dict:
    """A saved real ESPN response (see tests/fixtures/espn)."""
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as f:
        return json.load(f)
