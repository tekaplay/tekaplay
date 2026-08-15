"""Authentication flows. All state-changing outcomes emit events and write
audit rows; all tokens are stored hashed; refresh tokens rotate with
family-based reuse detection."""
import uuid
from datetime import UTC, datetime, timedelta

from app.core import security
from app.core.config import get_settings
from app.core.errors import AuthenticationError, ConflictError, ValidationFailedError
from app.events.bus import DomainEvent, EventBus
from app.modules.auth import events
from app.modules.auth.models import ActionToken, OAuthAccount, RefreshToken
from app.modules.auth.oauth import get_provider
from app.modules.auth.repository import (
    ActionTokenRepository,
    OAuthAccountRepository,
    RefreshTokenRepository,
)
from app.modules.auth.schemas import TokenPair
from app.modules.users.audit import AuditService
from app.modules.users.events import USER_REGISTERED
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.services.base import BaseService

_VERIFICATION_TTL = timedelta(hours=48)
_RESET_TTL = timedelta(hours=2)
_LOGIN_FAILED = "Incorrect email or password"


class AuthService(BaseService):
    def __init__(
        self,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        action_tokens: ActionTokenRepository,
        oauth_accounts: OAuthAccountRepository,
        audit: AuditService,
        event_bus: EventBus,
    ) -> None:
        super().__init__(event_bus)
        self._users = users
        self._refresh = refresh_tokens
        self._actions = action_tokens
        self._oauth = oauth_accounts
        self._audit = audit

    # ── Registration & verification ────────────────────────────
    async def register(self, *, email: str, password: str, display_name: str) -> User:
        email = email.strip().lower()
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("An account with this email already exists")
        user = User(
            email=email,
            password_hash=security.hash_password(password),
            display_name=display_name,
        )
        self._users.add(user)
        await self._users.flush()
        # Default role: learner (seeded by migration 0001). Graceful when the
        # role is absent (e.g. metadata-only test databases).
        learner = await self._users.get_role_by_name("learner")
        if learner is not None:
            await self._users.assign_role(user.id, learner.id)
        await self._issue_action_token(user, purpose="email_verification", ttl=_VERIFICATION_TTL)
        await self.emit(DomainEvent(name=USER_REGISTERED, user_id=user.id))
        self._audit.record(action="auth.registered", actor_user_id=user.id,
                           entity_type="user", entity_id=user.id)
        return user

    async def verify_email(self, token: str) -> None:
        record = await self._consume_action_token(token, purpose="email_verification")
        user = await self._users.get(record.user_id)
        user.email_verified_at = datetime.now(UTC)
        await self._users.flush()

    # ── Login / refresh / logout ───────────────────────────────
    async def login(
        self, *, email: str, password: str, user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        user = await self._users.get_by_email(email)
        # Constant-shape failure: same error whether email or password is wrong.
        if user is None or user.password_hash is None or not user.is_active:
            security.hash_password(password)  # equalize timing
            raise AuthenticationError(_LOGIN_FAILED)
        if not security.verify_password(user.password_hash, password):
            raise AuthenticationError(_LOGIN_FAILED)
        if security.password_needs_rehash(user.password_hash):
            user.password_hash = security.hash_password(password)
        user.last_login_at = datetime.now(UTC)
        await self._users.flush()
        self._audit.record(action="auth.login", actor_user_id=user.id, ip_address=ip_address)
        await self.emit(DomainEvent(name=events.LOGIN_SUCCEEDED, user_id=user.id))
        return await self._issue_token_pair(user, user_agent=user_agent)

    async def refresh(self, refresh_token: str) -> TokenPair:
        record = await self._refresh.get_by_hash(security.hash_token(refresh_token))
        if record is None:
            raise AuthenticationError("Invalid refresh token")
        now = datetime.now(UTC)
        if record.revoked_at is not None:
            # Replay of a rotated token ⇒ assume theft; kill the whole family.
            # This must be durable: raising below aborts the request
            # transaction (get_db rolls back on exception), so the revocation
            # runs in its own session — the same "handler owns its session"
            # rule the event subscribers follow.
            await self._revoke_family_durably(record.family_id)
            await self.emit(
                DomainEvent(name=events.REFRESH_REUSE_DETECTED, user_id=record.user_id)
            )
            self._audit.record(action="auth.refresh_reuse_detected",
                               actor_user_id=record.user_id)
            raise AuthenticationError("Invalid refresh token")
        expires_at = (
            record.expires_at.replace(tzinfo=UTC)
            if record.expires_at.tzinfo is None  # SQLite returns naive datetimes
            else record.expires_at
        )
        if expires_at < now:
            raise AuthenticationError("Refresh token expired")
        user = await self._users.get(record.user_id)
        if not user.is_active:
            raise AuthenticationError("Account is not active")
        record.revoked_at = now
        return await self._issue_token_pair(user, family_id=record.family_id,
                                            user_agent=record.user_agent)

    async def logout(self, refresh_token: str) -> None:
        record = await self._refresh.get_by_hash(security.hash_token(refresh_token))
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)
            await self._refresh.flush()

    # ── Password reset ─────────────────────────────────────────
    async def request_password_reset(self, email: str) -> None:
        """Deliberately silent when the email is unknown (no account enumeration)."""
        user = await self._users.get_by_email(email)
        if user is None or not user.is_active:
            return
        await self._issue_action_token(user, purpose="password_reset", ttl=_RESET_TTL)

    async def confirm_password_reset(self, *, token: str, new_password: str) -> None:
        record = await self._consume_action_token(token, purpose="password_reset")
        user = await self._users.get(record.user_id)
        user.password_hash = security.hash_password(new_password)
        await self._users.flush()
        await self._refresh.revoke_all_for_user(user.id)  # every session dies
        self._audit.record(action="auth.password_reset", actor_user_id=user.id)
        await self.emit(DomainEvent(name=events.PASSWORD_CHANGED, user_id=user.id))

    # ── OAuth ──────────────────────────────────────────────────
    def oauth_authorization_url(self, provider_name: str, *, state: str,
                                redirect_uri: str) -> str:
        return get_provider(provider_name).authorization_url(
            state=state, redirect_uri=redirect_uri
        )

    async def oauth_login(self, provider_name: str, *, code: str,
                          redirect_uri: str) -> TokenPair:
        info = await get_provider(provider_name).exchange_code(
            code=code, redirect_uri=redirect_uri
        )
        if not info.email:
            raise ValidationFailedError("OAuth provider returned no email address")
        account = await self._oauth.get_by_provider_account(
            info.provider, info.provider_account_id
        )
        if account is not None:
            user = await self._users.get(account.user_id)
        else:
            email = info.email.strip().lower()
            user = await self._users.get_by_email(email)
            if user is None:
                user = User(
                    email=email,
                    display_name=info.name,
                    email_verified_at=datetime.now(UTC),  # provider verified it
                )
                self._users.add(user)
                await self._users.flush()
                await self.emit(DomainEvent(name=USER_REGISTERED, user_id=user.id))
            self._oauth.add(OAuthAccount(
                user_id=user.id, provider=info.provider,
                provider_account_id=info.provider_account_id, email=info.email,
            ))
            await self._oauth.flush()
        if not user.is_active:
            raise AuthenticationError("Account is not active")
        user.last_login_at = datetime.now(UTC)
        self._audit.record(action="auth.oauth_login", actor_user_id=user.id,
                           meta={"provider": info.provider})
        await self.emit(DomainEvent(name=events.LOGIN_SUCCEEDED, user_id=user.id))
        return await self._issue_token_pair(user)

    # ── Internals ──────────────────────────────────────────────
    @staticmethod
    async def _revoke_family_durably(family_id: uuid.UUID) -> None:
        """Revoke a token family in a session of its own so the write survives
        the caller's rollback (reuse detection always ends in a raised
        AuthenticationError)."""
        from app.db.session import SessionFactory

        async with SessionFactory() as session:
            await RefreshTokenRepository(session).revoke_family(family_id)
            await session.commit()

    async def _issue_token_pair(
        self, user: User, *, family_id: uuid.UUID | None = None,
        user_agent: str | None = None,
    ) -> TokenPair:
        settings = get_settings()
        access_token, expires_in = security.create_access_token(user.id)
        raw_refresh = security.generate_opaque_token()
        self._refresh.add(RefreshToken(
            user_id=user.id,
            token_hash=security.hash_token(raw_refresh),
            family_id=family_id or uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
            user_agent=user_agent,
        ))
        await self._refresh.flush()
        return TokenPair(access_token=access_token, refresh_token=raw_refresh,
                         expires_in=expires_in)

    async def _issue_action_token(self, user: User, *, purpose: str,
                                  ttl: timedelta) -> None:
        raw = security.generate_opaque_token()
        self._actions.add(ActionToken(
            user_id=user.id,
            token_hash=security.hash_token(raw),
            purpose=purpose,
            expires_at=datetime.now(UTC) + ttl,
        ))
        await self._actions.flush()
        event_name = (events.VERIFICATION_REQUESTED if purpose == "email_verification"
                      else events.PASSWORD_RESET_REQUESTED)
        # Token travels via the event bus to the (future) notification module.
        await self.emit(DomainEvent(name=event_name, user_id=user.id,
                                    payload={"token": raw, "email": user.email}))

    async def _consume_action_token(self, token: str, *, purpose: str) -> ActionToken:
        record = await self._actions.get_valid(security.hash_token(token), purpose)
        if record is None:
            raise ValidationFailedError("This link is invalid or has expired")
        record.used_at = datetime.now(UTC)
        await self._actions.flush()
        return record
