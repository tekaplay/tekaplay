"""Add curriculum metadata to the content catalog.

Adds a free-text `description` and `unit_ref` (e.g. "Nelson Chemistry 11,
Ch. 2-3") to Course, and `unit_ref` to Mission, so a course/mission can be
labeled against a specific textbook's unit or chapter structure. No changes
to the mission JSON/GameDefinition schema.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-20
"""
import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column(
        "description", sa.Text(), nullable=False, server_default=""))
    op.add_column("courses", sa.Column(
        "unit_ref", sa.String(300), nullable=False, server_default=""))
    op.add_column("missions", sa.Column(
        "unit_ref", sa.String(300), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("missions", "unit_ref")
    op.drop_column("courses", "unit_ref")
    op.drop_column("courses", "description")
