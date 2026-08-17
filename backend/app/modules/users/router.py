from fastapi import APIRouter, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Bus, CurrentUser, DbSession, require_permission
from app.events.bus import EventBus
from app.modules.users.audit import AuditService
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserOut, UserPage, UserUpdate
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def _service(session: AsyncSession, bus: EventBus) -> UserService:
    return UserService(UserRepository(session), AuditService(session), bus)


@router.get("/me", response_model=UserOut)
async def get_me(current_user: CurrentUser) -> UserOut:
    return UserOut.model_validate(current_user)


@router.get("/me/permissions", response_model=list[str])
async def my_permissions(current_user: CurrentUser, session: DbSession) -> list[str]:
    """The caller's effective permission codes — lets clients gate UI
    (e.g. Creator Studio) without a failed-request probe. Authorization is
    still enforced server-side on every route."""
    codes = await UserRepository(session).get_permission_codes(current_user.id)
    return sorted(codes)


@router.patch("/me", response_model=UserOut)
async def update_me(
    patch: UserUpdate, current_user: CurrentUser, session: DbSession, bus: Bus
) -> UserOut:
    user = await _service(session, bus).update_profile(current_user.id, patch)
    return UserOut.model_validate(user)


@router.delete("/me", status_code=204)
async def delete_me(
    request: Request, current_user: CurrentUser, session: DbSession, bus: Bus
) -> None:
    await _service(session, bus).delete_account(
        current_user.id, ip_address=request.client.host if request.client else None
    )


@router.get("", response_model=UserPage, dependencies=[require_permission("users.read")])
async def list_users(
    session: DbSession,
    bus: Bus,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserPage:
    users = await _service(session, bus).list_users(limit=limit, offset=offset)
    return UserPage(items=[UserOut.model_validate(u) for u in users], limit=limit, offset=offset)
