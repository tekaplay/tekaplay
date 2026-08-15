"""Org-scoped authorization dependencies.

Deliberately separate from app.api.deps.require_permission (platform RBAC) —
see the module docstring on service.py. Every org route re-validates
membership/role server-side via these dependencies; the frontend hiding a
button is never sufficient on its own.
"""
import uuid
from typing import Annotated

from fastapi import Depends

from app.api.deps import Bus, CurrentUser, DbSession
from app.modules.organizations.service import OrganizationService, build_organization_service
from app.modules.users.models import OrganizationMember


async def get_org_service(session: DbSession, bus: Bus) -> OrganizationService:
    return build_organization_service(session, bus)


OrgService = Annotated[OrganizationService, Depends(get_org_service)]


async def get_org_member(
    organization_id: uuid.UUID, current_user: CurrentUser, service: OrgService,
) -> OrganizationMember:
    return await service.get_member_or_404(organization_id, current_user.id)


OrgMember = Annotated[OrganizationMember, Depends(get_org_member)]


async def require_org_admin(member: OrgMember) -> OrganizationMember:
    OrganizationService.require_admin(member)
    return member


OrgAdmin = Annotated[OrganizationMember, Depends(require_org_admin)]
