"""meetup weather

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-25

Adds meetup_weather: one row per meetup with the weather of its first 6 hours
(Open-Meteo), keyed by meetup_id and deleted with the meetup. No backfill: the
weather worker fills it for past meetups.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetup_weather",
        sa.Column("meetup_id", sa.Uuid(), nullable=False),
        sa.Column("avg_temperature", sa.Float(), nullable=True),
        sa.Column("avg_apparent_temperature", sa.Float(), nullable=True),
        sa.Column("total_precipitation_mm", sa.Float(), nullable=True),
        sa.Column("max_wind_kmh", sa.Float(), nullable=True),
        sa.Column("rained", sa.Boolean(), nullable=True),
        sa.Column("hourly", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["meetup_id"], ["meetups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("meetup_id"),
    )


def downgrade() -> None:
    op.drop_table("meetup_weather")
