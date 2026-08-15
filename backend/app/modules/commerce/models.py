"""Commerce: plans, customers, subscriptions, payments, webhook ledger,
enterprise licenses.

Stripe is the billing system of record; these tables are the platform's
projection of it, maintained exclusively by verified webhooks (plus the
checkout/portal session creation calls). The webhook ledger makes
at-least-once delivery idempotent. Coupons are Stripe promotion codes applied
at checkout; hosted invoices live in the Stripe billing portal.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import PortableJSON

SUB_ACTIVE = "active"
SUB_TRIALING = "trialing"
SUB_PAST_DUE = "past_due"
SUB_CANCELED = "canceled"
SUB_INCOMPLETE = "incomplete"
PREMIUM_STATUSES = {SUB_ACTIVE, SUB_TRIALING}


class Plan(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(60), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    interval: Mapped[str] = mapped_column(String(10), nullable=False, default="month")
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stripe_price_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="individual")
    features: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict,
                                                      nullable=False)


class BillingCustomer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "billing_customers"
    __table_args__ = (UniqueConstraint("user_id", name="uq_billing_customer_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stripe_customer_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False,
                                        default=SUB_INCOMPLETE)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                       default=False)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payments"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stripe_payment_intent_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    stripe_invoice_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # succeeded|failed|refunded
    refunded_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")


class WebhookEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Idempotency ledger: a Stripe event id is processed at most once."""

    __tablename__ = "webhook_events"

    stripe_event_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(PortableJSON, nullable=False)


class EnterpriseLicense(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """Org-level seat pool. A member gets premium access only once an admin
    assigns them one of these seats (see LicenseAssignment) — membership in
    the organization alone is not enough.

    Stripe fields are null for admin-granted comped licenses (the original,
    still-supported flow) and populated for self-serve org subscriptions,
    kept in sync exclusively by verified webhooks, exactly like the personal
    Subscription model."""

    __tablename__ = "enterprise_licenses"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    seats: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), unique=True,
                                                                index=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                        default=False)
    trial_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LicenseAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A single seat handed to a specific member. Allocation is transactional
    (see CommerceService.assign_license, which row-locks the license) so two
    simultaneous requests cannot both grab the last seat. Re-assignment after
    a revoke is allowed (a new row), but a user can never hold two
    simultaneously-active assignments on the same license — enforced by a
    partial unique index on (license_id, user_id) where status='active'."""

    __tablename__ = "license_assignments"
    __table_args__ = (
        Index(
            "uq_license_assignment_active", "license_id", "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    license_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("enterprise_licenses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class Trial(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per user, ever. Existence (regardless of status) means the
    trial has been consumed — the backend, not client state, is authoritative
    on whether a user may start a new one."""

    __tablename__ = "trials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class OrganizationBillingCustomer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Mirrors BillingCustomer but keyed by organization — orgs get their own
    Stripe customer, separate from any member's personal one."""

    __tablename__ = "organization_billing_customers"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_org_billing_customer_org"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    stripe_customer_id: Mapped[str] = mapped_column(
        String(120), nullable=False, unique=True, index=True
    )
