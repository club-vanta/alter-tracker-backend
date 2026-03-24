"""multi-meetup schema - meetups, guests, meetup_rsvps with arrival trigger

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-17 00:00:00.000000

Schema decisions:
  - `guests` table holds identity only (mazmo_user_id, username, displayname).
    No RSVP or check-in data here.

  - `meetups` table stores event metadata and the Mazmo URL used for syncing.

  - `meetup_rsvps` is the association table with composite PK (meetup_id, guest_id).
    Contains RSVP state (rsvp_time, cancelled_rsvp) and check-in state
    (has_arrived, arrival_time, arrival_order).

  - Per-meetup arrival_order is handled by a trigger that calculates the next
    arrival order when has_arrived flips to TRUE. This is gap-free per meetup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0002"
down_revision: str = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. meetups ────────────────────────────────────────────────────────────
    op.create_table(
        "meetups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(), nullable=False),
        sa.Column("mazmo_meetup_url", sqlmodel.AutoString(), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mazmo_meetup_url"),
    )
    op.create_index("ix_meetups_name", "meetups", ["name"])

    # ── 2. guests (identity only) ─────────────────────────────────────────────
    op.create_table(
        "guests",
        sa.Column("mazmo_user_id", sa.Integer(), nullable=False),
        sa.Column("username", sqlmodel.AutoString(), nullable=False),
        sa.Column("displayname", sqlmodel.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("mazmo_user_id"),
    )
    op.create_index("ix_guests_username", "guests", ["username"])

    # ── 3. meetup_rsvps (association table with RSVP + check-in data) ─────────
    op.create_table(
        "meetup_rsvps",
        sa.Column("meetup_id", sa.UUID(), nullable=False),
        sa.Column("guest_id", sa.Integer(), nullable=False),
        sa.Column("rsvp_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_rsvp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_arrived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrival_order", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("meetup_id", "guest_id"),
        sa.ForeignKeyConstraint(["meetup_id"], ["meetups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.mazmo_user_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_meetup_rsvps_has_arrived", "meetup_rsvps", ["has_arrived"])

    # ── 4. Trigger function for per-meetup arrival_order ──────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION set_arrival_order()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Only set arrival_order when has_arrived becomes TRUE
            IF NEW.has_arrived = TRUE AND (OLD.has_arrived = FALSE OR OLD.has_arrived IS NULL) THEN
                NEW.arrival_time := NOW();
                NEW.arrival_order := COALESCE(
                    (SELECT MAX(arrival_order) + 1
                     FROM meetup_rsvps
                     WHERE meetup_id = NEW.meetup_id),
                    1
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 5. Attach trigger to meetup_rsvps ─────────────────────────────────────
    op.execute("""
        CREATE TRIGGER trg_set_arrival_order
            BEFORE UPDATE ON meetup_rsvps
            FOR EACH ROW
            EXECUTE FUNCTION set_arrival_order();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_set_arrival_order ON meetup_rsvps")
    op.execute("DROP FUNCTION IF EXISTS set_arrival_order()")
    op.drop_table("meetup_rsvps")
    op.drop_table("guests")
    op.drop_table("meetups")
