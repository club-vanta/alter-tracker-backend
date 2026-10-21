"""guest mazmo profile deleted flag

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-21

Adds guest_mazmo_profile.mazmo_deleted, set from the `deleted` field Mazmo
returns for accounts removed from Mazmo. No backfill: guests already
showing a hashed displayname get flagged, and their previous name
restored, the next time a sync or POST /guests/refresh-mazmo sees them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "guest_mazmo_profile",
        sa.Column("mazmo_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("guest_mazmo_profile", "mazmo_deleted")
