"""Payment gateway — the only place Stripe HTTP details may live.

StripeGateway speaks Stripe's REST API directly over httpx (form-encoded) and
verifies webhook signatures manually (HMAC-SHA256 over "{t}.{payload}" with a
timestamp tolerance) — no vendor SDK anywhere in the codebase, per the
portability rules. FakeGateway is deterministic for dev and CI: same protocol,
no network, no keys.
"""
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import AuthenticationError


class CheckoutSession(BaseModel):
    id: str
    url: str


class PaymentGateway(Protocol):
    name: str

    async def create_customer(self, *, email: str, user_id: uuid.UUID) -> str: ...
    async def create_checkout_session(
        self, *, customer_id: str, price_id: str, plan_code: str,
        user_id: uuid.UUID, trial_days: int, success_url: str, cancel_url: str,
        quantity: int = 1, metadata: dict[str, str] | None = None,
    ) -> CheckoutSession: ...
    async def create_portal_session(self, *, customer_id: str,
                                    return_url: str) -> str: ...
    async def refund_payment(self, *, payment_intent_id: str,
                             amount_cents: int | None) -> str: ...
    async def update_subscription_quantity(self, *, subscription_id: str,
                                           quantity: int) -> None: ...
    def verify_webhook(self, payload: bytes, signature_header: str) -> dict[str, Any]: ...


class FakeGateway:
    """Deterministic local gateway. Webhooks require the literal signature
    'fake-signature' so the verification path is still exercised."""

    name = "fake"

    async def create_customer(self, *, email: str, user_id: uuid.UUID) -> str:
        return f"cus_fake_{hashlib.sha256(str(user_id).encode()).hexdigest()[:16]}"

    async def create_checkout_session(
        self, *, customer_id: str, price_id: str, plan_code: str,
        user_id: uuid.UUID, trial_days: int, success_url: str, cancel_url: str,
        quantity: int = 1, metadata: dict[str, str] | None = None,
    ) -> CheckoutSession:
        session_id = f"cs_fake_{uuid.uuid4().hex[:16]}"
        return CheckoutSession(
            id=session_id, url=f"https://billing.example/checkout/{session_id}"
        )

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return f"https://billing.example/portal/{customer_id}"

    async def refund_payment(self, *, payment_intent_id: str,
                             amount_cents: int | None) -> str:
        return f"re_fake_{hashlib.sha256(payment_intent_id.encode()).hexdigest()[:16]}"

    async def update_subscription_quantity(self, *, subscription_id: str,
                                           quantity: int) -> None:
        return None

    def verify_webhook(self, payload: bytes, signature_header: str) -> dict[str, Any]:
        if signature_header != "fake-signature":
            raise AuthenticationError("Invalid webhook signature")
        return json.loads(payload)


class StripeGateway:
    name = "stripe"
    _base = "https://api.stripe.com/v1"

    async def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base}{path}",
                headers={"Authorization": f"Bearer {settings.stripe_api_key}"},
                data=data,
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Stripe error {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def _get(self, path: str) -> dict[str, Any]:
        settings = get_settings()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base}{path}",
                headers={"Authorization": f"Bearer {settings.stripe_api_key}"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"Stripe error {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def create_customer(self, *, email: str, user_id: uuid.UUID) -> str:
        customer = await self._post("/customers", {
            "email": email,
            "metadata[user_id]": str(user_id),
        })
        return customer["id"]

    async def create_checkout_session(
        self, *, customer_id: str, price_id: str, plan_code: str,
        user_id: uuid.UUID, trial_days: int, success_url: str, cancel_url: str,
        quantity: int = 1, metadata: dict[str, str] | None = None,
    ) -> CheckoutSession:
        data: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": str(quantity),
            "success_url": success_url,
            "cancel_url": cancel_url,
            "allow_promotion_codes": "true",  # coupons via Stripe promo codes
            "client_reference_id": str(user_id),
            "subscription_data[metadata][plan_code]": plan_code,
        }
        for key, value in (metadata or {}).items():
            data[f"subscription_data[metadata][{key}]"] = value
            data[f"metadata[{key}]"] = value  # session-level too, for checkout.session.completed
        if trial_days > 0:
            data["subscription_data[trial_period_days]"] = str(trial_days)
        session = await self._post("/checkout/sessions", data)
        return CheckoutSession(id=session["id"], url=session["url"])

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = await self._post("/billing_portal/sessions", {
            "customer": customer_id,
            "return_url": return_url,
        })
        return session["url"]

    async def refund_payment(self, *, payment_intent_id: str,
                             amount_cents: int | None) -> str:
        data: dict[str, Any] = {"payment_intent": payment_intent_id}
        if amount_cents is not None:
            data["amount"] = str(amount_cents)
        refund = await self._post("/refunds", data)
        return refund["id"]

    async def update_subscription_quantity(self, *, subscription_id: str,
                                           quantity: int) -> None:
        subscription = await self._get(f"/subscriptions/{subscription_id}")
        items = subscription.get("items", {}).get("data", [])
        if not items:
            return
        item_id = items[0]["id"]
        await self._post(f"/subscriptions/{subscription_id}", {
            "items[0][id]": item_id,
            "items[0][quantity]": str(quantity),
        })

    def verify_webhook(self, payload: bytes, signature_header: str) -> dict[str, Any]:
        settings = get_settings()
        parts = dict(
            part.split("=", 1) for part in signature_header.split(",") if "=" in part
        )
        timestamp = parts.get("t", "")
        candidate = parts.get("v1", "")
        if not timestamp or not candidate:
            raise AuthenticationError("Invalid webhook signature")
        try:
            age = abs(time.time() - int(timestamp))
        except ValueError as exc:
            raise AuthenticationError("Invalid webhook signature") from exc
        if age > settings.stripe_webhook_tolerance_seconds:
            raise AuthenticationError("Webhook timestamp outside tolerance")
        expected = hmac.new(
            settings.stripe_webhook_secret.encode(),
            f"{timestamp}.".encode() + payload,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, candidate):
            raise AuthenticationError("Invalid webhook signature")
        return json.loads(payload)


def get_gateway() -> PaymentGateway:
    gateways: dict[str, PaymentGateway] = {"fake": FakeGateway(), "stripe": StripeGateway()}
    return gateways[get_settings().payment_provider]
