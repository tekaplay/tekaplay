"""Alembic environment.

Reads the database URL from application settings so migrations and the app can
never disagree about where the database lives. Uses a synchronous engine for
migrations (standard practice even with an async app).
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base

# Model modules must be imported so autogenerate sees their tables.
from app.modules.users import models as user_models  # noqa: F401
from app.modules.auth import models as auth_models  # noqa: F401
from app.modules.runtime import models as runtime_models  # noqa: F401
from app.modules.content import models as content_models  # noqa: F401
from app.modules.xp import models as xp_models  # noqa: F401
from app.modules.achievements import models as achievement_models  # noqa: F401
from app.modules.progress import models as progress_models  # noqa: F401
from app.modules.inventory import models as inventory_models  # noqa: F401
from app.modules.ai import models as ai_models  # noqa: F401
from app.modules.commerce import models as commerce_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option(
    # Sync driver for migrations, with libpq params (sslmode) preserved —
    # managed Postgres requires them. See app/db/url.py.
    "sqlalchemy.url",
    settings.sync_database_url.replace("%", "%%"),  # ConfigParser interpolation
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
