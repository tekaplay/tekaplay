"""Brute-force protection on the credential endpoints.

The limiter is backed by Redis, which the test suite does not run, and it
fails open by design — so without a stand-in these tests would pass while
asserting nothing. FakeRedis supplies just the two operations the limiter
uses, keeping the real code path (including the fail-open branch) intact.
"""
import pytest

from app.core.config import get_settings

#: Deliberately far below the production default. Every failed login runs an
#: argon2 hash (the timing-equalization step), so looping to the real limit of
#: 10 would spend seconds on key derivation for no extra coverage. What is
#: under test is that a threshold is enforced, not what its value happens to be.
TEST_LIMIT = 3


class FakeRedis:
    """Minimal INCR/EXPIRE counter — enough for a fixed-window limiter."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


@pytest.fixture
def limited(monkeypatch):
    """Lower the threshold and give the limiter a working Redis."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_attempts", TEST_LIMIT)
    # One shared instance: get_redis() is called per check, and a lambda that
    # constructs a new FakeRedis each time would reset the counter every call.
    client = FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis", lambda: client)
    return TEST_LIMIT


async def _login(client, email="nobody@example.com", password="wrong-password-x"):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_repeated_failed_logins_are_throttled(client, limited):
    for _ in range(limited):
        assert (await _login(client)).status_code == 401

    throttled = await _login(client)
    assert throttled.status_code == 429
    assert throttled.json()["error"]["code"] == "rate_limited"


async def test_throttle_survives_changing_the_email(client, limited):
    """Guessing a different account from the same address must not reset the
    counter — otherwise the limit is trivially bypassed."""
    for i in range(limited):
        assert (await _login(client, email=f"victim{i}@example.com")).status_code == 401

    assert (await _login(client, email="another@example.com")).status_code == 429


async def test_registration_is_throttled(client, limited):
    for i in range(limited):
        resp = await client.post("/api/v1/auth/register", json={
            "email": f"new{i}@example.com", "password": "correct-horse-battery",
            "display_name": "New"})
        assert resp.status_code == 201, resp.text

    resp = await client.post("/api/v1/auth/register", json={
        "email": "overflow@example.com", "password": "correct-horse-battery",
        "display_name": "Overflow"})
    assert resp.status_code == 429


async def test_limiter_fails_open_when_redis_is_down(client, monkeypatch):
    """Availability over strictness: a Redis outage must not lock users out."""
    monkeypatch.setattr(get_settings(), "auth_rate_limit_attempts", TEST_LIMIT)

    class BrokenRedis:
        async def incr(self, key: str) -> int:
            raise ConnectionError("redis unreachable")

    monkeypatch.setattr("app.core.redis.get_redis", lambda: BrokenRedis())

    for _ in range(TEST_LIMIT + 2):
        assert (await _login(client)).status_code == 401
