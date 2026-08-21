"""Add is_active toggle to Course.

Lets a course be hidden from the player-facing catalog (ContentService.library())
without soft-deleting it. Defaults true so every existing course stays visible.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-20
"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("courses", sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("courses", "is_active")
