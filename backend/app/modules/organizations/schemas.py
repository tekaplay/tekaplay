import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    org_type: str | None = Field(default=None, max_length=40)


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    org_type: str | None
    status: str
    created_at: datetime


class OrganizationMemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    email: str
    role: str
    has_license: bool
    joined_at: datetime


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member)$")


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    created_at: datetime


class InvitationPreviewOut(BaseModel):
    organization_name: str
    role: str
    expires_at: datetime
    valid: bool


class InvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
