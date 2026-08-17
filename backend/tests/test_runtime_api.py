"""Runtime through the HTTP API: sessions, save/resume, event stream,
optimistic concurrency."""
import json
import uuid as uuidlib
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.events.bus import DomainEvent, InProcessEventBus, bus
from app.modules.runtime.repository import (
    GameDefinitionRepository,
    GameSessionRepository,
    SavePointRepository,
)
from app.modules.runtime.service import RuntimeService

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "calculus_mission_1.json"


@pytest.fixture
async def published_definition():
    raw = json.loads(EXAMPLE.read_text())
    async with SessionFactory() as session:
        service = RuntimeService(
            definitions=GameDefinitionRepository(session),
            sessions=GameSessionRepository(session),
            saves=SavePointRepository(session),
            event_bus=InProcessEventBus(),  # direct-service: no fan-out mid-txn
        )
        record = await service.publish_definition(slug="calculus-mission-1", raw=raw)
        await session.commit()
        return str(record.id)


@pytest.fixture
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


@pytest.fixture
def event_log():
    captured: list[DomainEvent] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(event)

    bus.subscribe("*", collector)
    return captured


async def test_start_resume_and_replay(client, auth_headers, published_definition):
    start = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition})
    assert start.status_code == 201, start.text
    first_id = start.json()["session_id"]

    again = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition})
    assert again.json()["session_id"] == first_id  # resumed, not duplicated

    replay = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                               json={"definition_id": published_definition,
                                     "replay": True})
    assert replay.json()["session_id"] != first_id


async def test_full_playthrough_over_http(client, auth_headers, published_definition):
    start = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition})
    sid = start.json()["session_id"]
    base = f"/api/v1/runtime/sessions/{sid}"

    view = start.json()
    assert view["interactive"]["type"] == "choice"
    assert "correct" not in json.dumps(view)  # sanitized end to end

    view = (await client.post(f"{base}/choose", headers=auth_headers,
                              json={"element_id": "first_call",
                                    "option_id": "derivative"})).json()
    assert view["hud"]["inventory"] == {"chart": 1}

    wrong = (await client.post(f"{base}/answer", headers=auth_headers,
                               json={"element_id": "q_derivative_def",
                                     "response": {"selected": ["b"]}})).json()
    assert wrong["correct"] is False
    assert wrong["view"]["interactive"]["attempts_remaining"] == 1

    right = (await client.post(f"{base}/answer", headers=auth_headers,
                               json={"element_id": "q_derivative_def",
                                     "response": {"selected": ["a"]}})).json()
    assert right["correct"] is True and "instantaneous rate of change" in right["feedback"]

    await client.post(f"{base}/answer", headers=auth_headers,
                      json={"element_id": "q_order",
                            "response": {"order": ["position", "velocity", "acceleration"]}})
    await client.post(f"{base}/answer", headers=auth_headers,
                      json={"element_id": "q_power_rule",
                            "response": {"text": "6t"}})
    await client.post(f"{base}/advance", headers=auth_headers)
    await client.post(f"{base}/choose", headers=auth_headers,
                      json={"element_id": "wrap_up", "option_id": "hero_end"})
    final = (await client.post(f"{base}/advance", headers=auth_headers)).json()
    assert final["status"] == "completed"
    assert final["ending"]["id"] == "hero"
    assert final["hud"]["xp_earned"] == 180
    assert final["hud"]["achievements"] == ["rate_of_change_hero"]


async def test_simulation_event_stream(client, auth_headers, published_definition,
                                        event_log):
    """The architecture's promise: content changes are regression-tested by
    walking a definition and asserting on the emitted event stream."""
    start = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition})
    sid = start.json()["session_id"]
    base = f"/api/v1/runtime/sessions/{sid}"
    await client.post(f"{base}/choose", headers=auth_headers,
                      json={"element_id": "first_call", "option_id": "derivative"})
    await client.post(f"{base}/answer", headers=auth_headers,
                      json={"element_id": "q_derivative_def", "response": {"selected": ["a"]}})

    names = [e.name for e in event_log if e.payload.get("session_id") == sid]
    expected_prefix = ["mission.started", "scene.entered", "inventory.changed",
                       "scene.completed", "scene.entered", "question.answered",
                       "xp.awarded"]
    assert names[: len(expected_prefix)] == expected_prefix
    answered = next(e for e in event_log if e.name == "question.answered")
    assert answered.payload["correct"] is True
    assert answered.user_id is not None


async def test_save_and_restore(client, auth_headers, published_definition):
    start = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition})
    sid = start.json()["session_id"]
    base = f"/api/v1/runtime/sessions/{sid}"

    save = await client.post(f"{base}/saves", headers=auth_headers,
                             json={"label": "before the choice"})
    assert save.status_code == 201
    save_id = save.json()["id"]

    await client.post(f"{base}/choose", headers=auth_headers,
                      json={"element_id": "first_call", "option_id": "average_only"})
    restored = await client.post(f"{base}/saves/{save_id}/restore",
                                 headers=auth_headers)
    view = restored.json()
    assert view["scene_id"] == "briefing"
    assert view["hud"]["variables"]["mastery"] == 0
    assert view["interactive"]["id"] == "first_call"


async def test_sessions_are_private(client, auth_headers, published_definition):
    start = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition})
    sid = start.json()["session_id"]

    other = {"email": "mallory@example.com", "password": "correct-horse-battery",
             "display_name": "Mallory"}
    await client.post("/api/v1/auth/register", json=other)
    login = await client.post("/api/v1/auth/login", json={
        "email": other["email"], "password": other["password"]})
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    resp = await client.get(f"/api/v1/runtime/sessions/{sid}", headers=other_headers)
    assert resp.status_code == 404  # existence not leaked


@pytest.mark.skipif(
    get_settings().database_url.startswith("sqlite"),
    reason="Needs two genuinely isolated transactions so the second writer keeps "
           "a stale copy until it flushes. SQLite's file-level locking gives the "
           "second session the already-committed state instead, so the engine "
           "rejects the choice as inactive before the version check is ever "
           "reached. Runs for real against PostgreSQL in CI.",
)
async def test_optimistic_concurrency_conflict(published_definition, auth_tokens):
    """Two writers race on the same session: the second flush must surface a
    retryable 409, never a silent lost update."""
    from sqlalchemy import select

    from app.core.errors import ConflictError
    from app.modules.users.models import User

    definition_id = uuidlib.UUID(published_definition)

    isolated = InProcessEventBus()  # direct-service test: no fan-out mid-txn

    async def make_service(session):
        return RuntimeService(
            definitions=GameDefinitionRepository(session),
            sessions=GameSessionRepository(session),
            saves=SavePointRepository(session),
            event_bus=isolated,
        )

    async with SessionFactory() as s0:
        user_id = (await s0.execute(select(User.id).where(
            User.email == "alice@example.com"))).scalar_one()
        service = await make_service(s0)
        view = await service.start_session(user_id=user_id, definition_id=definition_id)
        await s0.commit()
        session_id = view.session_id

    async with SessionFactory() as s1, SessionFactory() as s2:
        svc1, svc2 = await make_service(s1), await make_service(s2)
        # Both writers load the session (version 1) before either commits.
        await GameSessionRepository(s2).get(session_id)
        await svc1.choose(user_id=user_id, session_id=session_id,
                          element_id="first_call", option_id="derivative")
        await s1.commit()
        # svc2 now operates on its stale in-memory copy; the flush's
        # UPDATE ... WHERE version=1 matches zero rows → 409.
        with pytest.raises(ConflictError):
            await svc2.choose(user_id=user_id, session_id=session_id,
                              element_id="first_call", option_id="average_only")


async def test_publish_requires_permission(client, auth_headers):
    raw = json.loads(EXAMPLE.read_text())
    resp = await client.post("/api/v1/runtime/definitions", headers=auth_headers,
                             json={"slug": "nope", "definition": raw})
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required"] == "content.publish"


async def test_publish_rejects_invalid_definition(client, auth_headers,
                                                  registered_user):
    from tests.test_rbac import _grant_permission

    await _grant_permission(registered_user["email"], "content.publish")
    resp = await client.post("/api/v1/runtime/definitions", headers=auth_headers,
                             json={"slug": "broken-game",
                                   "definition": {"schema_version": 1,
                                                  "title": "X",
                                                  "start_scene": "ghost",
                                                  "scenes": {}}})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"
