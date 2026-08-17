"""Central application configuration.

Every environment variable the system understands is declared here, once.
Modules never read os.environ directly — they depend on Settings, which keeps
configuration testable and makes the local → Neon/Upstash/R2 → AWS migration a
pure configuration change (see docs/ARCHITECTURE.md, "Portability").
"""
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.url import async_database_url, is_sqlite, sync_database_url

INSECURE_SECRET_KEY = "insecure-local-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_env: str = Field(default="local", pattern="^(local|test|staging|production)$")
    app_name: str = "tekaplay"
    app_url: str = "http://localhost:3000"
    api_v1_prefix: str = "/api/v1"
    secret_key: str = INSECURE_SECRET_KEY
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]
    # Interactive API docs. Left on outside production (see the validator
    # below); publishing the full schema of an authenticated API to anonymous
    # visitors is free reconnaissance, so production must opt in explicitly.
    docs_enabled: bool | None = None

    # Database
    database_url: str = "postgresql+asyncpg://tekaplay:tekaplay@localhost:5432/tekaplay"
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Object storage — S3-compatible interface (R2 today, S3 later)
    object_storage_endpoint: str = ""
    object_storage_bucket: str = "tekaplay-assets"
    object_storage_access_key: str = ""
    object_storage_secret_key: str = ""
    object_storage_region: str = "auto"

    # AI service — provider-neutral gateway; the frontend never holds keys
    ai_provider: str = Field(default="echo", pattern="^(echo|anthropic)$")
    ai_model: str = "claude-sonnet-4-6"
    ai_api_key: str = ""
    ai_dispatch: str = Field(default="celery", pattern="^(celery|inline)$")
    ai_cache_ttl_seconds: int = 86400
    ai_rate_limit_per_minute: int = 20

    # Commerce — Stripe behind a gateway; 'fake' is deterministic for dev/CI
    payment_provider: str = Field(default="fake", pattern="^(fake|stripe)$")
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_webhook_tolerance_seconds: int = 300

    # Free trials — backend/DB is authoritative; not configurable per-request
    trial_enabled: bool = True
    trial_duration_days: int = 14

    # Email — invitation/verification/reset delivery; 'console' logs instead
    # of sending, so the flow works out of the box with no provider set up.
    email_provider: str = Field(default="console", pattern="^(console|smtp)$")
    email_dispatch: str = Field(default="inline", pattern="^(celery|inline)$")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_email: str = "no-reply@tekaplay.app"

    # Auth
    # Credential endpoints are throttled per IP and per submitted email; the
    # window is deliberately long (15 min) because these limits exist to stop
    # password guessing, not to smooth out bursty traffic.
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 900
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def async_database_url(self) -> str:
        """Database URL for the running application (asyncpg driver)."""
        return async_database_url(self.database_url)[0]

    @property
    def database_connect_args(self) -> dict[str, object]:
        """Driver connect args — carries TLS settings the URL cannot express."""
        return async_database_url(self.database_url)[1]

    @property
    def sync_database_url(self) -> str:
        """Database URL for Alembic (synchronous driver)."""
        return sync_database_url(self.database_url)

    @property
    def docs_are_enabled(self) -> bool:
        """Explicit DOCS_ENABLED wins; otherwise docs are on everywhere but production."""
        if self.docs_enabled is not None:
            return self.docs_enabled
        return not self.is_production

    @model_validator(mode="after")
    def _validate_production_posture(self) -> "Settings":
        """Fail fast on configuration that is unsafe to serve real users.

        Only enforced when APP_ENV=production, so local development, tests and
        CI keep their convenient insecure defaults. A misconfigured production
        boot should die loudly at startup rather than quietly serve traffic
        signed with a publicly known key.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if self.secret_key == INSECURE_SECRET_KEY:
            problems.append(
                "SECRET_KEY is still the built-in development value. Generate one with "
                "`openssl rand -hex 32`."
            )
        elif len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters.")

        if not self.cors_origins:
            problems.append("CORS_ORIGINS must list the public web origin.")
        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS must not contain '*'. Credentialed requests are enabled, so a "
                "wildcard origin would let any site call the API with a user's session."
            )
        for origin in self.cors_origins:
            if origin != "*" and not origin.startswith("https://"):
                problems.append(f"CORS origin {origin!r} must use https:// in production.")

        if is_sqlite(self.database_url):
            problems.append("DATABASE_URL points at SQLite, which is not a production database.")
        elif "localhost" in self.database_url or "127.0.0.1" in self.database_url:
            problems.append(
                "DATABASE_URL points at localhost, which cannot be reached in production."
            )

        if self.app_url.startswith("http://"):
            problems.append(
                "APP_URL must use https:// in production; it builds links in emails."
            )

        if self.payment_provider == "stripe" and not self.stripe_webhook_secret:
            problems.append(
                "STRIPE_WEBHOOK_SECRET is required when PAYMENT_PROVIDER=stripe; without it "
                "subscription state cannot be verified."
            )

        if self.ai_provider == "anthropic" and not self.ai_api_key:
            problems.append("AI_API_KEY is required when AI_PROVIDER=anthropic.")

        if self.email_provider == "smtp" and not self.smtp_host:
            problems.append("SMTP_HOST is required when EMAIL_PROVIDER=smtp.")

        if problems:
            raise ValueError(
                "Invalid production configuration:\n"
                + "\n".join(f"  - {p}" for p in problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — import this, never instantiate Settings directly."""
    return Settings()
