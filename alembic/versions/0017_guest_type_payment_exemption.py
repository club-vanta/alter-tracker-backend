"""add guest_type to meetup_rsvps for payment exemption categories

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-12

Club Vanta needs to distinguish guests who don't pay entry (personally
invited by the organizer, vendors running their own stand, or event
staff) from guests who simply haven't paid yet. Marking them has_paid=True
by hand would lie about the audit trail and lose the reason.

Adds:
  - meetup_rsvps.guest_type: VARCHAR(16), one of NORMAL/INVITED/VENDOR/STAFF
    (see GuestType in app/models/models.py), defaulting to NORMAL so
    existing rows backfill without a separate data migration step. Never
    touched by Mazmo sync, curated by hand by an org admin, same as
    has_paid.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetup_rsvps",
        sa.Column("guest_type", sa.String(length=16), nullable=False, server_default="NORMAL"),
    )
    op.create_index("ix_meetup_rsvps_guest_type", "meetup_rsvps", ["guest_type"])


def downgrade() -> None:
    op.drop_index("ix_meetup_rsvps_guest_type", "meetup_rsvps")
    op.drop_column("meetup_rsvps", "guest_type")
