import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlanCreate(BaseModel):
    code: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price_cents: int = Field(ge=0)
    currency: str = Field(default="usd", min_length=3, max_length=3)
    interval: str = Field(default="month", pattern="^(month|year)$")
    trial_days: int = Field(default=0, ge=0, le=90)
    stripe_price_id: str = ""


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str
    price_cents: int
    currency: str
    interval: str
    trial_days: int


class CheckoutRequest(BaseModel):
    plan_code: str
    success_url: str = Field(max_length=1000)
    cancel_url: str = Field(max_length=1000)


class CheckoutOut(BaseModel):
    checkout_url: str


class PortalRequest(BaseModel):
    return_url: str = Field(max_length=1000)


class PortalOut(BaseModel):
    portal_url: str


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
    trial_end: datetime | None
    plan_id: uuid.UUID | None


class TrialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    started_at: datetime
    expires_at: datetime
    status: str


class EntitlementOut(BaseModel):
    premium: bool
    source: str  # subscription | license | trial | none
    subscription: SubscriptionOut | None
    trial: TrialOut | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount_cents: int
    currency: str
    status: str
    refunded_amount_cents: int
    description: str
    created_at: datetime


class RefundRequest(BaseModel):
    amount_cents: int | None = Field(default=None, ge=1)


class RefundOut(BaseModel):
    refund_id: str
    status: str = "requested"


class LicenseCreate(BaseModel):
    organization_id: uuid.UUID
    seats: int = Field(default=1, ge=1)
    expires_at: datetime | None = None
    notes: str = ""


class LicenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    seats: int
    status: str
    expires_at: datetime | None
    notes: str


class LicenseAssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    license_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    assigned_at: datetime
    revoked_at: datetime | None


class LicenseAssignRequest(BaseModel):
    user_id: uuid.UUID


class ActiveLicenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seats: int
    status: str
    expires_at: datetime | None


class OrgLicenseSummaryOut(BaseModel):
    organization_id: uuid.UUID
    seats_purchased: int
    seats_assigned: int
    seats_available: int
    licenses: list[ActiveLicenseOut]
    assignments: list[LicenseAssignmentOut]


class OrgCheckoutRequest(BaseModel):
    plan_code: str
    seats: int = Field(ge=1)
    success_url: str = Field(max_length=1000)
    cancel_url: str = Field(max_length=1000)


class ChangeSeatsRequest(BaseModel):
    seats: int = Field(ge=1)
