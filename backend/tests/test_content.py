"""Creator Studio lifecycle: draft → review → publish → rollback, catalog
library assembly, and in-flight session isolation from republishing."""
import json
from pathlib import Path

from tests.test_rbac import _grant_permission

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "calculus_mission_1.json"


def _definition(title: str) -> dict:
    raw = json.loads(EXAMPLE.read_text())
    raw["title"] = title
    return raw


async def _author_and_publisher(client):
    """Two users: an author and a separate reviewer/publisher."""
    author = {"email": "author@example.com", "password": "correct-horse-battery",
              "display_name": "Ada Author"}
    publisher = {"email": "publisher@example.com", "password": "correct-horse-battery",
                 "display_name": "Pat Publisher"}
    for account in (author, publisher):
        await client.post("/api/v1/auth/register", json=account)
    await _grant_permission(author["email"], "content.author")
    await _grant_permission(publisher["email"], "content.author")
    await _grant_permission(publisher["email"], "content.publish")

    async def login(account):
        resp = await client.post("/api/v1/auth/login", json={
            "email": account["email"], "password": account["password"]})
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    return await login(author), await login(publisher)


async def test_full_lifecycle_draft_to_published(client):
    author, publisher = await _author_and_publisher(client)

    project = (await client.post("/api/v1/content/projects", headers=author,
                                 json={"slug": "calculus-m1", "title": "Mission 1",
                                       "certification": "high-school-calculus"})).json()

    draft = (await client.post(f"/api/v1/content/projects/{project['id']}/versions",
                               headers=author,
                               json={"definition": _definition("v1"),
                                     "notes": "first cut"})).json()
    assert draft["status"] == "draft" and draft["version_number"] == 1

    # editable while draft
    updated = (await client.put(f"/api/v1/content/versions/{draft['id']}",
                                headers=author,
                                json={"definition": _definition("v1 edited"),
                                      "notes": "tightened dialogue"})).json()
    assert updated["notes"] == "tightened dialogue"

    submitted = (await client.post(f"/api/v1/content/versions/{draft['id']}/submit",
                                   headers=author)).json()
    assert submitted["status"] == "in_review"

    # immutable once submitted
    frozen = await client.put(f"/api/v1/content/versions/{draft['id']}",
                              headers=author,
                              json={"definition": _definition("sneaky"), "notes": ""})
    assert frozen.status_code == 422

    # author cannot approve their own work — publish permission required
    denied = await client.post(f"/api/v1/content/versions/{draft['id']}/approve",
                               headers=author, json={"note": "lgtm"})
    assert denied.status_code == 403

    approved = (await client.post(f"/api/v1/content/versions/{draft['id']}/approve",
                                  headers=publisher, json={"note": "ship it"})).json()
    assert approved["status"] == "approved"

    published = (await client.post(f"/api/v1/content/versions/{draft['id']}/publish",
                                   headers=publisher)).json()
    assert published["status"] == "published"

    # the definition is now live in the runtime and playable
    definitions = (await client.get("/api/v1/runtime/definitions",
                                    headers=author)).json()
    live = [d for d in definitions if d["slug"] == "calculus-m1"]
    assert len(live) == 1 and live[0]["title"] == "v1 edited"

    session = await client.post("/api/v1/runtime/sessions", headers=author,
                                json={"definition_id": live[0]["id"]})
    assert session.status_code == 201


async def test_submit_gates_broken_definitions(client):
    author, _ = await _author_and_publisher(client)
    project = (await client.post("/api/v1/content/projects", headers=author,
                                 json={"slug": "broken", "title": "Broken"})).json()
    draft = (await client.post(f"/api/v1/content/projects/{project['id']}/versions",
                               headers=author,
                               json={"definition": {"schema_version": 1,
                                                    "title": "X",
                                                    "start_scene": "ghost",
                                                    "scenes": {}},
                                     "notes": ""})).json()
    resp = await client.post(f"/api/v1/content/versions/{draft['id']}/submit",
                             headers=author)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"

    # the validate endpoint reports the same problems without state changes
    check = (await client.post("/api/v1/content/validate", headers=author,
                               json={"definition": {"schema_version": 1,
                                                    "title": "X",
                                                    "start_scene": "ghost",
                                                    "scenes": {}}})).json()
    assert check["valid"] is False and check["errors"]


async def test_reject_and_new_draft(client):
    author, publisher = await _author_and_publisher(client)
    project = (await client.post("/api/v1/content/projects", headers=author,
                                 json={"slug": "revise-me", "title": "R"})).json()
    v1 = (await client.post(f"/api/v1/content/projects/{project['id']}/versions",
                            headers=author,
                            json={"definition": _definition("v1"), "notes": ""})).json()
    await client.post(f"/api/v1/content/versions/{v1['id']}/submit", headers=author)
    rejected = (await client.post(f"/api/v1/content/versions/{v1['id']}/reject",
                                  headers=publisher,
                                  json={"note": "endings too abrupt"})).json()
    assert rejected["status"] == "rejected"
    assert rejected["review_note"] == "endings too abrupt"

    v2 = (await client.post(f"/api/v1/content/projects/{project['id']}/versions",
                            headers=author,
                            json={"definition": _definition("v2"), "notes": ""})).json()
    assert v2["version_number"] == 2


async def test_republish_isolates_inflight_sessions_and_rollback(client):
    author, publisher = await _author_and_publisher(client)
    project = (await client.post("/api/v1/content/projects", headers=author,
                                 json={"slug": "evolving", "title": "E"})).json()

    async def ship(title: str) -> dict:
        version = (await client.post(
            f"/api/v1/content/projects/{project['id']}/versions", headers=author,
            json={"definition": _definition(title), "notes": ""})).json()
        await client.post(f"/api/v1/content/versions/{version['id']}/submit",
                          headers=author)
        await client.post(f"/api/v1/content/versions/{version['id']}/approve",
                          headers=publisher, json={"note": ""})
        await client.post(f"/api/v1/content/versions/{version['id']}/publish",
                          headers=publisher)
        return version

    v1 = await ship("Mission v1")
    live_v1 = next(d for d in (await client.get("/api/v1/runtime/definitions",
                                                headers=author)).json()
                   if d["slug"] == "evolving")
    session = (await client.post("/api/v1/runtime/sessions", headers=author,
                                 json={"definition_id": live_v1["id"]})).json()

    v2 = await ship("Mission v2")

    # exactly one live definition per slug, and it's v2
    live_now = [d for d in (await client.get("/api/v1/runtime/definitions",
                                             headers=author)).json()
                if d["slug"] == "evolving"]
    assert len(live_now) == 1 and live_now[0]["title"] == "Mission v2"
    assert live_now[0]["id"] != live_v1["id"]

    # the in-flight session still plays the immutable v1 row
    view = (await client.get(f"/api/v1/runtime/sessions/{session['session_id']}",
                             headers=author)).json()
    assert view["definition_id"] == live_v1["id"]
    move = await client.post(
        f"/api/v1/runtime/sessions/{session['session_id']}/choose", headers=author,
        json={"element_id": "first_call", "option_id": "derivative"})
    assert move.status_code == 200

    # version bookkeeping: v1 superseded, v2 published
    versions = {v["version_number"]: v for v in
                (await client.get(f"/api/v1/content/projects/{project['id']}/versions",
                                  headers=author)).json()}
    assert versions[1]["status"] == "superseded"
    assert versions[2]["status"] == "published"

    # rollback: v1 becomes live again through the same path
    rolled = (await client.post(f"/api/v1/content/versions/{v1['id']}/rollback",
                                headers=publisher)).json()
    assert rolled["status"] == "published"
    live_after = [d for d in (await client.get("/api/v1/runtime/definitions",
                                               headers=author)).json()
                  if d["slug"] == "evolving"]
    assert len(live_after) == 1 and live_after[0]["title"] == "Mission v1"

    # rollback endpoint refuses non-superseded targets (v1 is live/published now;
    # v2 is superseded and would be a legitimate roll-forward target)
    refused = await client.post(f"/api/v1/content/versions/{v1['id']}/rollback",
                                headers=publisher)
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["status"] == "published"


async def test_library_tree(client):
    author, publisher = await _author_and_publisher(client)

    cert = (await client.post("/api/v1/content/certifications", headers=publisher,
                              json={"slug": "calculus", "title": "Calculus",
                                    "category": "math"})).json()
    campaign = (await client.post("/api/v1/content/campaigns", headers=publisher,
                                  json={"certification_id": cert["id"],
                                        "slug": "launch", "title": "Rate of Change Track",
                                        "sort_order": 1})).json()
    course = (await client.post("/api/v1/content/courses", headers=publisher,
                                json={"campaign_id": campaign["id"],
                                      "slug": "fundamentals",
                                      "title": "Calculus Fundamentals"})).json()

    project = (await client.post("/api/v1/content/projects", headers=author,
                                 json={"slug": "launchpad", "title": "Launchpad"})).json()
    version = (await client.post(f"/api/v1/content/projects/{project['id']}/versions",
                                 headers=author,
                                 json={"definition": _definition("Launchpad"),
                                       "notes": ""})).json()
    await client.post(f"/api/v1/content/versions/{version['id']}/submit", headers=author)
    await client.post(f"/api/v1/content/versions/{version['id']}/approve",
                      headers=publisher, json={"note": ""})
    await client.post(f"/api/v1/content/versions/{version['id']}/publish",
                      headers=publisher)

    await client.post("/api/v1/content/missions", headers=publisher,
                      json={"course_id": course["id"], "slug": "m1",
                            "title": "The Launchpad", "project_id": project["id"]})
    await client.post("/api/v1/content/missions", headers=publisher,
                      json={"course_id": course["id"], "slug": "m2",
                            "title": "Unwritten Mission", "sort_order": 2})

    tree = (await client.get("/api/v1/content/library", headers=author)).json()
    assert len(tree) == 1
    missions = tree[0]["campaigns"][0]["courses"][0]["missions"]
    assert [m["slug"] for m in missions] == ["m1", "m2"]
    assert missions[0]["definition_id"] is not None  # published → playable
    assert missions[1]["definition_id"] is None      # no project yet
