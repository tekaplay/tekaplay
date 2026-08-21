import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"


# ── Catalog ────────────────────────────────────────────────────
class CertificationCreate(BaseModel):
    slug: str = Field(pattern=_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    category: str = ""
    sort_order: int = 0


class CampaignCreate(BaseModel):
    certification_id: uuid.UUID
    slug: str = Field(pattern=_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    sort_order: int = 0


class CourseCreate(BaseModel):
    campaign_id: uuid.UUID
    slug: str = Field(pattern=_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    unit_ref: str = ""
    sort_order: int = 0
    is_active: bool = True


class MissionCreate(BaseModel):
    course_id: uuid.UUID
    slug: str = Field(pattern=_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    unit_ref: str = ""
    sort_order: int = 0
    project_id: uuid.UUID | None = None


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    sort_order: int


class MissionNode(NodeOut):
    unit_ref: str = ""
    project_id: uuid.UUID | None
    definition_id: uuid.UUID | None = None  # live runtime definition, if published


class CourseNode(NodeOut):
    description: str = ""
    unit_ref: str = ""
    missions: list[MissionNode] = []


class CampaignNode(NodeOut):
    description: str = ""
    courses: list[CourseNode] = []


class CertificationNode(NodeOut):
    description: str = ""
    category: str = ""
    campaigns: list[CampaignNode] = []


# ── Authoring ──────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    slug: str = Field(pattern=_SLUG, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    certification: str = ""


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    certification: str
    owner_id: uuid.UUID | None
    live_version_id: uuid.UUID | None
    created_at: datetime


class DraftIn(BaseModel):
    definition: dict[str, Any]
    notes: str = ""


class ReviewIn(BaseModel):
    note: str = ""


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    version_number: int
    status: str
    notes: str
    review_note: str
    created_by: uuid.UUID | None
    created_at: datetime
    submitted_at: datetime | None
    reviewed_at: datetime | None
    published_at: datetime | None


class VersionDetail(VersionOut):
    definition: dict[str, Any]  # JSON preview


class ValidateIn(BaseModel):
    definition: dict[str, Any]


class ValidateOut(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = []
