"""Commerce orchestration.

Read path (entitlement — the central hasFeatureAccess-equivalent; see
entitlement() below): checked in strict precedence — an active/trialing
personal subscription, then an org license *assigned* to this specific user
(membership alone is no longer enough — see LicenseAssignment), then an
active free trial. This precedence is deterministic and is the single place
premium access is decided; nothing else in the codebase re-implements it.
Write path: local state is mutated only by verified, idempotent webhooks —
checkout and portal calls create Stripe sessions but never guess outcomes.
"""
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.events.bus import DomainEvent, EventBus
from app.modules.commerce import events as ev
from app.modules.commerce.gateway import PaymentGateway
from app.modules.commerce.models import (
    PREMIUM_STATUSES,
    SUB_CANCELED,
    SUB_INCOMPLETE,
    SUB_PAST_DUE,
    BillingCustomer,
    EnterpriseLicense,
    LicenseAssignment,
    OrganizationBillingCustomer,
    Payment,
    Plan,
    Subscription,
    Trial,
    WebhookEvent,
)
from app.modules.commerce.repository import (
    BillingCustomerRepository,
    EnterpriseLicenseRepository,
    LicenseAssignmentRepository,
    OrganizationBillingCustomerRepository,
    PaymentRepository,
    PlanRepository,
    SubscriptionRepository,
    TrialRepository,
    WebhookEventRepository,
)
from app.modules.users.audit import AuditService
from app.modules.users.service import UserService
from app.services.base import BaseService

log = get_logger(__name__)


def _from_unix(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError):
        return None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:  # SQLite returns naive datetimes
        return value.replace(tzinfo=UTC)
    return value


class CommerceService(BaseService):
    def __init__(
        self,
        plans: PlanRepository,
        customers: BillingCustomerRepository,
        subscriptions: SubscriptionRepository,
        payments: PaymentRepository,
        webhooks: WebhookEventRepository,
        licenses: EnterpriseLicenseRepository,
        assignments: LicenseAssignmentRepository,
        trials: TrialRepository,
        org_customers: OrganizationBillingCustomerRepository,
        gateway: PaymentGateway,
        users: UserService,
        audit: AuditService,
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._plans = plans
        self._customers = customers
        self._subscriptions = subscriptions
        self._payments = payments
        self._webhooks = webhooks
        self._licenses = licenses
        self._assignments = assignments
        self._trials = trials
        self._org_customers = org_customers
        self._gateway = gateway
        self._users = users
        self._audit = audit

    # ── Plans ──────────────────────────────────────────────────
    async def list_plans(self) -> list[Plan]:
        return await self._plans.list_active()

    async def create_plan(self, data: dict[str, Any], actor: uuid.UUID) -> Plan:
        if await self._plans.get_by_code(data["code"]) is not None:
            raise ValidationFailedError("A plan with this code already exists",
                                        details={"code": data["code"]})
        plan = Plan(**data)
        self._plans.add(plan)
        await self._plans.flush()
        self._audit.record(action="commerce.plan_created", actor_user_id=actor,
                           entity_type="plan", entity_id=plan.id,
                           meta={"code": plan.code})
        return plan

    # ── Checkout & portal ──────────────────────────────────────
    async def start_checkout(self, *, user_id: uuid.UUID, email: str,
                             plan_code: str, success_url: str,
                             cancel_url: str) -> str:
        plan = await self._plans.get_by_code(plan_code)
        if plan is None or not plan.active:
            raise NotFoundError("Plan not found", details={"code": plan_code})
        customer = await self._ensure_customer(user_id=user_id, email=email)
        session = await self._gateway.create_checkout_session(
            customer_id=customer.stripe_customer_id,
            price_id=plan.stripe_price_id or plan.code,
            plan_code=plan.code,
            user_id=user_id,
            trial_days=plan.trial_days,
            success_url=success_url,
            cancel_url=cancel_url,
        )
        self._audit.record(action="commerce.checkout_started", actor_user_id=user_id,
                           meta={"plan": plan.code, "session": session.id})
        return session.url

    async def portal_url(self, *, user_id: uuid.UUID, return_url: str) -> str:
        customer = await self._customers.get_for_user(user_id)
        if customer is None:
            raise ValidationFailedError(
                "No billing profile yet — start a subscription first"
            )
        return await self._gateway.create_portal_session(
            customer_id=customer.stripe_customer_id, return_url=return_url
        )

    async def _ensure_customer(self, *, user_id: uuid.UUID,
                               email: str) -> BillingCustomer:
        existing = await self._customers.get_for_user(user_id)
        if existing is not None:
            return existing
        stripe_id = await self._gateway.create_customer(email=email, user_id=user_id)
        customer = BillingCustomer(user_id=user_id, stripe_customer_id=stripe_id)
        self._customers.add(customer)
        await self._customers.flush()
        return customer

    # ── Entitlement & reads ────────────────────────────────────
    async def entitlement(self, user_id: uuid.UUID) -> dict[str, Any]:
        subscription = await self._subscriptions.latest_for_user(user_id)
        trial = await self._trials.get_for_user(user_id)

        if subscription is not None and subscription.status in PREMIUM_STATUSES:
            return {"premium": True, "source": "subscription",
                    "subscription": subscription, "trial": trial}

        if await self._has_assigned_license(user_id):
            return {"premium": True, "source": "license",
                    "subscription": subscription, "trial": trial}

        if trial is not None and self._trial_is_active(trial):
            return {"premium": True, "source": "trial",
                    "subscription": subscription, "trial": trial}

        return {"premium": False, "source": "none", "subscription": subscription,
                "trial": trial}

    @staticmethod
    def _trial_is_active(trial: Trial) -> bool:
        now = datetime.now(UTC)
        expires = _aware(trial.expires_at)
        return trial.status == "active" and expires is not None and expires > now

    async def _has_assigned_license(self, user_id: uuid.UUID) -> bool:
        assignments = await self._assignments.active_for_user(user_id)
        if not assignments:
            return False
        now = datetime.now(UTC)
        for assignment in assignments:
            license_ = await self._licenses.get(assignment.license_id)
            if license_.status != "active":
                continue
            expires = _aware(license_.expires_at)
            if expires is None or expires > now:
                return True
        return False

    async def my_payments(self, user_id: uuid.UUID) -> list[Payment]:
        return await self._payments.list_for_user(user_id)

    # ── Trials ───────────────────────────────────────────────────
    async def start_trial(self, user_id: uuid.UUID) -> Trial:
        settings = get_settings()
        if not settings.trial_enabled:
            raise ValidationFailedError("Free trials are not currently available")
        if await self._trials.get_for_user(user_id) is not None:
            # Existence alone means "already used" — the backend, not client
            # state, is authoritative, so this can't be bypassed by retrying
            # with a fresh browser session or clearing local storage.
            raise ValidationFailedError("You've already used your free trial")
        now = datetime.now(UTC)
        trial = Trial(
            user_id=user_id, started_at=now,
            expires_at=now + timedelta(days=settings.trial_duration_days),
        )
        self._trials.add(trial)
        await self._trials.flush()
        self._audit.record(action="commerce.trial_started", actor_user_id=user_id,
                           entity_type="trial", entity_id=trial.id)
        return trial

    # ── Refunds (admin-initiated, webhook-confirmed) ───────────
    async def request_refund(self, *, payment_id: uuid.UUID,
                             amount_cents: int | None, actor: uuid.UUID) -> str:
        payment = await self._payments.get(payment_id)
        if payment.status != "succeeded":
            raise ValidationFailedError("Only succeeded payments can be refunded",
                                        details={"status": payment.status})
        refund_id = await self._gateway.refund_payment(
            payment_intent_id=payment.stripe_payment_intent_id,
            amount_cents=amount_cents,
        )
        self._audit.record(action="commerce.refund_requested", actor_user_id=actor,
                           entity_type="payment", entity_id=payment.id,
                           meta={"refund": refund_id,
                                 "amount_cents": amount_cents})
        return refund_id

    # ── Enterprise licensing ───────────────────────────────────
    async def create_license(self, data: dict[str, Any],
                             actor: uuid.UUID) -> EnterpriseLicense:
        license_ = EnterpriseLicense(**data)
        self._licenses.add(license_)
        await self._licenses.flush()
        self._audit.record(action="commerce.license_created", actor_user_id=actor,
                           entity_type="enterprise_license", entity_id=license_.id)
        return license_

    async def list_licenses(self, *, limit: int, offset: int) -> list[EnterpriseLicense]:
        return await self._licenses.list(limit=limit, offset=offset)

    # ── Seat allocation (org-admin actions; authorization enforced by the
    # caller — see organizations.deps.OrgAdmin) ─────────────────────────
    async def assign_license(
        self, *, organization_id: uuid.UUID, license_id: uuid.UUID,
        target_user_id: uuid.UUID, actor: uuid.UUID,
    ) -> LicenseAssignment:
        """Transactional seat allocation: row-locks the license for the rest
        of this DB transaction (get_locked -> SELECT ... FOR UPDATE), so two
        concurrent requests against the same license serialize instead of
        both observing "one seat free" and both succeeding."""
        license_ = await self._licenses.get_locked(license_id)
        if license_.organization_id != organization_id:
            raise NotFoundError("License not found")
        if license_.status != "active":
            raise ValidationFailedError("This license is not active")
        if await self._assignments.get_active(license_id, target_user_id) is not None:
            raise ValidationFailedError("This member already has a seat on this license")
        active_count = await self._assignments.count_active_for_license(license_id)
        if active_count >= license_.seats:
            raise ValidationFailedError("No seats available on this license",
                                        details={"seats": license_.seats})
        assignment = LicenseAssignment(
            license_id=license_id, user_id=target_user_id, assigned_by=actor,
        )
        self._assignments.add(assignment)
        await self._assignments.flush()
        self._audit.record(action="commerce.license_assigned", actor_user_id=actor,
                           entity_type="license_assignment", entity_id=assignment.id,
                           meta={"license_id": str(license_id),
                                 "user_id": str(target_user_id)})
        await self.emit(DomainEvent(name=ev.SUBSCRIPTION_CHANGED, user_id=target_user_id,
                                    payload={"status": "license_assigned"}))
        return assignment

    async def revoke_license_assignment(
        self, *, organization_id: uuid.UUID, assignment_id: uuid.UUID, actor: uuid.UUID,
    ) -> None:
        assignment = await self._assignments.get(assignment_id)
        license_ = await self._licenses.get(assignment.license_id)
        if license_.organization_id != organization_id:
            raise NotFoundError("Assignment not found")
        if assignment.status != "active":
            raise ValidationFailedError("This assignment is already revoked")
        assignment.status = "revoked"
        assignment.revoked_at = datetime.now(UTC)
        assignment.revoked_by = actor
        await self._assignments.flush()
        self._audit.record(action="commerce.license_revoked", actor_user_id=actor,
                           entity_type="license_assignment", entity_id=assignment.id)
        await self.emit(DomainEvent(name=ev.SUBSCRIPTION_CHANGED, user_id=assignment.user_id,
                                    payload={"status": "license_revoked"}))

    async def revoke_all_assignments_for_user_in_org(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID, actor: uuid.UUID | None,
    ) -> None:
        """Called when a member is removed from an organization — a removed
        member can never retain access through a license they no longer
        qualify for."""
        licenses = await self._licenses.list_active_for_org(organization_id)
        for license_ in licenses:
            assignment = await self._assignments.get_active(license_.id, user_id)
            if assignment is not None:
                assignment.status = "revoked"
                assignment.revoked_at = datetime.now(UTC)
                assignment.revoked_by = actor
        await self._assignments.flush()

    async def org_license_summary(self, organization_id: uuid.UUID) -> dict[str, Any]:
        licenses = await self._licenses.list_active_for_org(organization_id)
        license_ids = [lic.id for lic in licenses]
        assignments = await self._assignments.list_active_for_licenses(license_ids)
        seats_purchased = sum(lic.seats for lic in licenses)
        seats_assigned = len(assignments)
        return {
            "organization_id": organization_id,
            "seats_purchased": seats_purchased,
            "seats_assigned": seats_assigned,
            "seats_available": max(seats_purchased - seats_assigned, 0),
            "licenses": licenses,
            "assignments": assignments,
        }

    async def licensed_user_ids_for_org(self, organization_id: uuid.UUID) -> set[uuid.UUID]:
        licenses = await self._licenses.list_active_for_org(organization_id)
        assignments = await self._assignments.list_active_for_licenses(
            [lic.id for lic in licenses]
        )
        return {a.user_id for a in assignments}

    # ── Organization self-serve seat billing ────────────────────
    async def start_org_checkout(
        self, *, organization_id: uuid.UUID, plan_code: str, seats: int,
        success_url: str, cancel_url: str, actor: uuid.UUID,
    ) -> str:
        plan = await self._plans.get_by_code(plan_code)
        if plan is None or not plan.active or plan.kind != "organization":
            raise NotFoundError("Organization plan not found", details={"code": plan_code})
        customer = await self._ensure_org_customer(organization_id=organization_id,
                                                    actor=actor)
        session = await self._gateway.create_checkout_session(
            customer_id=customer.stripe_customer_id,
            price_id=plan.stripe_price_id or plan.code,
            plan_code=plan.code, user_id=actor, trial_days=plan.trial_days,
            success_url=success_url, cancel_url=cancel_url, quantity=seats,
            metadata={"kind": "organization", "organization_id": str(organization_id)},
        )
        self._audit.record(action="commerce.org_checkout_started", actor_user_id=actor,
                           entity_type="organization", entity_id=organization_id,
                           meta={"plan": plan.code, "seats": seats})
        return session.url

    async def _ensure_org_customer(
        self, *, organization_id: uuid.UUID, actor: uuid.UUID,
    ) -> OrganizationBillingCustomer:
        existing = await self._org_customers.get_for_org(organization_id)
        if existing is not None:
            return existing
        actor_user = await self._users.get(actor)
        # Reuses create_customer's (email, tag) signature; the tag is only
        # used for a deterministic fake id / Stripe metadata, not a real
        # personal-account reference.
        stripe_id = await self._gateway.create_customer(
            email=actor_user.email, user_id=organization_id,
        )
        customer = OrganizationBillingCustomer(
            organization_id=organization_id, stripe_customer_id=stripe_id,
        )
        self._org_customers.add(customer)
        await self._org_customers.flush()
        return customer

    async def change_org_seats(
        self, *, organization_id: uuid.UUID, new_seat_count: int, actor: uuid.UUID,
    ) -> None:
        licenses = await self._licenses.list_active_for_org(organization_id)
        stripe_licenses = [lic for lic in licenses if lic.stripe_subscription_id]
        if not stripe_licenses:
            raise ValidationFailedError(
                "This organization has no self-serve seat subscription to change"
            )
        summary = await self.org_license_summary(organization_id)
        if new_seat_count < summary["seats_assigned"]:
            raise ValidationFailedError(
                "Cannot reduce seats below the number currently assigned",
                details={"seats_assigned": summary["seats_assigned"]},
            )
        license_ = stripe_licenses[0]
        # Local seats is not updated here — the webhook remains the sole
        # writer of subscription state, consistent with the rest of this file.
        await self._gateway.update_subscription_quantity(
            subscription_id=license_.stripe_subscription_id, quantity=new_seat_count,
        )
        self._audit.record(action="commerce.org_seats_change_requested",
                           actor_user_id=actor, entity_type="enterprise_license",
                           entity_id=license_.id, meta={"requested_seats": new_seat_count})

    # ── Webhooks ───────────────────────────────────────────────
    async def handle_webhook(self, payload: bytes, signature: str) -> None:
        event = self._gateway.verify_webhook(payload, signature)
        event_id = str(event.get("id", ""))
        event_type = str(event.get("type", ""))
        if not event_id:
            raise ValidationFailedError("Webhook event missing id")
        if await self._webhooks.seen(event_id):
            return  # at-least-once delivery: replay is a no-op
        data = (event.get("data") or {}).get("object") or {}

        handler = {
            "checkout.session.completed": self._on_checkout_completed,
            "customer.subscription.created": self._on_subscription_upsert,
            "customer.subscription.updated": self._on_subscription_upsert,
            "customer.subscription.deleted": self._on_subscription_deleted,
            "invoice.paid": self._on_invoice_paid,
            "invoice.payment_failed": self._on_invoice_failed,
            "charge.refunded": self._on_charge_refunded,
        }.get(event_type)
        if handler is not None:
            await handler(data)
        else:
            log.info("webhook_ignored", type=event_type)

        self._webhooks.add(WebhookEvent(stripe_event_id=event_id, type=event_type,
                                        payload=event))
        await self._webhooks.flush()

    async def _resolve_user_id(self, data: dict[str, Any]) -> uuid.UUID | None:
        customer_id = str(data.get("customer", "") or "")
        if customer_id:
            customer = await self._customers.get_by_stripe_id(customer_id)
            if customer is not None:
                return customer.user_id
        reference = data.get("client_reference_id")
        if reference:
            try:
                return uuid.UUID(str(reference))
            except ValueError:
                return None
        return None

    @staticmethod
    def _is_org_event(data: dict[str, Any]) -> bool:
        return str((data.get("metadata") or {}).get("kind") or "") == "organization"

    @staticmethod
    def _org_id_from_metadata(data: dict[str, Any]) -> uuid.UUID | None:
        raw = (data.get("metadata") or {}).get("organization_id")
        if not raw:
            return None
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None

    async def _on_checkout_completed(self, data: dict[str, Any]) -> None:
        stripe_sub_id = str(data.get("subscription", "") or "")
        if not stripe_sub_id:
            log.warning("checkout_completed_unresolvable")
            return
        if self._is_org_event(data):
            org_id = self._org_id_from_metadata(data)
            if org_id is None:
                log.warning("org_checkout_completed_unresolvable")
                return
            if await self._licenses.get_by_stripe_subscription_id(stripe_sub_id) is None:
                self._licenses.add(EnterpriseLicense(
                    organization_id=org_id, stripe_subscription_id=stripe_sub_id,
                    status=SUB_INCOMPLETE, seats=1,
                ))
                await self._licenses.flush()
            return
        user_id = await self._resolve_user_id(data)
        if user_id is None:
            log.warning("checkout_completed_unresolvable")
            return
        if await self._subscriptions.get_by_stripe_id(stripe_sub_id) is None:
            self._subscriptions.add(Subscription(
                user_id=user_id, stripe_subscription_id=stripe_sub_id,
                status=SUB_INCOMPLETE,
            ))
            await self._subscriptions.flush()

    async def _on_subscription_upsert(self, data: dict[str, Any]) -> None:
        stripe_sub_id = str(data.get("id", "") or "")
        if not stripe_sub_id:
            return
        if self._is_org_event(data):
            await self._on_org_subscription_upsert(stripe_sub_id, data)
            return

        subscription = await self._subscriptions.get_by_stripe_id(stripe_sub_id)
        if subscription is None:
            user_id = await self._resolve_user_id(data)
            if user_id is None:
                log.warning("subscription_event_unresolvable", sub=stripe_sub_id)
                return
            subscription = Subscription(user_id=user_id,
                                        stripe_subscription_id=stripe_sub_id)
            self._subscriptions.add(subscription)
        plan_code = str(((data.get("metadata") or {}).get("plan_code")) or "")
        if plan_code and subscription.plan_id is None:
            plan = await self._plans.get_by_code(plan_code)
            if plan is not None:
                subscription.plan_id = plan.id
        subscription.status = str(data.get("status", subscription.status))
        subscription.current_period_end = _from_unix(data.get("current_period_end"))
        subscription.trial_end = _from_unix(data.get("trial_end"))
        subscription.cancel_at_period_end = bool(data.get("cancel_at_period_end", False))
        await self._subscriptions.flush()
        await self.emit(DomainEvent(name=ev.SUBSCRIPTION_CHANGED,
                                    user_id=subscription.user_id,
                                    payload={"status": subscription.status}))

    async def _on_org_subscription_upsert(self, stripe_sub_id: str,
                                          data: dict[str, Any]) -> None:
        """Org seat subscriptions project onto EnterpriseLicense instead of
        Subscription: seats = the Stripe subscription's line-item quantity,
        kept in sync exclusively by this webhook, same discipline as
        personal subscriptions."""
        license_ = await self._licenses.get_by_stripe_subscription_id(stripe_sub_id)
        if license_ is None:
            org_id = self._org_id_from_metadata(data)
            if org_id is None:
                log.warning("org_subscription_event_unresolvable", sub=stripe_sub_id)
                return
            license_ = EnterpriseLicense(organization_id=org_id,
                                         stripe_subscription_id=stripe_sub_id)
            self._licenses.add(license_)
        plan_code = str(((data.get("metadata") or {}).get("plan_code")) or "")
        if plan_code and license_.plan_id is None:
            plan = await self._plans.get_by_code(plan_code)
            if plan is not None:
                license_.plan_id = plan.id
        license_.status = str(data.get("status", license_.status))
        license_.seats = int(data.get("quantity", license_.seats) or license_.seats)
        license_.current_period_end = _from_unix(data.get("current_period_end"))
        license_.trial_end = _from_unix(data.get("trial_end"))
        license_.cancel_at_period_end = bool(data.get("cancel_at_period_end", False))
        await self._licenses.flush()
        await self.emit(DomainEvent(name=ev.SUBSCRIPTION_CHANGED,
                                    payload={"organization_id": str(license_.organization_id),
                                             "status": license_.status}))

    async def _on_subscription_deleted(self, data: dict[str, Any]) -> None:
        stripe_sub_id = str(data.get("id", "") or "")
        if self._is_org_event(data):
            license_ = await self._licenses.get_by_stripe_subscription_id(stripe_sub_id)
            if license_ is None:
                return
            license_.status = SUB_CANCELED
            await self._licenses.flush()
            await self.emit(DomainEvent(
                name=ev.SUBSCRIPTION_CHANGED,
                payload={"organization_id": str(license_.organization_id),
                         "status": SUB_CANCELED}))
            return
        subscription = await self._subscriptions.get_by_stripe_id(stripe_sub_id)
        if subscription is None:
            return
        subscription.status = SUB_CANCELED
        await self._subscriptions.flush()
        await self.emit(DomainEvent(name=ev.SUBSCRIPTION_CHANGED,
                                    user_id=subscription.user_id,
                                    payload={"status": SUB_CANCELED}))

    async def _on_invoice_paid(self, data: dict[str, Any]) -> None:
        user_id = await self._resolve_user_id(data)
        if user_id is None:
            log.warning("invoice_paid_unresolvable")
            return
        intent = str(data.get("payment_intent") or f"inv:{data.get('id', '')}")
        if await self._payments.get_by_intent(intent) is not None:
            return
        self._payments.add(Payment(
            user_id=user_id,
            stripe_payment_intent_id=intent,
            stripe_invoice_id=str(data.get("id", "") or ""),
            amount_cents=int(data.get("amount_paid", 0) or 0),
            currency=str(data.get("currency", "usd") or "usd"),
            status="succeeded",
            description="Subscription payment",
        ))
        await self._payments.flush()
        await self.emit(DomainEvent(name=ev.PURCHASE_COMPLETED, user_id=user_id,
                                    payload={"amount_cents":
                                             int(data.get("amount_paid", 0) or 0)}))

    async def _on_invoice_failed(self, data: dict[str, Any]) -> None:
        stripe_sub_id = str(data.get("subscription", "") or "")
        if not stripe_sub_id:
            return
        subscription = await self._subscriptions.get_by_stripe_id(stripe_sub_id)
        if subscription is not None:
            subscription.status = SUB_PAST_DUE
            await self._subscriptions.flush()
            await self.emit(DomainEvent(name=ev.SUBSCRIPTION_CHANGED,
                                        user_id=subscription.user_id,
                                        payload={"status": SUB_PAST_DUE}))
            return
        license_ = await self._licenses.get_by_stripe_subscription_id(stripe_sub_id)
        if license_ is not None:
            license_.status = SUB_PAST_DUE
            await self._licenses.flush()
            await self.emit(DomainEvent(
                name=ev.SUBSCRIPTION_CHANGED,
                payload={"organization_id": str(license_.organization_id),
                         "status": SUB_PAST_DUE}))

    async def _on_charge_refunded(self, data: dict[str, Any]) -> None:
        intent = str(data.get("payment_intent", "") or "")
        payment = await self._payments.get_by_intent(intent) if intent else None
        if payment is None:
            return
        payment.status = "refunded"
        payment.refunded_amount_cents = int(data.get("amount_refunded", 0) or 0)
        await self._payments.flush()
        await self.emit(DomainEvent(name=ev.PAYMENT_REFUNDED, user_id=payment.user_id,
                                    payload={"amount_cents":
                                             payment.refunded_amount_cents}))


def build_commerce_service(session, event_bus: EventBus) -> CommerceService:
    """Composition helper (module boundary rule)."""
    from app.modules.commerce.gateway import get_gateway
    from app.modules.users.service import build_user_service

    return CommerceService(
        plans=PlanRepository(session),
        customers=BillingCustomerRepository(session),
        subscriptions=SubscriptionRepository(session),
        payments=PaymentRepository(session),
        webhooks=WebhookEventRepository(session),
        licenses=EnterpriseLicenseRepository(session),
        assignments=LicenseAssignmentRepository(session),
        trials=TrialRepository(session),
        org_customers=OrganizationBillingCustomerRepository(session),
        gateway=get_gateway(),
        users=build_user_service(session, event_bus),
        audit=AuditService(session),
        event_bus=event_bus,
    )
