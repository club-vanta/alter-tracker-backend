"""add paid-entrance tracking to meetups and meetup_rsvps

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-05

Club Vanta is starting to run paid events. Guests still RSVP via Mazmo as
usual, but for these events a guest cannot be checked in at the door until
an org admin manually confirms their payment (handled externally by the
organizer).

Adds:
  - meetups.requires_payment: marks an event as requiring paid entrance.
  - meetup_rsvps.has_paid / paid_at / paid_by_id: per-guest-per-meetup
    payment state, mirroring the existing has_arrived / arrival_time /
    checked_in_by_id check-in tracking fields. Never touched by Mazmo sync.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetups",
        sa.Column("requires_payment", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column(
        "meetup_rsvps",
        sa.Column("has_paid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "meetup_rsvps",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "meetup_rsvps",
        sa.Column("paid_by_id", sa.Integer(), nullable=True),
    )

    op.create_index("ix_meetup_rsvps_has_paid", "meetup_rsvps", ["has_paid"])

    op.create_foreign_key(
        "fk_meetup_rsvps_paid_by_id",
        "meetup_rsvps",
        "users",
        ["paid_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_meetup_rsvps_paid_by_id", "meetup_rsvps", type_="foreignkey")
    op.drop_index("ix_meetup_rsvps_has_paid", "meetup_rsvps")
    op.drop_column("meetup_rsvps", "paid_by_id")
    op.drop_column("meetup_rsvps", "paid_at")
    op.drop_column("meetup_rsvps", "has_paid")
    op.drop_column("meetups", "requires_payment")
