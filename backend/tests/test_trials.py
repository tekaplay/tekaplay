"""Configurable free trials: start/expiry, anti-abuse (single use, backend-
authoritative), default duration, and entitlement precedence against
subscriptions and org licenses."""
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from tests.test_rbac import _grant_permission


@pytest.fixture
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access_token']}"}


@pytest.fixture
async def admin_headers(auth_headers, registered_user):
    """Same account, now holding commerce.manage — mirrors the fixture in
    test_commerce.py."""
    await _grant_permission(registered_user["email"], "commerce.manage")
    return auth_headers


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Settings is process-cached via lru_cache; tests that flip env vars
    must invalidate it before and after so they don't leak into other tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_trial_starts_and_defaults_to_14_days(client, auth_headers, monkeypatch):
    monkeypatch.delenv("TRIAL_DURATION_DAYS", raising=False)
    get_settings.cache_clear()
    resp = await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    started = datetime.fromisoformat(body["started_at"])
    expires = datetime.fromisoformat(body["expires_at"])
    assert 13.9 < (expires - started).total_seconds() / 86400 < 14.1
    assert body["status"] == "active"


async def test_trial_duration_is_configurable(client, auth_headers, monkeypatch):
    monkeypatch.setenv("TRIAL_DURATION_DAYS", "30")
    get_settings.cache_clear()
    resp = await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    started = datetime.fromisoformat(body["started_at"])
    expires = datetime.fromisoformat(body["expires_at"])
    assert 29.9 < (expires - started).total_seconds() / 86400 < 30.1


async def test_trial_disabled_via_config(client, auth_headers, monkeypatch):
    monkeypatch.setenv("TRIAL_ENABLED", "false")
    get_settings.cache_clear()
    resp = await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    assert resp.status_code == 422


async def test_trial_cannot_be_restarted(client, auth_headers):
    first = await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    assert first.status_code == 201
    second = await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "validation_failed"


async def test_trial_grants_premium_until_expiry(client, auth_headers):
    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.commerce.models import Trial

    started = await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    assert started.status_code == 201

    active = (await client.get("/api/v1/commerce/subscription",
                               headers=auth_headers)).json()
    assert active["premium"] is True
    assert active["source"] == "trial"

    async with SessionFactory() as session:
        trial = (await session.execute(select(Trial))).scalar_one()
        trial.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    expired = (await client.get("/api/v1/commerce/subscription",
                                headers=auth_headers)).json()
    assert expired["premium"] is False
    assert expired["source"] == "none"


async def test_subscription_takes_precedence_over_trial(client, auth_headers,
                                                         admin_headers):
    """A trial doesn't disappear when a paid subscription starts, but the
    subscription wins the precedence order while both exist."""
    plan = await client.post("/api/v1/commerce/plans", headers=admin_headers,
                             json={"code": "trial-precedence-plan", "name": "Pro",
                                   "price_cents": 1000})
    assert plan.status_code == 201

    await client.post("/api/v1/commerce/trial/start", headers=auth_headers)
    trialing = (await client.get("/api/v1/commerce/subscription",
                                 headers=auth_headers)).json()
    assert trialing["source"] == "trial"

    checkout = await client.post("/api/v1/commerce/checkout", headers=auth_headers,
                                 json={"plan_code": "trial-precedence-plan",
                                       "success_url": "https://app.example/ok",
                                       "cancel_url": "https://app.example/cancel"})
    assert checkout.status_code == 200

    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.commerce.models import BillingCustomer

    async with SessionFactory() as session:
        customer = (await session.execute(select(BillingCustomer))).scalar_one()

    await client.post("/api/v1/commerce/webhooks/stripe",
                      headers={"stripe-signature": "fake-signature",
                               "content-type": "application/json"},
                      content=__import__("json").dumps({
                          "id": "evt_precedence_1", "type": "checkout.session.completed",
                          "data": {"object": {"customer": customer.stripe_customer_id,
                                              "subscription": "sub_precedence_1"}},
                      }).encode())
    await client.post("/api/v1/commerce/webhooks/stripe",
                      headers={"stripe-signature": "fake-signature",
                               "content-type": "application/json"},
                      content=__import__("json").dumps({
                          "id": "evt_precedence_2", "type": "customer.subscription.updated",
                          "data": {"object": {
                              "id": "sub_precedence_1", "customer": customer.stripe_customer_id,
                              "status": "active",
                              "metadata": {"plan_code": "trial-precedence-plan"},
                          }},
                      }).encode())

    entitlement = (await client.get("/api/v1/commerce/subscription",
                                    headers=auth_headers)).json()
    assert entitlement["source"] == "subscription"
    assert entitlement["trial"] is not None  # trial record preserved, not destroyed


async def test_org_license_takes_precedence_over_trial_and_trial_survives_revocation(
    client, auth_headers, admin_headers, registered_user,
):
    from sqlalchemy import select

    from app.db.session import SessionFactory
    from app.modules.users.models import Organization, OrganizationMember, User

    async with SessionFactory() as session:
        user_id = (await session.execute(select(User.id).where(
            User.email == registered_user["email"]))).scalar_one()
        org = Organization(name="Trial Precedence Org", slug="trial-precedence-org")
        session.add(org)
        await session.flush()
        session.add(OrganizationMember(organization_id=org.id, user_id=user_id,
                                       role="owner"))
        await session.commit()
        org_id = str(org.id)

    await client.post("/api/v1/commerce/trial/start", headers=auth_headers)

    created = await client.post("/api/v1/commerce/licenses", headers=admin_headers,
                                json={"organization_id": org_id, "seats": 5})
    license_id = created.json()["id"]
    assigned = await client.post(
        f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}/assign",
        headers=auth_headers, json={"user_id": str(user_id)})
    assignment_id = assigned.json()["id"]

    with_license = (await client.get("/api/v1/commerce/subscription",
                                     headers=auth_headers)).json()
    assert with_license["source"] == "license"

    await client.post(
        f"/api/v1/commerce/organizations/{org_id}/licenses/{license_id}"
        f"/assignments/{assignment_id}/revoke",
        headers=auth_headers)

    after_revoke = (await client.get("/api/v1/commerce/subscription",
                                     headers=auth_headers)).json()
    # trial is still live, so access falls back to it rather than disappearing
    assert after_revoke["source"] == "trial"
    assert after_revoke["premium"] is True
