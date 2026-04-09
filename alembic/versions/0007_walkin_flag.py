"""walkin flag on meetup_rsvps

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-31 00:00:00.000000

Adds is_walkin to meetup_rsvps so the frontend can show a Walk-in badge
on guests who were added manually instead of via Mazmo sync.
Defaults to false for all existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetup_rsvps",
        sa.Column("is_walkin", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("meetup_rsvps", "is_walkin")
