"""meetup_rsvps guest_id index

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-23

Indexes meetup_rsvps.guest_id on its own. The primary key is
(meetup_id, guest_id), so it cannot serve "every RSVP of one guest", which
GET /organizations/{org_id}/guests/{guest_id}/meetups runs on every guest
profile open.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_meetup_rsvps_guest_id", "meetup_rsvps", ["guest_id"])


def downgrade() -> None:
    op.drop_index("ix_meetup_rsvps_guest_id", table_name="meetup_rsvps")
