"""uncancel walk-in rsvps

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-21

Data-only fix. Before this revision, the Mazmo sync marked every RSVP
missing from Mazmo's latest list as cancelled, walk-ins included, so any
meetup synced after a walk-in was added showed that walk-in as
cancelled. The sync now skips walk-ins (see
GuestSyncer._update_cancelled_rsvps); this resets the rows it already
flipped.

Safe for every walk-in row: add-walkin refuses to create a walk-in over
an existing RSVP row, and the sync's own upsert only ever sets
cancelled_rsvp to false, so a walk-in can only be cancelled through the
old bug.

downgrade is a no-op: the previous values were wrong and are not worth
restoring.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE meetup_rsvps SET cancelled_rsvp = false WHERE is_walkin AND cancelled_rsvp")


def downgrade() -> None:
    pass
