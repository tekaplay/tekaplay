"""Celery worker entry for email delivery (routed to the default queue)."""
import asyncio

from app.workers.celery_app import celery


async def _run(to: str, subject: str, body: str) -> None:
    from app.core.email import get_email_sender

    await get_email_sender().send(to=to, subject=subject, body=body)


@celery.task(
    name="app.modules.notifications.tasks.send_email",
    retry_backoff=2,
    retry_kwargs={"max_retries": 5},
    acks_late=True,
)
def send_email(to: str, subject: str, body: str) -> None:
    asyncio.run(_run(to, subject, body))
