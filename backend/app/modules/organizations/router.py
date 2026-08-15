import uuid

from fastapi import APIRouter, Response

from app.api.deps import Bus, CurrentUser, DbSession
from app.modules.organizations.deps import OrgAdmin, OrgMember, OrgService
from app.modules.organizations.schemas import (
    InvitationAcceptRequest,
    InvitationCreate,
    InvitationOut,
    InvitationPreviewOut,
    OrganizationCreate,
    OrganizationMemberOut,
    OrganizationOut,
)

router = APIRouter(tags=["organizations"])


@router.post("/organizations", response_model=OrganizationOut, status_code=201)
async def create_organization(
    body: OrganizationCreate, current_user: CurrentUser, service: OrgService,
) -> OrganizationOut:
    org = await service.create_organization(
        name=body.name, org_type=body.org_type, owner_id=current_user.id,
    )
    return OrganizationOut.model_validate(org)


@router.get("/organizations/me", response_model=list[OrganizationOut])
async def my_organizations(current_user: CurrentUser, service: OrgService) -> list[OrganizationOut]:
    orgs = await service.list_my_organizations(current_user.id)
    return [OrganizationOut.model_validate(org) for org in orgs]


@router.get("/organizations/{organization_id}", response_model=OrganizationOut)
async def get_organization(
    organization_id: uuid.UUID, _: OrgMember, service: OrgService,
) -> OrganizationOut:
    org = await service.get_org_or_404(organization_id)
    return OrganizationOut.model_validate(org)


@router.get("/organizations/{organization_id}/members",
           response_model=list[OrganizationMemberOut])
async def list_members(
    organization_id: uuid.UUID, _: OrgMember, service: OrgService,
    session: DbSession, bus: Bus,
) -> list[OrganizationMemberOut]:
    from app.modules.commerce.service import build_commerce_service

    rows = await service.list_members(organization_id)
    licensed_user_ids = await build_commerce_service(session, bus).licensed_user_ids_for_org(
        organization_id
    )
    return [
        OrganizationMemberOut(
            id=member.id, user_id=user.id, display_name=user.display_name,
            email=user.email, role=member.role,
            has_license=user.id in licensed_user_ids, joined_at=member.created_at,
        )
        for member, user in rows
    ]


@router.post("/organizations/{organization_id}/invitations", response_model=InvitationOut,
             status_code=201)
async def create_invitation(
    organization_id: uuid.UUID, body: InvitationCreate, admin: OrgAdmin, service: OrgService,
) -> InvitationOut:
    invitation, _raw_token = await service.invite_member(
        organization_id=organization_id, email=body.email, role=body.role,
        invited_by=admin.user_id,
    )
    return InvitationOut.model_validate(invitation)


@router.get("/organizations/{organization_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    organization_id: uuid.UUID, _: OrgAdmin, service: OrgService,
) -> list[InvitationOut]:
    invitations = await service.list_invitations(organization_id)
    return [InvitationOut.model_validate(i) for i in invitations]


@router.delete("/organizations/{organization_id}/invitations/{invitation_id}",
               status_code=204)
async def revoke_invitation(
    organization_id: uuid.UUID, invitation_id: uuid.UUID, admin: OrgAdmin, service: OrgService,
) -> None:
    await service.revoke_invitation(
        organization_id=organization_id, invitation_id=invitation_id, actor=admin.user_id,
    )


@router.post("/organizations/{organization_id}/members/{user_id}/remove", status_code=204)
async def remove_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, admin: OrgAdmin, service: OrgService,
) -> None:
    await service.remove_member(
        organization_id=organization_id, target_user_id=user_id, actor=admin.user_id,
    )


@router.post("/organizations/{organization_id}/leave", status_code=204)
async def leave_organization(
    organization_id: uuid.UUID, current_user: CurrentUser, service: OrgService,
) -> None:
    await service.leave_organization(organization_id=organization_id, user_id=current_user.id)


@router.get("/invitations/{token}/preview", response_model=InvitationPreviewOut)
async def preview_invitation(token: str, service: OrgService) -> InvitationPreviewOut:
    """Unauthenticated by design: lets a registration form show "you're
    invited to X" before the visitor has an account. Returns no PII beyond
    the org name."""
    from datetime import UTC, datetime

    from app.modules.organizations.repository import is_expired

    invitation, org = await service.preview_invitation(token)
    valid = (invitation.status == "pending"
             and not is_expired(invitation.expires_at, datetime.now(UTC)))
    return InvitationPreviewOut(organization_name=org.name, role=invitation.role,
                                expires_at=invitation.expires_at, valid=valid)


@router.post("/invitations/accept", status_code=204)
async def accept_invitation(
    body: InvitationAcceptRequest, current_user: CurrentUser, service: OrgService,
) -> Response:
    await service.accept_invitation(raw_token=body.token, accepting_user=current_user)
    return Response(status_code=204)
