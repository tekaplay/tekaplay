import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.modules.commerce.models import (
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
from app.repositories.base import BaseRepository


class PlanRepository(BaseRepository[Plan]):
    model = Plan

    async def get_by_code(self, code: str) -> Plan | None:
        stmt = self._base_query().where(Plan.code == code)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active(self) -> list[Plan]:
        stmt = self._base_query().where(Plan.active.is_(True)).order_by(Plan.price_cents)
        return list((await self.session.execute(stmt)).scalars())


class BillingCustomerRepository(BaseRepository[BillingCustomer]):
    model = BillingCustomer

    async def get_for_user(self, user_id: uuid.UUID) -> BillingCustomer | None:
        stmt = select(BillingCustomer).where(BillingCustomer.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_stripe_id(self, stripe_customer_id: str) -> BillingCustomer | None:
        stmt = select(BillingCustomer).where(
            BillingCustomer.stripe_customer_id == stripe_customer_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class SubscriptionRepository(BaseRepository[Subscription]):
    model = Subscription

    async def get_by_stripe_id(self, stripe_subscription_id: str) -> Subscription | None:
        stmt = select(Subscription).where(
            Subscription.stripe_subscription_id == stripe_subscription_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_for_user(self, user_id: uuid.UUID) -> Subscription | None:
        stmt = (select(Subscription)
                .where(Subscription.user_id == user_id)
                .order_by(Subscription.created_at.desc())
                .limit(1))
        return (await self.session.execute(stmt)).scalar_one_or_none()


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_by_intent(self, payment_intent_id: str) -> Payment | None:
        stmt = select(Payment).where(
            Payment.stripe_payment_intent_id == payment_intent_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Payment]:
        stmt = (select(Payment)
                .where(Payment.user_id == user_id)
                .order_by(Payment.created_at.desc()))
        return list((await self.session.execute(stmt)).scalars())


class WebhookEventRepository(BaseRepository[WebhookEvent]):
    model = WebhookEvent

    async def seen(self, stripe_event_id: str) -> bool:
        stmt = select(WebhookEvent.id).where(
            WebhookEvent.stripe_event_id == stripe_event_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None


def _not_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:  # SQLite naive
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > now


class EnterpriseLicenseRepository(BaseRepository[EnterpriseLicense]):
    model = EnterpriseLicense

    async def active_for_organizations(
        self, organization_ids: list[uuid.UUID]
    ) -> EnterpriseLicense | None:
        if not organization_ids:
            return None
        now = datetime.now(UTC)
        stmt = self._base_query().where(
            EnterpriseLicense.organization_id.in_(organization_ids),
            EnterpriseLicense.status == "active",
        )
        for license_ in (await self.session.execute(stmt)).scalars():
            if _not_expired(license_.expires_at, now):
                return license_
        return None

    async def list_active_for_org(self, organization_id: uuid.UUID) -> list[EnterpriseLicense]:
        now = datetime.now(UTC)
        stmt = self._base_query().where(
            EnterpriseLicense.organization_id == organization_id,
            EnterpriseLicense.status == "active",
        )
        return [lic for lic in (await self.session.execute(stmt)).scalars()
                if _not_expired(lic.expires_at, now)]

    async def get_locked(self, license_id: uuid.UUID) -> EnterpriseLicense:
        """Row-locks the license for the duration of the caller's transaction
        so two simultaneous assignment requests against the same license
        can't both observe "one seat free" and both succeed."""
        stmt = self._base_query().where(
            EnterpriseLicense.id == license_id
        ).with_for_update()
        result = await self.session.execute(stmt)
        license_ = result.scalar_one_or_none()
        if license_ is None:
            from app.core.errors import NotFoundError
            raise NotFoundError("License not found", details={"id": str(license_id)})
        return license_

    async def get_by_stripe_subscription_id(
        self, stripe_subscription_id: str
    ) -> EnterpriseLicense | None:
        stmt = select(EnterpriseLicense).where(
            EnterpriseLicense.stripe_subscription_id == stripe_subscription_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class LicenseAssignmentRepository(BaseRepository[LicenseAssignment]):
    model = LicenseAssignment

    async def count_active_for_license(self, license_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            LicenseAssignment.license_id == license_id,
            LicenseAssignment.status == "active",
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_active(
        self, license_id: uuid.UUID, user_id: uuid.UUID
    ) -> LicenseAssignment | None:
        stmt = select(LicenseAssignment).where(
            LicenseAssignment.license_id == license_id,
            LicenseAssignment.user_id == user_id,
            LicenseAssignment.status == "active",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_active_for_licenses(
        self, license_ids: list[uuid.UUID]
    ) -> list[LicenseAssignment]:
        if not license_ids:
            return []
        stmt = select(LicenseAssignment).where(
            LicenseAssignment.license_id.in_(license_ids),
            LicenseAssignment.status == "active",
        )
        return list((await self.session.execute(stmt)).scalars())

    async def active_for_user(self, user_id: uuid.UUID) -> list[LicenseAssignment]:
        stmt = select(LicenseAssignment).where(
            LicenseAssignment.user_id == user_id,
            LicenseAssignment.status == "active",
        )
        return list((await self.session.execute(stmt)).scalars())


class TrialRepository(BaseRepository[Trial]):
    model = Trial

    async def get_for_user(self, user_id: uuid.UUID) -> Trial | None:
        stmt = select(Trial).where(Trial.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class OrganizationBillingCustomerRepository(BaseRepository[OrganizationBillingCustomer]):
    model = OrganizationBillingCustomer

    async def get_for_org(self, organization_id: uuid.UUID) -> OrganizationBillingCustomer | None:
        stmt = select(OrganizationBillingCustomer).where(
            OrganizationBillingCustomer.organization_id == organization_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_stripe_id(
        self, stripe_customer_id: str
    ) -> OrganizationBillingCustomer | None:
        stmt = select(OrganizationBillingCustomer).where(
            OrganizationBillingCustomer.stripe_customer_id == stripe_customer_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
