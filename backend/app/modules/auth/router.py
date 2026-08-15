import secrets

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Bus, DbSession
from app.core.errors import ValidationFailedError
from app.core.redis import get_redis
from app.events.bus import EventBus
from app.modules.auth.repository import (
    ActionTokenRepository,
    OAuthAccountRepository,
    RefreshTokenRepository,
)
from app.modules.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    OAuthAuthorizeOut,
    OAuthCallbackRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    VerifyEmailRequest,
)
from app.modules.auth.service import AuthService
from app.modules.users.audit import AuditService
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_TTL_SECONDS = 600


def _service(session: AsyncSession, bus: EventBus) -> AuthService:
    return AuthService(
        users=UserRepository(session),
        refresh_tokens=RefreshTokenRepository(session),
        action_tokens=ActionTokenRepository(session),
        oauth_accounts=OAuthAccountRepository(session),
        audit=AuditService(session),
        event_bus=bus,
    )


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterRequest, session: DbSession, bus: Bus) -> UserOut:
    user = await _service(session, bus).register(
        email=body.email, password=body.password, display_name=body.display_name
    )
    if body.invitation_token:
        # Same transaction as registration: an invalid/expired token rolls
        # the whole request back rather than leaving an orphaned account.
        from app.modules.organizations.service import build_organization_service

        await build_organization_service(session, bus).accept_invitation(
            raw_token=body.invitation_token, accepting_user=user,
        )
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, request: Request, session: DbSession, bus: Bus) -> TokenPair:
    return await _service(session, bus).login(
        email=body.email,
        password=body.password,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: DbSession, bus: Bus) -> TokenPair:
    return await _service(session, bus).refresh(body.refresh_token)


@router.post("/logout", status_code=204)
async def logout(body: LogoutRequest, session: DbSession, bus: Bus) -> None:
    await _service(session, bus).logout(body.refresh_token)


@router.post("/verify-email", status_code=204)
async def verify_email(body: VerifyEmailRequest, session: DbSession, bus: Bus) -> None:
    await _service(session, bus).verify_email(body.token)


@router.post("/password-reset/request", status_code=202)
async def request_password_reset(
    body: PasswordResetRequest, session: DbSession, bus: Bus
) -> dict[str, str]:
    await _service(session, bus).request_password_reset(body.email)
    return {"status": "accepted"}  # same response whether or not the email exists


@router.post("/password-reset/confirm", status_code=204)
async def confirm_password_reset(
    body: PasswordResetConfirm, session: DbSession, bus: Bus
) -> None:
    await _service(session, bus).confirm_password_reset(
        token=body.token, new_password=body.new_password
    )


@router.get("/oauth/{provider}/authorize", response_model=OAuthAuthorizeOut)
async def oauth_authorize(
    provider: str, redirect_uri: str, session: DbSession, bus: Bus
) -> OAuthAuthorizeOut:
    state = secrets.token_urlsafe(24)
    await get_redis().set(f"oauth:state:{state}", provider, ex=_OAUTH_STATE_TTL_SECONDS)
    url = _service(session, bus).oauth_authorization_url(
        provider, state=state, redirect_uri=redirect_uri
    )
    return OAuthAuthorizeOut(authorization_url=url, state=state)


@router.post("/oauth/{provider}/callback", response_model=TokenPair)
async def oauth_callback(
    provider: str, body: OAuthCallbackRequest, session: DbSession, bus: Bus
) -> TokenPair:
    redis = get_redis()
    stored = await redis.getdel(f"oauth:state:{body.state}")
    if stored != provider:
        raise ValidationFailedError("Invalid or expired OAuth state")
    return await _service(session, bus).oauth_login(
        provider, code=body.code, redirect_uri=body.redirect_uri
    )
