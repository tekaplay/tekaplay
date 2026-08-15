"""Organizations: creation/ownership, membership, invitations, and
authorization (admin-only actions, 404-not-403 for non-members)."""
import uuid as uuidlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.session import SessionFactory
from app.modules.users.models import OrganizationInvitation


@pytest.fixture
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


async def _register_and_login(client, email: str) -> dict:
    body = {"email": email, "password": "correct-horse-battery", "display_name": "Bob"}
    resp = await client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    login = await client.post("/api/v1/auth/login", json={
        "email": email, "password": body["password"]})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
async def org(client, auth_headers) -> dict:
    resp = await client.post("/api/v1/organizations", headers=auth_headers,
                             json={"name": "Meridian Orbital Academy",
                                   "org_type": "school"})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_organization_makes_creator_owner(client, auth_headers, org):
    members = await client.get(f"/api/v1/organizations/{org['id']}/members",
                               headers=auth_headers)
    assert members.status_code == 200
    roles = {m["role"] for m in members.json()}
    assert roles == {"owner"}


async def test_non_member_gets_404_not_403(client, org):
    other_headers = await _register_and_login(client, "outsider@example.com")
    resp = await client.get(f"/api/v1/organizations/{org['id']}", headers=other_headers)
    assert resp.status_code == 404  # existence not leaked to non-members


async def test_invite_rejected_for_non_member(client, org):
    # a random authenticated user (not even a member) is rejected as a
    # non-member (404), matching the get_member_or_404 IDOR posture
    outsider_headers = await _register_and_login(client, "outsider2@example.com")
    resp = await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                             headers=outsider_headers,
                             json={"email": "someone@example.com", "role": "member"})
    assert resp.status_code == 404


async def test_invite_rejected_for_plain_member(client, auth_headers, org):
    from app.events.bus import DomainEvent, bus
    from app.modules.organizations import events as org_events

    captured: list[str] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(str(event.payload.get("token")))

    bus.subscribe(org_events.INVITATION_CREATED, collector)
    member_headers = await _register_and_login(client, "member@example.com")
    await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                      headers=auth_headers,
                      json={"email": "member@example.com", "role": "member"})
    await client.post("/api/v1/invitations/accept", headers=member_headers,
                      json={"token": captured[-1]})

    # a genuine, non-admin member is forbidden from admin-only actions
    resp = await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                             headers=member_headers,
                             json={"email": "someone@example.com", "role": "member"})
    assert resp.status_code == 403


async def test_invite_and_accept_existing_user(client, auth_headers, org):
    from app.events.bus import DomainEvent, bus
    from app.modules.organizations import events as org_events

    captured: list[str] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(str(event.payload.get("token")))

    bus.subscribe(org_events.INVITATION_CREATED, collector)

    invitee_headers = await _register_and_login(client, "invitee@example.com")
    created = await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                                headers=auth_headers,
                                json={"email": "invitee@example.com", "role": "member"})
    assert created.status_code == 201, created.text
    assert captured, "invitation token should travel on the event bus"
    token = captured[-1]

    preview = await client.get(f"/api/v1/invitations/{token}/preview")
    assert preview.status_code == 200
    assert preview.json()["valid"] is True
    assert preview.json()["organization_name"] == org["name"]

    accepted = await client.post("/api/v1/invitations/accept", headers=invitee_headers,
                                 json={"token": token})
    assert accepted.status_code == 204

    members = (await client.get(f"/api/v1/organizations/{org['id']}/members",
                                headers=auth_headers)).json()
    assert any(m["email"] == "invitee@example.com" and m["role"] == "member"
              for m in members)

    # single-use: accepting again fails
    again = await client.post("/api/v1/invitations/accept", headers=invitee_headers,
                              json={"token": token})
    assert again.status_code == 422


async def test_register_with_invitation_token_joins_org(client, auth_headers, org):
    from app.events.bus import DomainEvent, bus
    from app.modules.organizations import events as org_events

    captured: list[str] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(str(event.payload.get("token")))

    bus.subscribe(org_events.INVITATION_CREATED, collector)

    created = await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                                headers=auth_headers,
                                json={"email": "newbie@example.com", "role": "admin"})
    assert created.status_code == 201
    token = captured[-1]

    resp = await client.post("/api/v1/auth/register", json={
        "email": "newbie@example.com", "password": "correct-horse-battery",
        "display_name": "Newbie", "invitation_token": token,
    })
    assert resp.status_code == 201, resp.text

    members = (await client.get(f"/api/v1/organizations/{org['id']}/members",
                                headers=auth_headers)).json()
    assert any(m["email"] == "newbie@example.com" and m["role"] == "admin"
              for m in members)


async def test_expired_invitation_rejected(client, auth_headers, org):
    from app.events.bus import DomainEvent, bus
    from app.modules.organizations import events as org_events

    captured: list[str] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(str(event.payload.get("token")))

    bus.subscribe(org_events.INVITATION_CREATED, collector)

    invitee_headers = await _register_and_login(client, "late@example.com")
    created = await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                                headers=auth_headers,
                                json={"email": "late@example.com", "role": "member"})
    assert created.status_code == 201
    token = captured[-1]

    async with SessionFactory() as session:
        invitation = (await session.execute(select(OrganizationInvitation))).scalar_one()
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    preview = await client.get(f"/api/v1/invitations/{token}/preview")
    assert preview.status_code == 200
    assert preview.json()["valid"] is False

    accept = await client.post("/api/v1/invitations/accept", headers=invitee_headers,
                               json={"token": token})
    assert accept.status_code == 422


async def test_invalid_invitation_token_rejected(client):
    other_headers = await _register_and_login(client, "randomer@example.com")
    resp = await client.get(f"/api/v1/invitations/{uuidlib.uuid4().hex}/preview")
    assert resp.status_code == 404

    accept = await client.post("/api/v1/invitations/accept", headers=other_headers,
                               json={"token": "not-a-real-token"})
    assert accept.status_code == 422


async def test_reused_and_revoked_invitation(client, auth_headers, org):
    from app.events.bus import DomainEvent, bus
    from app.modules.organizations import events as org_events

    captured: list[str] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(str(event.payload.get("token")))

    bus.subscribe(org_events.INVITATION_CREATED, collector)

    created = await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                                headers=auth_headers,
                                json={"email": "revokeme@example.com", "role": "member"})
    assert created.status_code == 201
    invitation_id = created.json()["id"]
    token = captured[-1]

    revoked = await client.delete(
        f"/api/v1/organizations/{org['id']}/invitations/{invitation_id}",
        headers=auth_headers)
    assert revoked.status_code == 204

    invitee_headers = await _register_and_login(client, "revokeme@example.com")
    accept = await client.post("/api/v1/invitations/accept", headers=invitee_headers,
                               json={"token": token})
    assert accept.status_code == 422


async def test_remove_member_and_leave(client, auth_headers, org):
    invitee_headers = await _register_and_login(client, "leaver@example.com")

    from app.events.bus import DomainEvent, bus
    from app.modules.organizations import events as org_events

    captured: list[str] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(str(event.payload.get("token")))

    bus.subscribe(org_events.INVITATION_CREATED, collector)
    await client.post(f"/api/v1/organizations/{org['id']}/invitations",
                      headers=auth_headers,
                      json={"email": "leaver@example.com", "role": "member"})
    token = captured[-1]
    await client.post("/api/v1/invitations/accept", headers=invitee_headers,
                      json={"token": token})

    async with SessionFactory() as session:
        from app.modules.users.models import User

        target_id = (await session.execute(
            select(User.id).where(User.email == "leaver@example.com")
        )).scalar_one()

    removed = await client.post(
        f"/api/v1/organizations/{org['id']}/members/{target_id}/remove",
        headers=auth_headers)
    assert removed.status_code == 204

    # sole owner cannot leave
    leave = await client.post(f"/api/v1/organizations/{org['id']}/leave",
                              headers=auth_headers)
    assert leave.status_code == 422


async def test_multi_org_membership(client, auth_headers, org):
    second = await client.post("/api/v1/organizations", headers=auth_headers,
                               json={"name": "Second Org"})
    assert second.status_code == 201
    mine = await client.get("/api/v1/organizations/me", headers=auth_headers)
    assert len(mine.json()) == 2
