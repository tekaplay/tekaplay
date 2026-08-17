"""Configuration guardrails and database URL normalisation.

Both are pure functions of the environment, and both fail in ways that are
invisible until a real deploy: a URL that asyncpg cannot parse, or an app that
happily boots in production signing tokens with a publicly known key. That
makes them exactly the things worth testing here rather than discovering live.
"""
import pytest
from pydantic import ValidationError

from app.core.config import INSECURE_SECRET_KEY, Settings
from app.db.url import async_database_url, is_sqlite, sync_database_url

NEON_URL = (
    "postgresql://tekaplay:pw@ep-cool-name-123456-pooler.us-east-2.aws.neon.tech"
    "/tekaplay?sslmode=require&channel_binding=require"
)

# A valid production baseline; individual tests break one thing at a time.
PROD = {
    "app_env": "production",
    "secret_key": "a" * 64,
    "cors_origins": ["https://tekaplay-web.onrender.com"],
    "app_url": "https://tekaplay-web.onrender.com",
    "database_url": NEON_URL,
}


def prod(**overrides) -> Settings:
    # _env_file=None so a developer's local .env cannot influence the result.
    return Settings(**{**PROD, **overrides}, _env_file=None)


# ── URL normalisation ──────────────────────────────────────────


def test_neon_url_is_translated_for_asyncpg():
    """sslmode/channel_binding are libpq-only; asyncpg rejects them outright."""
    url, connect_args = async_database_url(NEON_URL)
    assert url.startswith("postgresql+asyncpg://")
    assert "sslmode" not in url
    assert "channel_binding" not in url
    # TLS is not dropped, only relocated to where the driver accepts it.
    assert connect_args == {"ssl": "require"}


def test_neon_url_keeps_sslmode_for_alembic():
    """psycopg2 speaks libpq and needs sslmode to negotiate TLS."""
    url = sync_database_url(NEON_URL)
    assert url.startswith("postgresql+psycopg2://")
    assert "sslmode=require" in url


def test_host_and_database_survive_normalisation():
    url, _ = async_database_url(NEON_URL)
    assert "ep-cool-name-123456-pooler.us-east-2.aws.neon.tech" in url
    assert url.endswith("/tekaplay")


def test_already_async_url_is_left_alone():
    plain = "postgresql+asyncpg://tekaplay:tekaplay@db:5432/tekaplay"
    url, connect_args = async_database_url(plain)
    assert url == plain
    assert connect_args == {}


def test_sslmode_disable_is_honoured():
    _, connect_args = async_database_url("postgresql://u:p@h/db?sslmode=disable")
    assert connect_args == {"ssl": False}


def test_sqlite_passes_through_unchanged():
    sqlite = "sqlite+aiosqlite:///./test.db"
    assert is_sqlite(sqlite)
    assert async_database_url(sqlite) == (sqlite, {})
    assert sync_database_url(sqlite) == "sqlite:///./test.db"


def test_url_encoded_password_survives_alembic_config_parser():
    """Alembic stores the URL in a ConfigParser, which treats '%' as
    interpolation syntax. Managed providers generate passwords containing
    percent-encoded characters, so env.py escapes them — this asserts the
    round trip a real deploy performs."""
    from configparser import ConfigParser

    raw = "postgresql://user:p%40ss%2Fword@host.neon.tech/db?sslmode=require"
    escaped = sync_database_url(raw).replace("%", "%%")

    parser = ConfigParser()
    parser.add_section("alembic")
    parser.set("alembic", "sqlalchemy.url", escaped)

    assert parser.get("alembic", "sqlalchemy.url") == sync_database_url(raw)
    assert "p%40ss%2Fword" in parser.get("alembic", "sqlalchemy.url")


def test_settings_expose_both_urls():
    settings = prod()
    assert settings.async_database_url.startswith("postgresql+asyncpg://")
    assert settings.sync_database_url.startswith("postgresql+psycopg2://")
    assert settings.database_connect_args == {"ssl": "require"}


# ── Production guardrails ──────────────────────────────────────


def test_valid_production_config_is_accepted():
    assert prod().is_production


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"secret_key": INSECURE_SECRET_KEY}, "SECRET_KEY"),
        ({"secret_key": "too-short"}, "at least 32"),
        ({"cors_origins": ["*"]}, "must not contain"),
        ({"cors_origins": []}, "CORS_ORIGINS"),
        ({"cors_origins": ["http://tekaplay.app"]}, "https://"),
        ({"database_url": "sqlite+aiosqlite:///./prod.db"}, "SQLite"),
        ({"database_url": "postgresql+asyncpg://u:p@localhost/db"}, "localhost"),
        ({"app_url": "http://tekaplay.app"}, "APP_URL"),
        ({"payment_provider": "stripe", "stripe_api_key": "sk"}, "STRIPE_WEBHOOK_SECRET"),
        ({"ai_provider": "anthropic"}, "AI_API_KEY"),
        ({"email_provider": "smtp"}, "SMTP_HOST"),
    ],
)
def test_production_rejects_unsafe_configuration(overrides, expected):
    with pytest.raises(ValidationError, match=expected):
        prod(**overrides)


def test_insecure_defaults_are_fine_outside_production():
    """Local development and CI must keep working with zero configuration —
    the guardrails exist to protect production, not to obstruct developers."""
    settings = Settings(
        app_env="local",
        secret_key=INSECURE_SECRET_KEY,
        cors_origins=["http://localhost:3000"],
        app_url="http://localhost:3000",
        database_url="sqlite+aiosqlite:///./test.db",
        _env_file=None,
    )
    assert not settings.is_production


def test_docs_default_off_in_production_and_on_elsewhere():
    assert not prod().docs_are_enabled
    assert Settings(app_env="local", _env_file=None).docs_are_enabled
    # …but remain an explicit opt-in rather than an absolute.
    assert prod(docs_enabled=True).docs_are_enabled
