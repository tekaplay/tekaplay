"""Closes the "future notification module" TODO left in auth/service.py:
verification, password-reset, and org-invitation tokens all travel on the
event bus and are rendered into emails here — never returned in API
responses, never logged in full (only truncated), per the redaction posture
documented in core/logging.py.
"""
from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.bus import DomainEvent, EventBus
from app.modules.auth import events as auth_events
from app.modules.organizations import events as org_events

log = get_logger(__name__)


async def _dispatch(*, to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if settings.email_dispatch == "inline":
        from app.core.email import get_email_sender

        await get_email_sender().send(to=to, subject=subject, body=body)
    else:
        from app.modules.notifications.tasks import send_email

        send_email.delay(to, subject, body)


def register(bus: EventBus) -> None:
    settings = get_settings()

    async def on_verification_requested(event: DomainEvent) -> None:
        token = str(event.payload.get("token", ""))
        email = str(event.payload.get("email", ""))
        if not token or not email:
            return
        log.info("verification_email_queued", email=email, token_preview=token[:6] + "...")
        url = f"{settings.app_url}/verify-email?token={token}"
        await _dispatch(to=email, subject="Verify your Tekaplay email",
                        body=f"Confirm your email address: {url}")

    async def on_password_reset_requested(event: DomainEvent) -> None:
        token = str(event.payload.get("token", ""))
        email = str(event.payload.get("email", ""))
        if not token or not email:
            return
        log.info("password_reset_email_queued", email=email, token_preview=token[:6] + "...")
        url = f"{settings.app_url}/reset-password?token={token}"
        await _dispatch(to=email, subject="Reset your Tekaplay password",
                        body=f"Reset your password: {url}")

    async def on_invitation_created(event: DomainEvent) -> None:
        token = str(event.payload.get("token", ""))
        email = str(event.payload.get("email", ""))
        organization_id = event.payload.get("organization_id")
        if not token or not email:
            return
        org_name = organization_id
        if organization_id:
            import uuid

            from app.core.errors import NotFoundError
            from app.db.session import SessionFactory
            from app.modules.organizations.repository import OrganizationRepository

            async with SessionFactory() as session:
                try:
                    org = await OrganizationRepository(session).get(
                        uuid.UUID(str(organization_id))
                    )
                    org_name = org.name
                except NotFoundError:
                    pass
        log.info("invitation_email_queued", email=email, token_preview=token[:6] + "...")
        url = f"{settings.app_url}/invite/{token}"
        await _dispatch(to=email, subject=f"You're invited to join {org_name} on Tekaplay",
                        body=f"Accept your invitation: {url}")

    bus.subscribe(auth_events.VERIFICATION_REQUESTED, on_verification_requested)
    bus.subscribe(auth_events.PASSWORD_RESET_REQUESTED, on_password_reset_requested)
    bus.subscribe(org_events.INVITATION_CREATED, on_invitation_created)
