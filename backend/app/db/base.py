"""Declarative base and universal model mixins.

Platform rule: every table gets a UUID primary key, created_at, updated_at,
and (where appropriate) soft-delete. Encoding this once in mixins means no
table can accidentally opt out.

Uuid (dialect-agnostic) is used rather than the postgresql UUID type so the
same models run on PostgreSQL in production and SQLite in fast local tests.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Rows are never physically deleted where auditability matters.

    Repositories must filter deleted_at IS NULL by default (BaseRepository does).
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VersionedMixin:
    """Optimistic concurrency for hot rows (e.g. user progress).

    SQLAlchemy bumps `version` on every UPDATE and raises StaleDataError when
    two writers race — the service layer converts that to ConflictError (409).
    """

    version: Mapped[int] = mapped_column(default=1, nullable=False)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:  # noqa: N805 — declared_attr passes the class
        # Wires automatically for every model using this mixin: SQLAlchemy
        # bumps `version` on UPDATE and raises StaleDataError when the row
        # changed underneath us.
        return {"version_id_col": cls.version}
