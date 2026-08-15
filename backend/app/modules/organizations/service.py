"""Organization / membership / invitation orchestration.

Authorization for org routes is role-based (OrganizationMember.role), not the
platform RBAC system — deliberately, since require_permission's UserRole
lookup is not actually org-scoped today. get_member_or_404 returns 404 (not
403) for non-members, so a non-member can't distinguish "org doesn't exist"
from "you're not in it" (an IDOR/enumeration mitigation).
"""
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import security
from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationFailedError,
)
from app.events.bus import DomainEvent, EventBus
from app.modules.organizations import events as ev
from app.modules.organizations.repository import (
    ADMIN_ROLES,
    OWNER,
    OrganizationInvitationRepository,
    OrganizationMemberRepository,
    OrganizationRepository,
    is_expired,
)
from app.modules.users.audit import AuditService
from app.modules.users.models import (
    Organization,
    OrganizationInvitation,
    OrganizationMember,
    User,
)
from app.modules.users.repository import UserRepository
from app.services.base import BaseService

_INVITATION_TTL = timedelta(days=7)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "org"


class OrganizationService(BaseService):
    def __init__(
        self,
        organizations: OrganizationRepository,
        members: OrganizationMemberRepository,
        invitations: OrganizationInvitationRepository,
        users: UserRepository,
        audit: AuditService,
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._orgs = organizations
        self._members = members
        self._invitations = invitations
        self._users = users
        self._audit = audit

    # ── Organizations ───────────────────────────────────────────
    async def create_organization(
        self, *, name: str, org_type: str | None, owner_id: uuid.UUID
    ) -> Organization:
        slug = _slugify(name)
        candidate = slug
        suffix = 1
        while await self._orgs.slug_taken(candidate):
            suffix += 1
            candidate = f"{slug}-{suffix}"
        org = Organization(name=name, slug=candidate, org_type=org_type)
        self._orgs.add(org)
        await self._orgs.flush()
        self._members.add(OrganizationMember(
            organization_id=org.id, user_id=owner_id, role=OWNER,
        ))
        await self._members.flush()
        self._audit.record(action="organization.created", actor_user_id=owner_id,
                           entity_type="organization", entity_id=org.id)
        return org

    async def list_my_organizations(self, user_id: uuid.UUID) -> list[Organization]:
        memberships = await self._members.list_for_user(user_id)
        orgs = []
        for membership in memberships:
            org = await self._orgs.get(membership.organization_id)
            orgs.append(org)
        return orgs

    async def get_org_or_404(self, organization_id: uuid.UUID) -> Organization:
        return await self._orgs.get(organization_id)

    async def get_member_or_404(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember:
        member = await self._members.get_membership(organization_id, user_id)
        if member is None:
            raise NotFoundError("Organization not found",
                                details={"id": str(organization_id)})
        return member

    @staticmethod
    def require_admin(member: OrganizationMember) -> None:
        if member.role not in ADMIN_ROLES:
            raise PermissionDeniedError("Only organization admins can do this")

    async def list_members(
        self, organization_id: uuid.UUID
    ) -> list[tuple[OrganizationMember, User]]:
        stmt = (
            select(OrganizationMember, User)
            .join(User, User.id == OrganizationMember.user_id)
            .where(OrganizationMember.organization_id == organization_id)
            .order_by(OrganizationMember.created_at)
        )
        rows = (await self._members.session.execute(stmt)).all()
        return [(member, user) for member, user in rows]

    # ── Invitations ──────────────────────────────────────────────
    async def invite_member(
        self, *, organization_id: uuid.UUID, email: str, role: str,
        invited_by: uuid.UUID,
    ) -> tuple[OrganizationInvitation, str]:
        email = email.strip().lower()
        if role == OWNER:
            raise ValidationFailedError("Cannot invite someone directly as owner")
        existing_user = await self._users.get_by_email(email)
        if existing_user is not None:
            already_member = await self._members.get_membership(
                organization_id, existing_user.id
            )
            if already_member is not None:
                raise ConflictError("This person is already a member")
        if await self._invitations.get_pending_for_email(organization_id, email) is not None:
            raise ConflictError("An invitation is already pending for this email")

        raw_token = security.generate_opaque_token()
        invitation = OrganizationInvitation(
            organization_id=organization_id, email=email, role=role,
            token_hash=security.hash_token(raw_token), invited_by=invited_by,
            expires_at=datetime.now(UTC) + _INVITATION_TTL,
        )
        self._invitations.add(invitation)
        await self._invitations.flush()
        self._audit.record(action="organization.invitation_created",
                           actor_user_id=invited_by, entity_type="organization_invitation",
                           entity_id=invitation.id, meta={"organization_id":
                                                           str(organization_id)})
        # Raw token travels only on the event bus, never persisted or returned.
        await self.emit(DomainEvent(
            name=ev.INVITATION_CREATED,
            payload={"token": raw_token, "email": email,
                     "organization_id": str(organization_id)},
        ))
        return invitation, raw_token

    async def list_invitations(self, organization_id: uuid.UUID) -> list[OrganizationInvitation]:
        return await self._invitations.list_for_org(organization_id)

    async def revoke_invitation(
        self, *, organization_id: uuid.UUID, invitation_id: uuid.UUID, actor: uuid.UUID
    ) -> None:
        invitation = await self._invitations.get(invitation_id)
        if invitation.organization_id != organization_id:
            raise NotFoundError("Invitation not found")
        if invitation.status != "pending":
            raise ValidationFailedError("Only pending invitations can be revoked")
        invitation.status = "revoked"
        await self._invitations.flush()
        self._audit.record(action="organization.invitation_revoked", actor_user_id=actor,
                           entity_type="organization_invitation", entity_id=invitation.id)

    async def preview_invitation(
        self, raw_token: str
    ) -> tuple[OrganizationInvitation, Organization]:
        invitation = await self._invitations.get_by_token_hash(security.hash_token(raw_token))
        if invitation is None:
            raise NotFoundError("This invitation link is invalid")
        org = await self._orgs.get(invitation.organization_id)
        return invitation, org

    async def accept_invitation(
        self, *, raw_token: str, accepting_user: User
    ) -> OrganizationMember:
        invitation = await self._invitations.get_by_token_hash(security.hash_token(raw_token))
        if invitation is None:
            raise ValidationFailedError("This invitation link is invalid or has expired")
        now = datetime.now(UTC)
        if (invitation.status != "pending"
                or is_expired(invitation.expires_at, now)
                or invitation.email != accepting_user.email.strip().lower()):
            raise ValidationFailedError("This invitation link is invalid or has expired")
        existing = await self._members.get_membership(invitation.organization_id,
                                                       accepting_user.id)
        if existing is not None:
            raise ConflictError("Already a member of this organization")

        member = OrganizationMember(
            organization_id=invitation.organization_id, user_id=accepting_user.id,
            role=invitation.role, invited_by=invitation.invited_by,
        )
        self._members.add(member)
        invitation.status = "accepted"
        invitation.accepted_at = now
        invitation.accepted_by = accepting_user.id
        await self._members.flush()
        self._audit.record(action="organization.invitation_accepted",
                           actor_user_id=accepting_user.id,
                           entity_type="organization", entity_id=invitation.organization_id)
        await self.emit(DomainEvent(name=ev.MEMBER_JOINED, user_id=accepting_user.id,
                                    payload={"organization_id":
                                             str(invitation.organization_id)}))
        return member

    # ── Membership management ───────────────────────────────────
    async def remove_member(
        self, *, organization_id: uuid.UUID, target_user_id: uuid.UUID, actor: uuid.UUID
    ) -> None:
        member = await self.get_member_or_404(organization_id, target_user_id)
        if member.role == OWNER:
            raise ValidationFailedError(
                "Transfer ownership before removing the owner"
            )
        # A removed member can't retain access through a license they no
        # longer qualify for (cross-module via the commerce service
        # interface, not its models/repository — the module boundary rule).
        from app.modules.commerce.service import build_commerce_service

        await build_commerce_service(self._members.session, self._events)\
            .revoke_all_assignments_for_user_in_org(
                organization_id=organization_id, user_id=target_user_id, actor=actor,
            )
        await self._members.delete(member)
        await self._members.flush()
        self._audit.record(action="organization.member_removed", actor_user_id=actor,
                           entity_type="organization", entity_id=organization_id,
                           meta={"removed_user_id": str(target_user_id)})
        await self.emit(DomainEvent(name=ev.MEMBER_REMOVED, user_id=target_user_id,
                                    payload={"organization_id": str(organization_id)}))

    async def leave_organization(self, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> None:
        member = await self.get_member_or_404(organization_id, user_id)
        if member.role == OWNER and await self._members.count_owners(organization_id) <= 1:
            raise ValidationFailedError(
                "Transfer ownership to another member before leaving"
            )
        await self._members.delete(member)
        await self._members.flush()
        self._audit.record(action="organization.member_left", actor_user_id=user_id,
                           entity_type="organization", entity_id=organization_id)
        await self.emit(DomainEvent(name=ev.MEMBER_REMOVED, user_id=user_id,
                                    payload={"organization_id": str(organization_id)}))


def build_organization_service(session, event_bus: EventBus) -> OrganizationService:
    """Composition helper (module boundary rule)."""
    return OrganizationService(
        organizations=OrganizationRepository(session),
        members=OrganizationMemberRepository(session),
        invitations=OrganizationInvitationRepository(session),
        users=UserRepository(session),
        audit=AuditService(session),
        event_bus=event_bus,
    )
