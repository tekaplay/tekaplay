"""Player systems as event consumers: a full playthrough over HTTP should
leave durable XP (with the achievement-reward cascade), progress, mastery,
streaks, inventory, and a leaderboard entry — with zero direct calls from the
runtime into any of these modules."""
import json
from pathlib import Path

import pytest

from tests.test_rbac import _grant_permission

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "calculus_mission_1.json"


@pytest.fixture
async def published_definition_id(client, registered_user):
    """Publish the example mission through the API (content.publish path)."""
    await _grant_permission(registered_user["email"], "content.publish")
    login = await client.post("/api/v1/auth/login", json={
        "email": registered_user["email"], "password": registered_user["password"]})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    raw = json.loads(EXAMPLE.read_text())
    resp = await client.post("/api/v1/runtime/definitions", headers=headers,
                             json={"slug": "calculus-mission-1", "definition": raw})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _play_to_hero_ending(client, headers, definition_id,
                               wrong_first: bool = True) -> str:
    start = await client.post("/api/v1/runtime/sessions", headers=headers,
                              json={"definition_id": definition_id})
    sid = start.json()["session_id"]
    base = f"/api/v1/runtime/sessions/{sid}"
    await client.post(f"{base}/choose", headers=headers,
                      json={"element_id": "first_call", "option_id": "derivative"})
    if wrong_first:
        await client.post(f"{base}/answer", headers=headers,
                          json={"element_id": "q_derivative_def",
                                "response": {"selected": ["b"]}})
    await client.post(f"{base}/answer", headers=headers,
                      json={"element_id": "q_derivative_def", "response": {"selected": ["a"]}})
    await client.post(f"{base}/answer", headers=headers,
                      json={"element_id": "q_order",
                            "response": {"order": ["position", "velocity", "acceleration"]}})
    await client.post(f"{base}/answer", headers=headers,
                      json={"element_id": "q_power_rule",
                            "response": {"text": "6t"}})
    await client.post(f"{base}/advance", headers=headers)
    await client.post(f"{base}/choose", headers=headers,
                      json={"element_id": "wrap_up", "option_id": "hero_end"})
    final = await client.post(f"{base}/advance", headers=headers)
    assert final.json()["status"] == "completed"
    return sid


@pytest.fixture
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


async def test_playthrough_produces_full_player_state(client, auth_headers,
                                                      registered_user,
                                                      published_definition_id):
    # Define the achievement the mission references, with an XP reward that
    # must cascade: achievement.unlocked → grant → xp.awarded → ledger.
    await _grant_permission(registered_user["email"], "achievements.manage")
    created = await client.post("/api/v1/achievements", headers=auth_headers,
                                json={"code": "rate_of_change_hero",
                                      "title": "Rate of Change Hero",
                                      "description": "Ace your first shift.",
                                      "xp_reward": 20})
    assert created.status_code == 201

    await _play_to_hero_ending(client, auth_headers, published_definition_id)

    # XP: 50+40+30+60 in-game + 20 achievement reward = 200 → level 2
    xp = (await client.get("/api/v1/xp/me", headers=auth_headers)).json()
    assert xp["total_xp"] == 200
    assert xp["level"] == 2
    assert xp["next_level_at"] == 400

    # Achievements
    mine = (await client.get("/api/v1/achievements/me", headers=auth_headers)).json()
    assert [a["code"] for a in mine] == ["rate_of_change_hero"]

    # Progress + mastery: 4 answers (1 wrong quiz attempt), 3 correct
    progress = (await client.get("/api/v1/progress/me", headers=auth_headers)).json()
    assert len(progress) == 1
    record = progress[0]
    assert record["slug"] == "calculus-mission-1"
    assert record["status"] == "completed"
    assert record["completions"] == 1
    assert record["best_ending"] == "hero"
    assert record["questions_answered"] == 4
    assert record["questions_correct"] == 3

    # Streak
    streak = (await client.get("/api/v1/progress/me/streak",
                               headers=auth_headers)).json()
    assert streak["current_streak"] == 1
    assert streak["longest_streak"] == 1

    # Inventory mirrored from in-game grants
    inventory = (await client.get("/api/v1/inventory/me", headers=auth_headers)).json()
    items = {i["item_key"]: i["qty"] for i in inventory}
    assert items == {"chart": 1, "graph_pad": 1}
    assert all(i["source_slug"] == "calculus-mission-1" for i in inventory)

    # Leaderboard
    board = (await client.get("/api/v1/xp/leaderboard", headers=auth_headers)).json()
    assert board[0]["display_name"] == "Alice"
    assert board[0]["total_xp"] == 200
    assert board[0]["rank"] == 1


async def test_replay_is_idempotent_per_grant_and_accumulates_xp(
        client, auth_headers, registered_user, published_definition_id):
    await _grant_permission(registered_user["email"], "achievements.manage")
    await client.post("/api/v1/achievements", headers=auth_headers,
                      json={"code": "rate_of_change_hero", "title": "Rate of Change Hero",
                            "xp_reward": 20})

    await _play_to_hero_ending(client, auth_headers, published_definition_id)
    # replay the mission perfectly
    start = await client.post("/api/v1/runtime/sessions", headers=auth_headers,
                              json={"definition_id": published_definition_id,
                                    "replay": True})
    assert start.status_code == 201
    sid = start.json()["session_id"]
    base = f"/api/v1/runtime/sessions/{sid}"
    await client.post(f"{base}/choose", headers=auth_headers,
                      json={"element_id": "first_call", "option_id": "derivative"})
    for element, response in (("q_derivative_def", {"selected": ["a"]}),
                              ("q_order", {"order": ["position", "velocity", "acceleration"]}),
                              ("q_power_rule", {"text": "6t"})):
        await client.post(f"{base}/answer", headers=auth_headers,
                          json={"element_id": element, "response": response})
    await client.post(f"{base}/advance", headers=auth_headers)
    await client.post(f"{base}/choose", headers=auth_headers,
                      json={"element_id": "wrap_up", "option_id": "hero_end"})
    await client.post(f"{base}/advance", headers=auth_headers)

    # in-game XP accumulates on replay; the achievement grants exactly once
    xp = (await client.get("/api/v1/xp/me", headers=auth_headers)).json()
    assert xp["total_xp"] == 200 + 180
    mine = (await client.get("/api/v1/achievements/me", headers=auth_headers)).json()
    assert len(mine) == 1

    progress = (await client.get("/api/v1/progress/me", headers=auth_headers)).json()
    assert progress[0]["completions"] == 2


async def test_undefined_achievement_codes_are_ignored(client, auth_headers,
                                                       published_definition_id):
    # No catalog entry defined: the mission still completes and grants nothing.
    await _play_to_hero_ending(client, auth_headers, published_definition_id)
    mine = (await client.get("/api/v1/achievements/me", headers=auth_headers)).json()
    assert mine == []
    xp = (await client.get("/api/v1/xp/me", headers=auth_headers)).json()
    assert xp["total_xp"] == 180  # in-game XP only


async def test_hidden_achievements_absent_from_catalog(client, auth_headers,
                                                       registered_user):
    await _grant_permission(registered_user["email"], "achievements.manage")
    await client.post("/api/v1/achievements", headers=auth_headers,
                      json={"code": "secret", "title": "???", "hidden": True})
    await client.post("/api/v1/achievements", headers=auth_headers,
                      json={"code": "public", "title": "Getting Started"})
    catalog = (await client.get("/api/v1/achievements", headers=auth_headers)).json()
    assert [a["code"] for a in catalog] == ["public"]


def test_level_curve():
    from app.modules.xp.service import level_for_total, total_for_level

    assert level_for_total(0) == 1
    assert level_for_total(99) == 1
    assert level_for_total(100) == 2
    assert level_for_total(399) == 2
    assert level_for_total(400) == 3
    assert total_for_level(4) == 900
