"""Content module: the certification catalog and the authoring lifecycle.

Catalog (normalized): Certification → Campaign → Course → Mission. Structure
and metadata only — playable content lives in game definitions, which the
runtime interprets. A Mission points at a ContentProject; the project's live
version is what players get.

Authoring: ContentProject owns an append-only series of ContentVersion
snapshots. Versions are immutable once they leave `draft`; publishing copies
a version into the runtime as a new immutable live definition. Rollback is
just publishing an earlier (superseded) version again — history is never
rewritten.
"""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import PortableJSON


class Certification(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "certifications"

    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "campaigns"
    __table_args__ = (UniqueConstraint("certification_id", "slug", name="uq_campaign_slug"),)

    certification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("certifications.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Course(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("campaign_id", "slug", name="uq_course_slug"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit_ref: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Mission(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "missions"
    __table_args__ = (UniqueConstraint("course_id", "slug", name="uq_mission_slug"),)

    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    unit_ref: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("content_projects.id", ondelete="SET NULL"), nullable=True
    )


class ContentProject(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "content_projects"

    slug: Mapped[str] = mapped_column(String(200), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    certification: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # FK constraint added in the migration (deferred: circular with versions).
    live_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class ContentVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_version_number"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("content_projects.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    definition: Mapped[dict[str, Any]] = mapped_column(PortableJSON, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
