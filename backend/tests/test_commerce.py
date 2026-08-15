"""Commerce with the fake gateway: plans, checkout, the webhook-driven
subscription lifecycle, payment/refund flow, idempotency, signature
enforcement, and enterprise-license entitlement."""
import json
import uuid as uuidlib

import pytest

from app.core.config import get_settings
from tests.test_rbac import _grant_permission


@pytest.fixture
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


@pytest.fixture
async def admin_headers(client, auth_headers, registered_user):
    await _grant_permission(registered_user["email"], "commerce.manage")
    return auth_headers  # same account, now with manage rights


async def _webhook(client, event: dict, signature: str = "fake-signature"):
    return await client.post(
        "/api/v1/commerce/webhooks/stripe",
        content=json.dumps(event).encode(),
        headers={"stripe-signature": signature,
                 "content-type": "application/json"},
    )


def _event(event_type: str, obj: dict, event_id: str | None = None) -> dict:
    return {"id": event_id or f"evt_{uuidlib.uuid4().hex[:16]}",
            "type": event_type, "data": {"object": obj}}


@pytest.fixture
async def plan(client, admin_headers):
    resp = await client.post("/api/v1/commerce/plans", headers=admin_headers,
                             json={"code": "pro-monthly", "name": "Pro",
                                   "price_cents": 1500, "trial_days": 7})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def customer_id(client, auth_headers, plan) -> str:
    """Run checkout so the billing customer mapping exists, then return the
    fake Stripe customer id webhooks will reference."""
    resp = await client.post("/api/v1/commerce/checkout", headers=auth_headers,
                             json={"plan_code": "pro-monthly",
                                   "success_url": "https://app.example/ok",
                                   "cancel_url": "https://app.example/cancel"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["checkout_url"].startswith("https://billing.example/checkout/")

    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.commerce.models import BillingCustomer

    async with SessionFactory() as session:
        row = (await session.execute(select(BillingCustomer))).scalar_one()
        return row.stripe_customer_id


async def test_plans_visible_and_manage_gated(client, auth_headers, plan):
    plans = (await client.get("/api/v1/commerce/plans", headers=auth_headers)).json()
    assert [p["code"] for p in plans] == ["pro-monthly"]

    # the plan fixture granted manage to this account; a fresh account
    # must still be denied
    other = {"email": "pleb@example.com", "password": "correct-horse-battery",
             "display_name": "Pleb"}
    await client.post("/api/v1/auth/register", json=other)
    login = await client.post("/api/v1/auth/login", json={
        "email": other["email"], "password": other["password"]})
    fresh = {"Authorization": f"Bearer {login.json()['access_token']}"}
    denied = await client.post("/api/v1/commerce/plans", headers=fresh,
                               json={"code": "x", "name": "X", "price_cents": 1})
    assert denied.status_code == 403


async def test_subscription_lifecycle_via_webhooks(client, auth_headers,
                                                   customer_id):
    # before any webhook: no premium
    before = (await client.get("/api/v1/commerce/subscription",
                               headers=auth_headers)).json()
    assert before == {"premium": False, "source": "none", "subscription": None,
                      "trial": None}

    sub_id = "sub_fake_001"
    assert (await _webhook(client, _event("checkout.session.completed", {
        "customer": customer_id, "subscription": sub_id,
    }))).status_code == 204
    assert (await _webhook(client, _event("customer.subscription.updated", {
        "id": sub_id, "customer": customer_id, "status": "trialing",
        "current_period_end": 4102444800, "trial_end": 4102444800,
        "cancel_at_period_end": False,
        "metadata": {"plan_code": "pro-monthly"},
    }))).status_code == 204

    trialing = (await client.get("/api/v1/commerce/subscription",
                                 headers=auth_headers)).json()
    assert trialing["premium"] is True
    assert trialing["source"] == "subscription"
    assert trialing["subscription"]["status"] == "trialing"
    assert trialing["subscription"]["plan_id"] is not None

    # trial converts, invoice paid → payment recorded
    await _webhook(client, _event("customer.subscription.updated", {
        "id": sub_id, "customer": customer_id, "status": "active",
        "current_period_end": 4102444800,
    }))
    await _webhook(client, _event("invoice.paid", {
        "id": "in_001", "customer": customer_id, "payment_intent": "pi_001",
        "amount_paid": 1500, "currency": "usd", "subscription": sub_id,
    }))
    payments = (await client.get("/api/v1/commerce/payments/me",
                                 headers=auth_headers)).json()
    assert len(payments) == 1
    assert payments[0]["amount_cents"] == 1500
    assert payments[0]["status"] == "succeeded"

    # payment failure → past_due → premium lost (grace policy is explicit)
    await _webhook(client, _event("invoice.payment_failed", {
        "id": "in_002", "customer": customer_id, "subscription": sub_id,
    }))
    past_due = (await client.get("/api/v1/commerce/subscription",
                                 headers=auth_headers)).json()
    assert past_due["subscription"]["status"] == "past_due"
    assert past_due["premium"] is False

    # cancellation
    await _webhook(client, _event("customer.subscription.deleted", {"id": sub_id}))
    canceled = (await client.get("/api/v1/commerce/subscription",
                                 headers=auth_headers)).json()
    assert canceled["subscription"]["status"] == "canceled"


async def test_webhook_idempotency(client, auth_headers, customer_id):
    event = _event("invoice.paid", {
        "id": "in_dup", "customer": customer_id, "payment_intent": "pi_dup",
        "amount_paid": 900, "currency": "usd",
    }, event_id="evt_replayed")
    assert (await _webhook(client, event)).status_code == 204
    assert (await _webhook(client, event)).status_code == 204  # Stripe retry
    payments = (await client.get("/api/v1/commerce/payments/me",
                                 headers=auth_headers)).json()
    assert len(payments) == 1


async def test_webhook_signature_enforced(client):
    resp = await _webhook(client, _event("invoice.paid", {}), signature="wrong")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "authentication_required"


async def test_purchase_event_emitted(client, auth_headers, customer_id):
    from app.events.bus import DomainEvent, bus

    captured: list[DomainEvent] = []

    async def collector(event: DomainEvent) -> None:
        captured.append(event)

    bus.subscribe("purchase.completed", collector)
    await _webhook(client, _event("invoice.paid", {
        "id": "in_evt", "customer": customer_id, "payment_intent": "pi_evt",
        "amount_paid": 1500, "currency": "usd",
    }))
    assert any(e.payload.get("amount_cents") == 1500 for e in captured)
    assert all(e.user_id is not None for e in captured)


async def test_refund_flow(client, auth_headers, admin_headers, customer_id):
    await _webhook(client, _event("invoice.paid", {
        "id": "in_r", "customer": customer_id, "payment_intent": "pi_refund",
        "amount_paid": 1500, "currency": "usd",
    }))
    payments = (await client.get("/api/v1/commerce/payments/me",
                                 headers=auth_headers)).json()
    payment_id = payments[0]["id"]

    requested = await client.post(
        f"/api/v1/commerce/payments/{payment_id}/refund",
        headers=admin_headers, json={})
    assert requested.status_code == 202
    assert requested.json()["refund_id"].startswith("re_fake_")

    # Stripe confirms via webhook
    await _webhook(client, _event("charge.refunded", {
        "payment_intent": "pi_refund", "amount_refunded": 1500,
    }))
    refunded = (await client.get("/api/v1/commerce/payments/me",
                                 headers=auth_headers)).json()[0]
    assert refunded["status"] == "refunded"
    assert refunded["refunded_amount_cents"] == 1500

    # double refund request rejected
    again = await client.post(
        f"/api/v1/commerce/payments/{payment_id}/refund",
        headers=admin_headers, json={})
    assert again.status_code == 422


async def test_enterprise_license_grants_premium_only_when_assigned(
    client, auth_headers, admin_headers, registered_user,
):
    """Org membership alone is no longer sufficient — a seat must be
    explicitly assigned to the member (see LicenseAssignment). This is a
    deliberate behavior change from blanket org access."""
    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.users.models import Organization, OrganizationMember, User

    async with SessionFactory() as session:
        user_id = (await session.execute(select(User.id).where(
            User.email == registered_user["email"]))).scalar_one()
        org = Organization(name="Meridian Orbital", slug="meridian")
        session.add(org)
        await session.flush()
        session.add(OrganizationMember(organization_id=org.id, user_id=user_id,
                                       role="owner"))
        await session.commit()
        org_id = str(org.id)

    created = await client.post("/api/v1/commerce/licenses", headers=admin_headers,
                                json={"organization_id": org_id, "seats": 25,
                                      "notes": "Annual enterprise deal"})
    assert created.status_code == 201
    license_id = created.json()["id"]

    licenses = (await client.get("/api/v1/commerce/licenses",
                                 headers=admin_headers)).json()
    assert licenses[0]["seats"] == 25

    # membership alone: not premium yet
    before = (await client.get("/api/v1/commerce/subscription",
                               headers=auth_headers)).json()
    assert before["premium"] is False
    assert before["source"] == "none"

    assigned = await client.post(
        f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}/assign",
        headers=auth_headers, json={"user_id": str(user_id)})
    assert assigned.status_code == 201, assigned.text
    assignment_id = assigned.json()["id"]

    entitlement = (await client.get("/api/v1/commerce/subscription",
                                    headers=auth_headers)).json()
    assert entitlement["premium"] is True
    assert entitlement["source"] == "license"

    summary = (await client.get(f"/api/v1/commerce/organizations/{org_id}/licenses",
                                headers=auth_headers)).json()
    assert summary["seats_purchased"] == 25
    assert summary["seats_assigned"] == 1
    assert summary["seats_available"] == 24

    revoked = await client.post(
        f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}"
        f"/assignments/{assignment_id}/revoke",
        headers=auth_headers)
    assert revoked.status_code == 204

    after = (await client.get("/api/v1/commerce/subscription",
                              headers=auth_headers)).json()
    assert after["premium"] is False
    assert after["source"] == "none"


async def test_license_cannot_exceed_seats(client, auth_headers, admin_headers,
                                           registered_user):
    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.users.models import Organization, OrganizationMember, User

    async def _make_member(email: str, role: str, org_id) -> str:
        reg = {"email": email, "password": "correct-horse-battery", "display_name": "M"}
        await client.post("/api/v1/auth/register", json=reg)
        async with SessionFactory() as session:
            uid = (await session.execute(
                select(User.id).where(User.email == email))).scalar_one()
            session.add(OrganizationMember(organization_id=org_id, user_id=uid, role=role))
            await session.commit()
        return str(uid)

    async with SessionFactory() as session:
        owner_id = (await session.execute(select(User.id).where(
            User.email == registered_user["email"]))).scalar_one()
        org = Organization(name="Small Team", slug="small-team")
        session.add(org)
        await session.flush()
        session.add(OrganizationMember(organization_id=org.id, user_id=owner_id,
                                       role="owner"))
        await session.commit()
        org_id_obj, org_id = org.id, str(org.id)

    created = await client.post("/api/v1/commerce/licenses", headers=admin_headers,
                                json={"organization_id": org_id, "seats": 1})
    license_id = created.json()["id"]

    member1 = await _make_member("seatuser1@example.com", "member", org_id_obj)
    member2 = await _make_member("seatuser2@example.com", "member", org_id_obj)

    first = await client.post(
        f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}/assign",
        headers=auth_headers, json={"user_id": member1})
    assert first.status_code == 201

    second = await client.post(
        f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}/assign",
        headers=auth_headers, json={"user_id": member2})
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "validation_failed"


@pytest.mark.skipif(
    get_settings().database_url.startswith("sqlite"),
    reason="Seat allocation is serialized by SELECT ... FOR UPDATE, which SQLite "
           "does not implement (SQLAlchemy silently omits the clause). This runs "
           "for real against PostgreSQL in CI; test_license_cannot_exceed_seats "
           "covers the capacity rule itself on every dialect.",
)
async def test_concurrent_assignment_of_last_seat(client, auth_headers, admin_headers,
                                                   registered_user):
    import asyncio

    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.users.models import Organization, OrganizationMember, User

    async def _make_member(email: str, org_id) -> str:
        reg = {"email": email, "password": "correct-horse-battery", "display_name": "M"}
        await client.post("/api/v1/auth/register", json=reg)
        async with SessionFactory() as session:
            uid = (await session.execute(
                select(User.id).where(User.email == email))).scalar_one()
            session.add(OrganizationMember(organization_id=org_id, user_id=uid,
                                           role="member"))
            await session.commit()
        return str(uid)

    async with SessionFactory() as session:
        owner_id = (await session.execute(select(User.id).where(
            User.email == registered_user["email"]))).scalar_one()
        org = Organization(name="Race Co", slug="race-co")
        session.add(org)
        await session.flush()
        session.add(OrganizationMember(organization_id=org.id, user_id=owner_id,
                                       role="owner"))
        await session.commit()
        org_id_obj, org_id = org.id, str(org.id)

    created = await client.post("/api/v1/commerce/licenses", headers=admin_headers,
                                json={"organization_id": org_id, "seats": 1})
    license_id = created.json()["id"]

    member_a = await _make_member("race-a@example.com", org_id_obj)
    member_b = await _make_member("race-b@example.com", org_id_obj)

    async def _assign(user_id: str):
        return await client.post(
            f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}/assign",
            headers=auth_headers, json={"user_id": user_id})

    results = await asyncio.gather(_assign(member_a), _assign(member_b))
    statuses = sorted(r.status_code for r in results)
    assert statuses == [201, 422], \
        "exactly one of the two concurrent requests should win the last seat"


async def test_portal_requires_billing_profile(client, auth_headers):
    resp = await client.post("/api/v1/commerce/portal", headers=auth_headers,
                             json={"return_url": "https://app.example/settings"})
    assert resp.status_code == 422
