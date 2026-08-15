"""Organizational accounts, seat-based licensing, invitations, trials.

Extends the existing organizations/enterprise-license groundwork with:
role-based membership (owner/admin/member), invitations, per-seat license
assignment (LicenseAssignment), configurable individual trials, and org-level
Stripe billing customers. Existing admin-granted comped licenses keep working
unchanged (new Stripe columns on enterprise_licenses are nullable).

A data backfill promotes one existing member per organization to 'owner' so
every pre-existing org satisfies the new "exactly one owner" invariant
without losing any data.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-14
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ── Organization / membership extensions ───────────────────
    op.add_column("organizations", sa.Column("org_type", sa.String(40)))
    op.add_column("organizations", sa.Column(
        "status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("organizations", sa.Column(
        "org_metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.add_column("organization_members", sa.Column(
        "role", sa.String(20), nullable=False, server_default="member"))
    op.add_column("organization_members", sa.Column(
        "invited_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")))

    _backfill_org_owners()

    # ── Invitations ──────────────────────────────────────────────
    op.create_table(
        "organization_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("invited_by", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
    )
    op.create_index("ix_org_invitations_organization_id", "organization_invitations",
                    ["organization_id"])
    op.create_index("ix_org_invitations_token_hash", "organization_invitations",
                    ["token_hash"], unique=True)
    op.create_index("ix_org_invitations_org_email", "organization_invitations",
                    ["organization_id", "email"])

    # ── Plan extensions ─────────────────────────────────────────
    op.add_column("plans", sa.Column(
        "kind", sa.String(20), nullable=False, server_default="individual"))
    op.add_column("plans", sa.Column(
        "features", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))

    # ── Enterprise license: seat-subscription fields ────────────
    op.add_column("enterprise_licenses", sa.Column(
        "plan_id", sa.Uuid(), sa.ForeignKey("plans.id", ondelete="SET NULL")))
    op.add_column("enterprise_licenses", sa.Column(
        "stripe_subscription_id", sa.String(120)))
    op.add_column("enterprise_licenses", sa.Column(
        "current_period_end", sa.DateTime(timezone=True)))
    op.add_column("enterprise_licenses", sa.Column(
        "cancel_at_period_end", sa.Boolean(), nullable=False,
        server_default=sa.text("false")))
    op.add_column("enterprise_licenses", sa.Column("trial_end", sa.DateTime(timezone=True)))
    op.create_index("ix_enterprise_licenses_stripe_subscription_id", "enterprise_licenses",
                    ["stripe_subscription_id"], unique=True)

    # ── License assignments (seat allocation) ───────────────────
    op.create_table(
        "license_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("license_id", sa.Uuid(),
                  sa.ForeignKey("enterprise_licenses.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("assigned_by", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        *_timestamps(),
    )
    op.create_index("ix_license_assignments_license_id", "license_assignments",
                    ["license_id"])
    op.create_index("ix_license_assignments_user_id", "license_assignments", ["user_id"])
    op.create_index(
        "uq_license_assignment_active", "license_assignments", ["license_id", "user_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )

    # ── Trials ───────────────────────────────────────────────────
    op.create_table(
        "trials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        *_timestamps(),
    )
    op.create_index("ix_trials_user_id", "trials", ["user_id"], unique=True)

    # ── Organization billing customers ──────────────────────────
    op.create_table(
        "organization_billing_customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stripe_customer_id", sa.String(120), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("organization_id", name="uq_org_billing_customer_org"),
    )
    op.create_index("ix_org_billing_customers_organization_id",
                    "organization_billing_customers", ["organization_id"])
    op.create_index("ix_org_billing_customers_stripe_customer_id",
                    "organization_billing_customers", ["stripe_customer_id"], unique=True)


def _backfill_org_owners() -> None:
    """Every pre-existing organization must end up with exactly one
    role='owner' member. Promote the earliest-joined member of each org that
    doesn't already have one. Orgs with zero members are left as-is — there's
    no member to promote, and the app-level invariant is only enforced going
    forward for orgs that have at least one member."""
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE organization_members om
        SET role = 'owner'
        FROM (
            SELECT DISTINCT ON (organization_id) id, organization_id
            FROM organization_members
            WHERE organization_id NOT IN (
                SELECT organization_id FROM organization_members WHERE role = 'owner'
            )
            ORDER BY organization_id, created_at ASC
        ) earliest
        WHERE om.id = earliest.id
    """))


def downgrade() -> None:
    op.drop_table("organization_billing_customers")
    op.drop_table("trials")
    op.drop_index("uq_license_assignment_active", table_name="license_assignments")
    op.drop_table("license_assignments")
    op.drop_index("ix_enterprise_licenses_stripe_subscription_id",
                  table_name="enterprise_licenses")
    op.drop_column("enterprise_licenses", "trial_end")
    op.drop_column("enterprise_licenses", "cancel_at_period_end")
    op.drop_column("enterprise_licenses", "current_period_end")
    op.drop_column("enterprise_licenses", "stripe_subscription_id")
    op.drop_column("enterprise_licenses", "plan_id")
    op.drop_column("plans", "features")
    op.drop_column("plans", "kind")
    op.drop_table("organization_invitations")
    op.drop_column("organization_members", "invited_by")
    op.drop_column("organization_members", "role")
    op.drop_column("organizations", "org_metadata")
    op.drop_column("organizations", "status")
    op.drop_column("organizations", "org_type")
