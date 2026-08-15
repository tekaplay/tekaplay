"""Email sending — the only place SMTP/provider details may live.

Mirrors app.modules.commerce.gateway's PaymentGateway pattern: a tiny
Protocol, a deterministic no-op implementation for dev/CI (ConsoleEmailSender
just logs), and a real implementation for production (SMTPEmailSender),
selected by EMAIL_PROVIDER. Callers never touch smtplib directly.
"""
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    """Default for local/test: logs that an email would have been sent.
    Never logs the full body (may carry a token) — only its length, matching
    the redaction posture used elsewhere for sensitive values."""

    async def send(self, *, to: str, subject: str, body: str) -> None:
        log.info("email_sent_console", to=to, subject=subject, body_length=len(body))


class SMTPEmailSender:
    """Synchronous smtplib call — fine at this volume; move to the Celery
    email queue (EMAIL_DISPATCH=celery) rather than making this async-native."""

    async def send(self, *, to: str, subject: str, body: str) -> None:
        settings = get_settings()
        message = EmailMessage()
        message["From"] = settings.from_email or settings.smtp_username
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
            client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        log.info("email_sent_smtp", to=to, subject=subject)


def get_email_sender() -> EmailSender:
    senders: dict[str, EmailSender] = {
        "console": ConsoleEmailSender(),
        "smtp": SMTPEmailSender(),
    }
    return senders[get_settings().email_provider]
