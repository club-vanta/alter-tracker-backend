"""event log for audit trail

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-23 00:00:00.000000

This migration creates the event_log table for tracking auditable actions:
  - CHECK_IN: Guest checked in at a meetup
  - UNDO_CHECK_IN: Check-in was undone (mistake correction)
  - BAN: Guest was banned
  - UNBAN: Guest was unbanned

Each event records:
  - Who performed the action (actor_id -> users)
  - Which guest was affected (guest_id -> guests)
  - Which meetup (if applicable) (meetup_id -> meetups)
  - When it happened (timestamp)
  - Why (reason, optional)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(32), nullable=False, index=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        # Who performed the action
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        # Target guest (if applicable)
        sa.Column(
            "guest_id",
            sa.Integer(),
            sa.ForeignKey("guests.mazmo_user_id", ondelete="SET NULL"),
            index=True,
        ),
        # Related meetup (for check-in events)
        sa.Column(
            "meetup_id",
            sa.UUID(),
            sa.ForeignKey("meetups.id", ondelete="SET NULL"),
            index=True,
        ),
        # Additional context
        sa.Column("reason", sa.String(500), nullable=True),
    )

    # Composite index for common query: "events for guest X at meetup Y"
    op.create_index(
        "ix_event_log_guest_meetup",
        "event_log",
        ["guest_id", "meetup_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_log_guest_meetup", "event_log")
    op.drop_table("event_log")
