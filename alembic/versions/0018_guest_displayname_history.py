"""guest displayname history table

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-12

Adds guest_displayname_history: a full timeline of every displayname
value a guest has had (one row per value, not old/new pairs). Backfills
one BACKFILL row per existing guest using their current displayname,
since Guest has no created_at field to recover a real creation time from.

Follows the 0012_organization_bans.py pattern (create_table with
sa.ForeignKeyConstraint) plus a separate op.create_index for the
composite index, same pattern as the composite index in 0016.

Chained after 0017_guest_type_payment_exemption.py (the guest_type
migration from the prerequisite plan), not directly after 0016.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0018"
down_revision: str = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_displayname_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guest_id", UUID(as_uuid=True), nullable=False),
        sa.Column("displayname", sa.String(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_guest_displayname_history_guest_id", "guest_displayname_history", ["guest_id"])
    op.create_index(
        "ix_guest_displayname_history_guest_recorded",
        "guest_displayname_history",
        ["guest_id", "recorded_at"],
    )

    # Backfill: one BACKFILL row per existing guest, using their current
    # displayname. Unbatched - consistent with how 0016 runs its
    # UPDATE ... FROM statements without chunking at this data volume.
    op.execute("""
        INSERT INTO guest_displayname_history (guest_id, displayname, source, actor_id, recorded_at)
        SELECT id, displayname, 'BACKFILL', NULL, now()
        FROM guests
    """)


def downgrade() -> None:
    op.drop_index("ix_guest_displayname_history_guest_recorded", table_name="guest_displayname_history")
    op.drop_index("ix_guest_displayname_history_guest_id", table_name="guest_displayname_history")
    op.drop_table("guest_displayname_history")
