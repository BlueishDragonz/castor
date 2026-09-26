"""Pytest configuration.

Sets the database URL for the whole session BEFORE any application module is
imported, then widens the IP auth rate limit.

Why the env var must be set here, first
---------------------------------------
`castor.configs.settings` is a pydantic BaseSettings instance constructed at
import time, and `castor/app/db.py` binds `DATABASE_URL = settings.DATABASE_URL`
into the engine at import time. Whichever module imports `castor.configs` FIRST
therefore fixes the database for the entire process.

conftest.py is imported before any test module, so before this fix the suite
resolved the default `./.user/habits.db` and ran every test against the real
development database — writing users, habits and circles into it. Individual
test modules do set `os.environ['DATABASE_URL']` at the top of the file, but by
then settings was already constructed and the assignment had no effect.

Setting it here, in the earliest-imported file, is what makes those per-module
assignments work as intended and keeps the suite off real data. Verified: a
probe test asserting the resolved engine URL previously reported
`sqlite+aiosqlite:///./.user/habits.db` and now reports the temp path.

A fresh TemporaryDirectory per run also means a failed run cannot contaminate
the next one.
"""
import os
import tempfile

_TEST_TMP = tempfile.TemporaryDirectory(prefix="castor-tests-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_TMP.name}/test.db"

import pytest

from castor.configs import settings


@pytest.fixture(autouse=True, scope="session")
def _widen_test_rate_limits():
    """Set IP-auth rate to the maximum supported by the validator.

    The API token test suite registers and logs in many users from the same
    loopback IP; without this they exhaust the bucket and unrelated tests
    sharing the module-scoped TestClient see spurious 429s.

    The per-user limit is unchanged; we only widen the per-IP bucket.
    """
    original = settings.AUTH_RATE_IP_PER_MINUTE
    settings.AUTH_RATE_IP_PER_MINUTE = 10000
    yield
    settings.AUTH_RATE_IP_PER_MINUTE = original


pytest_plugins = ["nicegui.testing.plugin"]
