import uuid

from fastapi import APIRouter, Query, Request, Response

from app.api.deps import Bus, CurrentUser, DbSession, require_permission
from app.modules.commerce.schemas import (
    ActiveLicenseOut,
    ChangeSeatsRequest,
    CheckoutOut,
    CheckoutRequest,
    EntitlementOut,
    LicenseAssignmentOut,
    LicenseAssignRequest,
    LicenseCreate,
    LicenseOut,
    OrgCheckoutRequest,
    OrgLicenseSummaryOut,
    PaymentOut,
    PlanCreate,
    PlanOut,
    PortalOut,
    PortalRequest,
    RefundOut,
    RefundRequest,
    SubscriptionOut,
    TrialOut,
)
from app.modules.commerce.service import build_commerce_service
from app.modules.organizations.deps import OrgAdmin

router = APIRouter(prefix="/commerce", tags=["commerce"])

MANAGE = require_permission("commerce.manage")


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(_: CurrentUser, session: DbSession, bus: Bus) -> list[PlanOut]:
    plans = await build_commerce_service(session, bus).list_plans()
    return [PlanOut.model_validate(p) for p in plans]


@router.post("/plans", response_model=PlanOut, status_code=201, dependencies=[MANAGE])
async def create_plan(body: PlanCreate, current_user: CurrentUser,
                      session: DbSession, bus: Bus) -> PlanOut:
    plan = await build_commerce_service(session, bus).create_plan(
        body.model_dump(), actor=current_user.id
    )
    return PlanOut.model_validate(plan)


@router.post("/checkout", response_model=CheckoutOut)
async def start_checkout(body: CheckoutRequest, current_user: CurrentUser,
                         session: DbSession, bus: Bus) -> CheckoutOut:
    url = await build_commerce_service(session, bus).start_checkout(
        user_id=current_user.id, email=current_user.email,
        plan_code=body.plan_code,
        success_url=body.success_url, cancel_url=body.cancel_url,
    )
    return CheckoutOut(checkout_url=url)


@router.post("/portal", response_model=PortalOut)
async def billing_portal(body: PortalRequest, current_user: CurrentUser,
                         session: DbSession, bus: Bus) -> PortalOut:
    url = await build_commerce_service(session, bus).portal_url(
        user_id=current_user.id, return_url=body.return_url
    )
    return PortalOut(portal_url=url)


@router.get("/subscription", response_model=EntitlementOut)
async def my_subscription(current_user: CurrentUser, session: DbSession,
                          bus: Bus) -> EntitlementOut:
    result = await build_commerce_service(session, bus).entitlement(current_user.id)
    subscription = result["subscription"]
    trial = result["trial"]
    return EntitlementOut(
        premium=result["premium"],
        source=result["source"],
        subscription=(SubscriptionOut.model_validate(subscription)
                      if subscription is not None else None),
        trial=TrialOut.model_validate(trial) if trial is not None else None,
    )


@router.post("/trial/start", response_model=TrialOut, status_code=201)
async def start_trial(current_user: CurrentUser, session: DbSession, bus: Bus) -> TrialOut:
    trial = await build_commerce_service(session, bus).start_trial(current_user.id)
    return TrialOut.model_validate(trial)


@router.get("/payments/me", response_model=list[PaymentOut])
async def my_payments(current_user: CurrentUser, session: DbSession,
                      bus: Bus) -> list[PaymentOut]:
    payments = await build_commerce_service(session, bus).my_payments(current_user.id)
    return [PaymentOut.model_validate(p) for p in payments]


@router.post("/payments/{payment_id}/refund", response_model=RefundOut,
             status_code=202, dependencies=[MANAGE])
async def refund_payment(payment_id: uuid.UUID, body: RefundRequest,
                         current_user: CurrentUser, session: DbSession,
                         bus: Bus) -> RefundOut:
    refund_id = await build_commerce_service(session, bus).request_refund(
        payment_id=payment_id, amount_cents=body.amount_cents,
        actor=current_user.id,
    )
    return RefundOut(refund_id=refund_id)


@router.post("/licenses", response_model=LicenseOut, status_code=201,
             dependencies=[MANAGE])
async def create_license(body: LicenseCreate, current_user: CurrentUser,
                         session: DbSession, bus: Bus) -> LicenseOut:
    license_ = await build_commerce_service(session, bus).create_license(
        body.model_dump(), actor=current_user.id
    )
    return LicenseOut.model_validate(license_)


@router.get("/licenses", response_model=list[LicenseOut], dependencies=[MANAGE])
async def list_licenses(
    session: DbSession, bus: Bus,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[LicenseOut]:
    licenses = await build_commerce_service(session, bus).list_licenses(
        limit=limit, offset=offset
    )
    return [LicenseOut.model_validate(license_) for license_ in licenses]


@router.post("/organizations/{organization_id}/checkout", response_model=CheckoutOut)
async def start_org_checkout(
    organization_id: uuid.UUID, body: OrgCheckoutRequest, admin: OrgAdmin,
    session: DbSession, bus: Bus,
) -> CheckoutOut:
    """Org-admin only, verified server-side via OrgAdmin — never trusts the
    frontend for who may buy seats on this organization's behalf."""
    url = await build_commerce_service(session, bus).start_org_checkout(
        organization_id=organization_id, plan_code=body.plan_code, seats=body.seats,
        success_url=body.success_url, cancel_url=body.cancel_url, actor=admin.user_id,
    )
    return CheckoutOut(checkout_url=url)


@router.post("/organizations/{organization_id}/change-seats", status_code=202)
async def change_org_seats(
    organization_id: uuid.UUID, body: ChangeSeatsRequest, admin: OrgAdmin,
    session: DbSession, bus: Bus,
) -> dict[str, str]:
    await build_commerce_service(session, bus).change_org_seats(
        organization_id=organization_id, new_seat_count=body.seats, actor=admin.user_id,
    )
    return {"status": "requested"}  # local seat count updates via webhook, not here


@router.get("/organizations/{organization_id}/licenses", response_model=OrgLicenseSummaryOut)
async def org_license_summary(
    organization_id: uuid.UUID, admin: OrgAdmin, session: DbSession, bus: Bus,
) -> OrgLicenseSummaryOut:
    summary = await build_commerce_service(session, bus).org_license_summary(organization_id)
    return OrgLicenseSummaryOut(
        organization_id=summary["organization_id"],
        seats_purchased=summary["seats_purchased"],
        seats_assigned=summary["seats_assigned"],
        seats_available=summary["seats_available"],
        licenses=[ActiveLicenseOut.model_validate(lic) for lic in summary["licenses"]],
        assignments=[LicenseAssignmentOut.model_validate(a) for a in summary["assignments"]],
    )


@router.post("/organizations/{organization_id}/licenses/{license_id}/assign",
             response_model=LicenseAssignmentOut, status_code=201)
async def assign_license(
    organization_id: uuid.UUID, license_id: uuid.UUID, body: LicenseAssignRequest,
    admin: OrgAdmin, session: DbSession, bus: Bus,
) -> LicenseAssignmentOut:
    assignment = await build_commerce_service(session, bus).assign_license(
        organization_id=organization_id, license_id=license_id,
        target_user_id=body.user_id, actor=admin.user_id,
    )
    return LicenseAssignmentOut.model_validate(assignment)


@router.post(
    "/organizations/{organization_id}/licenses/{license_id}/assignments/{assignment_id}/revoke",
    status_code=204,
)
async def revoke_license_assignment(
    organization_id: uuid.UUID, license_id: uuid.UUID, assignment_id: uuid.UUID,
    admin: OrgAdmin, session: DbSession, bus: Bus,
) -> None:
    await build_commerce_service(session, bus).revoke_license_assignment(
        organization_id=organization_id, assignment_id=assignment_id, actor=admin.user_id,
    )


@router.post("/webhooks/stripe", status_code=204, include_in_schema=False)
async def stripe_webhook(request: Request, session: DbSession, bus: Bus) -> Response:
    """Unauthenticated by design; trust comes from signature verification.
    Idempotent via the webhook ledger, so Stripe retries are safe."""
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    await build_commerce_service(session, bus).handle_webhook(payload, signature)
    return Response(status_code=204)
