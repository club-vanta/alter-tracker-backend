"""staff disable (soft-delete) and check-in attribution

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-23 00:00:00.000000

This migration adds:
  1. Soft-delete fields to users table for audit trail:
     - is_disabled: whether the account is disabled
     - disabled_at: timestamp when disabled
     - disabled_by_id: FK to the admin who disabled it
     - disabled_reason: why the account was disabled

  2. Check-in attribution to meetup_rsvps:
     - checked_in_by_id: FK to the staff member who checked in the guest
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Add disable fields to users table ─────────────────────────────────
    op.add_column(
        "users",
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("disabled_by_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("disabled_reason", sa.String(500), nullable=True),
    )

    # Add FK constraint for disabled_by_id -> users.id (self-referential)
    op.create_foreign_key(
        "fk_users_disabled_by_id",
        "users",
        "users",
        ["disabled_by_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ── 2. Add check-in attribution to meetup_rsvps ──────────────────────────
    op.add_column(
        "meetup_rsvps",
        sa.Column("checked_in_by_id", sa.Integer(), nullable=True),
    )

    # Add FK constraint for checked_in_by_id -> users.id
    op.create_foreign_key(
        "fk_meetup_rsvps_checked_in_by_id",
        "meetup_rsvps",
        "users",
        ["checked_in_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Remove FK and column from meetup_rsvps
    op.drop_constraint("fk_meetup_rsvps_checked_in_by_id", "meetup_rsvps", type_="foreignkey")
    op.drop_column("meetup_rsvps", "checked_in_by_id")

    # Remove FK and columns from users
    op.drop_constraint("fk_users_disabled_by_id", "users", type_="foreignkey")
    op.drop_column("users", "disabled_reason")
    op.drop_column("users", "disabled_by_id")
    op.drop_column("users", "disabled_at")
    op.drop_column("users", "is_disabled")
