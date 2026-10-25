"""meetup location

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-24

Adds the venue of a meetup: a label plus its coordinates. All three columns
are nullable (no backfill) and are set or cleared together by
PATCH /organizations/{org_id}/meetups/{meetup_id}/location.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("meetups", sa.Column("location_label", sa.String(length=255), nullable=True))
    op.add_column("meetups", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("meetups", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetups", "longitude")
    op.drop_column("meetups", "latitude")
    op.drop_column("meetups", "location_label")
