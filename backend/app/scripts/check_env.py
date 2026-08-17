"""Pre-deploy configuration check: python -m app.scripts.check_env

Loads Settings (which enforces the production guardrails in
app/core/config.py), then reports on the things that are legal but worth
knowing about — integrations still running in no-op mode, missing Redis, and
so on. Exits non-zero on hard failures so it can gate a deploy.

Prints no secret values, only whether they are set.
"""
import sys

from app.core.config import get_settings
from app.db.url import is_sqlite

_OK = "  ok   "
_WARN = " warn  "
_FAIL = " fail  "


def main() -> int:
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 — the message is the whole point
        print(f"{_FAIL} configuration rejected\n\n{exc}\n")
        return 1

    warnings: list[str] = []
    lines: list[str] = []

    def ok(msg: str) -> None:
        lines.append(f"{_OK} {msg}")

    def warn(msg: str) -> None:
        lines.append(f"{_WARN} {msg}")
        warnings.append(msg)

    ok(f"APP_ENV={settings.app_env}")
    ok(f"APP_URL={settings.app_url}")
    ok(f"CORS_ORIGINS={settings.cors_origins}")
    ok(f"SECRET_KEY set ({len(settings.secret_key)} chars)")

    if is_sqlite(settings.database_url):
        warn("DATABASE_URL is SQLite: fine for tests, not for real data")
    else:
        # Host only: never print credentials.
        host = settings.async_database_url.split("@")[-1]
        ok(f"DATABASE_URL -> {host}")
        if settings.database_connect_args.get("ssl"):
            ok(f"database TLS: {settings.database_connect_args['ssl']}")
        else:
            warn("database connection is not requesting TLS (no sslmode in DATABASE_URL)")

    if settings.redis_url:
        ok(f"REDIS_URL -> {settings.redis_url.split('@')[-1]}")
    else:
        warn("REDIS_URL is empty: rate limiting and AI caching will be disabled")

    if settings.docs_are_enabled:
        warn(f"API docs are PUBLIC at {settings.api_v1_prefix}/docs")
    else:
        ok("API docs disabled")

    if settings.email_provider == "console":
        warn(
            "EMAIL_PROVIDER=console: verification, password-reset and invitation "
            "emails are only written to the logs, never delivered"
        )
    else:
        ok(f"EMAIL_PROVIDER={settings.email_provider} from {settings.from_email}")

    if settings.payment_provider == "fake":
        warn("PAYMENT_PROVIDER=fake: no real payments can be taken")
    else:
        ok("PAYMENT_PROVIDER=stripe")

    if settings.ai_provider == "echo":
        warn("AI_PROVIDER=echo: AI drafting returns placeholder text")
    else:
        ok(f"AI_PROVIDER={settings.ai_provider} model={settings.ai_model}")

    if settings.ai_dispatch == "celery" or settings.email_dispatch == "celery":
        warn(
            "AI_DISPATCH/EMAIL_DISPATCH is 'celery': a Celery worker process must be "
            "running or those jobs will queue forever"
        )
    else:
        ok("background dispatch is inline (no worker required)")

    print("\n".join(lines))
    print()
    if warnings:
        print(f"{len(warnings)} warning(s). Configuration is valid but incomplete.")
    else:
        print("Configuration looks complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
