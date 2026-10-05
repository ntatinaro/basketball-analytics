from __future__ import annotations

import os

import psycopg
import pytest

from hoops.db.migrate import migrate


@pytest.fixture
def db():
    """A freshly migrated database. Skips when HOOPS_TEST_DATABASE_URL is not set.

    The test database is wiped on every use, so never point this at real data.
    """
    url = os.environ.get("HOOPS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("HOOPS_TEST_DATABASE_URL not set")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        migrate(conn)
        yield conn
