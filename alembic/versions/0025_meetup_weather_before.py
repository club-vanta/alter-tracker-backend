"""meetup weather before the start

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-26

Adds the before_* summary of the 3 hours before a meetup's start to
meetup_weather, and deletes every stored row: they were fetched with the old
window (no before hours, no weather_code or is_day), and the weather worker
only fills meetups without a row, so deleting them makes it fetch every meetup
again with the new window.

Downgrade only drops the columns: it does not bring back the deleted rows, and
rows fetched since keep the wider hourly arrays (the old code ignores the extra
hours and keys).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("before_avg_temperature", sa.Float()),
    ("before_avg_apparent_temperature", sa.Float()),
    ("before_total_precipitation_mm", sa.Float()),
    ("before_rained", sa.Boolean()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("meetup_weather", sa.Column(name, type_, nullable=True))
    op.execute("DELETE FROM meetup_weather")


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("meetup_weather", name)
