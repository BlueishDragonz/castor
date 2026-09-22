"""Pytest configuration.

Bumps the IP-based auth rate limit high enough that the API token test
suite (which registers and logs in many users from the same loopback
IP) does not exhaust the bucket and produce 429s for unrelated tests
that share the module-scoped TestClient.

The per-user limit is unchanged; we only widen the per-IP bucket.
"""
import pytest

from beaverhabits.configs import settings


@pytest.fixture(autouse=True, scope="session")
def _widen_test_rate_limits():
    """Set IP-auth rate to the maximum supported by the validator."""
    original = settings.AUTH_RATE_IP_PER_MINUTE
    settings.AUTH_RATE_IP_PER_MINUTE = 10000
    yield
    settings.AUTH_RATE_IP_PER_MINUTE = original


pytest_plugins = ["nicegui.testing.plugin"]
