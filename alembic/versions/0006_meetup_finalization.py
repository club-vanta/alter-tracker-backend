"""meetup finalization fields

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-26 00:00:00.000000

Adds is_finalized and finalized_at to the meetups table.
Once finalized, a meetup no longer accepts check-ins or syncs.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("is_finalized", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("meetups", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("meetups", "finalized_at")
    op.drop_column("meetups", "is_finalized")
