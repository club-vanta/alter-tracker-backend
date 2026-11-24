"""meetup cover image and excerpt

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-23

Adds cover_url and excerpt to meetups, taken from the Mazmo event thread for
the public alters endpoint. No backfill here: Alembic does not touch the
network. Existing meetups get their values on the next sync or from the
backfill script.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("cover_url", sa.String(length=512), nullable=True))
    op.add_column("meetups", sa.Column("excerpt", sa.String(length=320), nullable=True))


def downgrade() -> None:
    op.drop_column("meetups", "excerpt")
    op.drop_column("meetups", "cover_url")
