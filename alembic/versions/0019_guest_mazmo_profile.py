"""guest mazmo profile table

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-12

Adds guest_mazmo_profile: a 1:1 snapshot of extended Mazmo profile data
(avatar, age, gender, pronoun, suspended, banned) for a linked guest.
guest_id is the primary key directly (not a surrogate id) since this is
a genuine 1:1 relationship - see the GuestMazmoProfile model docstring
in app/models/models.py for why this differs from this codebase's other
association tables.

No backfill: this data can only be obtained via a live call to Mazmo,
there is nothing to derive it from in existing data. The table starts
empty and fills in as each linked guest appears in a future sync (or is
re-linked via link-mazmo).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0019"
down_revision: str = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guest_mazmo_profile",
        sa.Column("guest_id", UUID(as_uuid=True), nullable=False),
        sa.Column("avatar_url", sa.String(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("gender", sa.String(length=32), nullable=True),
        sa.Column("pronoun", sa.String(length=32), nullable=True),
        sa.Column("mazmo_suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mazmo_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("guest_id"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("guest_mazmo_profile")
