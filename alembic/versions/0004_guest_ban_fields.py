"""guest ban fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-23 00:00:00.000000

This migration adds ban fields to the guests table for tracking banned guests:
  - is_banned: whether the guest is banned
  - banned_at: timestamp when banned
  - banned_by_id: FK to the admin who banned them
  - banned_reason: why the guest was banned
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add ban fields to guests table
    op.add_column(
        "guests",
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Index on is_banned to speed up banned guests list queries
    op.create_index("ix_guests_is_banned", "guests", ["is_banned"])
    op.add_column(
        "guests",
        sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guests",
        sa.Column("banned_by_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "guests",
        sa.Column("banned_reason", sa.String(500), nullable=True),
    )

    # Add FK constraint for banned_by_id -> users.id
    op.create_foreign_key(
        "fk_guests_banned_by_id",
        "guests",
        "users",
        ["banned_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Remove FK, index, and columns from guests
    op.drop_constraint("fk_guests_banned_by_id", "guests", type_="foreignkey")
    op.drop_index("ix_guests_is_banned", "guests")
    op.drop_column("guests", "banned_reason")
    op.drop_column("guests", "banned_by_id")
    op.drop_column("guests", "banned_at")
    op.drop_column("guests", "is_banned")
