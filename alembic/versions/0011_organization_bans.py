"""create organization_bans table and add org_id to event_log

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-20

Bans are now per-organization. The organization_bans table replaces the
ban fields on the guests table (removed in migration 0013).

event_log also gets org_id:
  - Events tied to a meetup inherit the meetup's org_id.
  - BAN/UNBAN events without a meetup are assigned to Club Vanta (only existing org).
  - GUEST_CREATED events remain NULL (they are global).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0011"
down_revision: str = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── organization_bans ─────────────────────────────────────────────────────
    op.create_table(
        "organization_bans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("guest_id", sa.Integer(), nullable=False),
        sa.Column("banned_by_id", sa.Integer(), nullable=True),
        sa.Column("banned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "guest_id", name="uq_organization_bans_org_guest"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.mazmo_user_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["banned_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_organization_bans_org_id", "organization_bans", ["org_id"])
    op.create_index("ix_organization_bans_guest_id", "organization_bans", ["guest_id"])

    # ── org_id on event_log ───────────────────────────────────────────────────
    op.add_column("event_log", sa.Column("org_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_event_log_org_id", "event_log", "organizations", ["org_id"], ["id"])
    op.create_index("ix_event_log_org_id", "event_log", ["org_id"])

    # Backfill: events with a meetup inherit the meetup's org
    op.execute("""
        UPDATE event_log el
        SET org_id = m.org_id
        FROM meetups m
        WHERE el.meetup_id = m.id
    """)

    # Backfill: BAN/UNBAN events without a meetup -> Club Vanta (only existing org)
    op.execute("""
        UPDATE event_log
        SET org_id = (SELECT id FROM organizations WHERE slug = 'club-vanta')
        WHERE event_type IN ('BAN', 'UNBAN') AND org_id IS NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_event_log_org_id", "event_log")
    op.drop_constraint("fk_event_log_org_id", "event_log", type_="foreignkey")
    op.drop_column("event_log", "org_id")
    op.drop_index("ix_organization_bans_guest_id", "organization_bans")
    op.drop_index("ix_organization_bans_org_id", "organization_bans")
    op.drop_table("organization_bans")
