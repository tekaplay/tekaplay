import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.modules.users.models import (
    Organization,
    OrganizationInvitation,
    OrganizationMember,
)
from app.repositories.base import BaseRepository

OWNER = "owner"
ADMIN = "admin"
MEMBER = "member"
ADMIN_ROLES = {OWNER, ADMIN}


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization

    async def get_by_slug(self, slug: str) -> Organization | None:
        stmt = self._base_query().where(Organization.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def slug_taken(self, slug: str) -> bool:
        return await self.get_by_slug(slug) is not None


class OrganizationMemberRepository(BaseRepository[OrganizationMember]):
    model = OrganizationMember

    async def get_membership(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        stmt = select(OrganizationMember).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.user_id == user_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(self, organization_id: uuid.UUID) -> list[OrganizationMember]:
        stmt = (
            select(OrganizationMember)
            .where(OrganizationMember.organization_id == organization_id)
            .order_by(OrganizationMember.created_at)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationMember]:
        stmt = select(OrganizationMember).where(OrganizationMember.user_id == user_id)
        return list((await self.session.execute(stmt)).scalars())

    async def count_owners(self, organization_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.role == OWNER,
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def delete(self, member: OrganizationMember) -> None:
        # Hard delete: membership is a pure join row, not an audited entity in
        # its own right — the AuditLog entry recorded by the service is the
        # durable record of "who removed whom and when".
        await self.session.delete(member)


class OrganizationInvitationRepository(BaseRepository[OrganizationInvitation]):
    model = OrganizationInvitation

    async def get_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None:
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.token_hash == token_hash
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_pending_for_email(
        self, organization_id: uuid.UUID, email: str
    ) -> OrganizationInvitation | None:
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.email == email,
            OrganizationInvitation.status == "pending",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_org(self, organization_id: uuid.UUID) -> list[OrganizationInvitation]:
        stmt = (
            select(OrganizationInvitation)
            .where(OrganizationInvitation.organization_id == organization_id)
            .order_by(OrganizationInvitation.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())


def is_expired(expires_at: datetime, now: datetime) -> bool:
    if expires_at.tzinfo is None:  # SQLite returns naive datetimes
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < now
