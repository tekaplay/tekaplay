"""Central application configuration.

Every environment variable the system understands is declared here, once.
Modules never read os.environ directly — they depend on Settings, which keeps
configuration testable and makes the local → Neon/Upstash/R2 → AWS migration a
pure configuration change (see docs/ARCHITECTURE.md, "Portability").
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_env: str = Field(default="local", pattern="^(local|test|staging|production)$")
    app_name: str = "tekaplay"
    app_url: str = "http://localhost:3000"
    api_v1_prefix: str = "/api/v1"
    secret_key: str = "insecure-local-only"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:3000"]

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


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — import this, never instantiate Settings directly."""
    return Settings()
