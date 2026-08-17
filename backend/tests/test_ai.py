"""AI service: intake/validation, inline processing with the echo provider,
two-layer caching (durable DB layer exercised here), personalization context,
ownership, failure handling, and the rate limiter unit."""
import json
from pathlib import Path

import pytest

from tests.test_players import _play_to_hero_ending
from tests.test_rbac import _grant_permission

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "calculus_mission_1.json"


@pytest.fixture
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


async def test_feature_catalog(client, auth_headers):
    resp = await client.get("/api/v1/ai/features", headers=auth_headers)
    assert resp.status_code == 200
    catalog = {f["name"]: f for f in resp.json()}
    assert {"hint", "explanation", "flashcards", "npc_dialogue",
            "question_generation", "study_plan", "weakness_analysis"} <= set(catalog)
    assert catalog["study_plan"]["personalized"] is True
    assert catalog["hint"]["personalized"] is False


async def test_hint_request_inline_completion(client, auth_headers):
    resp = await client.post("/api/v1/ai/requests", headers=auth_headers,
                             json={"feature": "hint",
                                   "input": {"question": "What is a derivative?",
                                             "options": ["An instantaneous rate of change",
                                                         "A billing boundary"],
                                             "prior_attempts": 1}})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["response"]["provider"] == "echo"
    assert body["response"]["cached"] is False
    assert "What is a derivative?" in body["response"]["content"]
    # hints must never be asked to reveal answers
    assert "correct" not in body["response"]["content"].lower().split("question")[0]

    # retrievable by id, owner-scoped
    fetched = await client.get(f"/api/v1/ai/requests/{body['id']}",
                               headers=auth_headers)
    assert fetched.status_code == 200
    assert fetched.json()["response"]["content"] == body["response"]["content"]


async def test_identical_request_hits_durable_cache(client, auth_headers):
    payload = {"feature": "flashcards",
               "input": {"topic": "derivatives", "count": 4}}
    first = (await client.post("/api/v1/ai/requests", headers=auth_headers,
                               json=payload)).json()
    assert first["response"]["cached"] is False
    second = (await client.post("/api/v1/ai/requests", headers=auth_headers,
                                json=payload)).json()
    assert second["response"]["cached"] is True
    assert second["response"]["content"] == first["response"]["content"]
    assert second["id"] != first["id"]  # every request is audited separately


async def test_input_variation_misses_cache(client, auth_headers):
    base = {"feature": "flashcards", "input": {"topic": "derivatives", "count": 4}}
    await client.post("/api/v1/ai/requests", headers=auth_headers, json=base)
    varied = (await client.post(
        "/api/v1/ai/requests", headers=auth_headers,
        json={"feature": "flashcards",
              "input": {"topic": "derivatives", "count": 5}})).json()
    assert varied["response"]["cached"] is False


async def test_unknown_feature_and_invalid_input(client, auth_headers):
    unknown = await client.post("/api/v1/ai/requests", headers=auth_headers,
                                json={"feature": "mind_reading", "input": {}})
    assert unknown.status_code == 422
    assert "available" in unknown.json()["error"]["details"]

    invalid = await client.post("/api/v1/ai/requests", headers=auth_headers,
                                json={"feature": "flashcards",
                                      "input": {"topic": "x", "count": 999}})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["details"]["feature"] == "flashcards"


async def test_study_plan_includes_personal_progress_context(
        client, auth_headers, registered_user):
    # publish + play the mission so mastery data exists
    await _grant_permission(registered_user["email"], "content.publish")
    raw = json.loads(EXAMPLE.read_text())
    published = (await client.post("/api/v1/runtime/definitions",
                                   headers=auth_headers,
                                   json={"slug": "calculus-mission-1",
                                         "definition": raw})).json()
    await _play_to_hero_ending(client, auth_headers, published["id"])

    resp = (await client.post("/api/v1/ai/requests", headers=auth_headers,
                              json={"feature": "study_plan",
                                    "input": {"certification": "Calculus",
                                              "weeks": 2}})).json()
    content = resp["response"]["content"]
    assert resp["personalized"] is True
    assert "calculus-mission-1" in content        # weakness context reached the prompt
    assert "3/4 correct" in content               # mastery numbers included


async def test_personalized_cache_is_per_user(client, auth_headers):
    payload = {"feature": "weakness_analysis", "input": {}}
    mine = (await client.post("/api/v1/ai/requests", headers=auth_headers,
                              json=payload)).json()
    assert mine["response"]["cached"] is False

    other = {"email": "bob@example.com", "password": "correct-horse-battery",
             "display_name": "Bob"}
    await client.post("/api/v1/auth/register", json=other)
    login = await client.post("/api/v1/auth/login", json={
        "email": other["email"], "password": other["password"]})
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    theirs = (await client.post("/api/v1/ai/requests", headers=other_headers,
                                json=payload)).json()
    assert theirs["response"]["cached"] is False  # no cross-user cache hit


async def test_requests_are_private(client, auth_headers):
    created = (await client.post("/api/v1/ai/requests", headers=auth_headers,
                                 json={"feature": "hint",
                                       "input": {"question": "Q?"}})).json()
    other = {"email": "eve@example.com", "password": "correct-horse-battery",
             "display_name": "Eve"}
    await client.post("/api/v1/auth/register", json=other)
    login = await client.post("/api/v1/auth/login", json={
        "email": other["email"], "password": other["password"]})
    resp = await client.get(f"/api/v1/ai/requests/{created['id']}",
                            headers={"Authorization":
                                     f"Bearer {login.json()['access_token']}"})
    assert resp.status_code == 404


async def test_provider_failure_recorded_not_raised():
    """Service-level: a provider exception becomes status=failed with the
    error persisted and ai.request.failed emitted — never a 500."""
    from app.db.session import SessionFactory
    from app.events.bus import DomainEvent, InProcessEventBus
    from app.modules.ai.repository import AIRequestRepository, AIResponseRepository
    from app.modules.ai.service import AIService
    from app.modules.users.models import User

    class FailingProvider:
        name = "failing"

        async def complete(self, prompt: str):
            raise RuntimeError("provider melted")

    captured: list[DomainEvent] = []
    isolated = InProcessEventBus()

    async def collector(event: DomainEvent) -> None:
        captured.append(event)

    isolated.subscribe("ai.request.failed", collector)

    async with SessionFactory() as session:
        user = User(email="fail@example.com", display_name="F")
        session.add(user)
        await session.flush()
        service = AIService(AIRequestRepository(session),
                            AIResponseRepository(session),
                            FailingProvider(), isolated)
        request = await service.submit(user_id=user.id, feature_name="hint",
                                       raw_input={"question": "Q?"})
        assert request.status == "failed"
        assert "provider melted" in request.error
        await session.commit()

    assert [e.name for e in captured] == ["ai.request.failed"]


async def test_rate_limiter_unit():
    """Limiter logic against a stub client; fail-open on backend errors."""
    from app.core.errors import RateLimitedError
    from app.core.ratelimit import check_rate_limit

    class StubRedis:
        def __init__(self):
            self.counts: dict[str, int] = {}

        async def incr(self, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        async def expire(self, key, ttl):
            return True

    stub = StubRedis()
    for _ in range(3):
        await check_rate_limit("u1", limit=3, window_seconds=60, client=stub)
    with pytest.raises(RateLimitedError):
        await check_rate_limit("u1", limit=3, window_seconds=60, client=stub)

    class BrokenRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")

    # fail-open: no exception
    await check_rate_limit("u2", limit=1, window_seconds=60, client=BrokenRedis())
