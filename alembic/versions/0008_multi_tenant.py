"""multi-tenant: organizations, org-scoped users/meetups, GuestBan table

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-25 00:00:00.000000

Introduces multi-org support:
  - New `organizations` table, seeded with Club Vanta (id=1)
  - `users.org_id` FK — all existing users backfilled to org 1
  - `meetups.org_id` FK — all existing meetups backfilled to org 1
  - New `guest_bans` table (mazmo_user_id, org_id) composite PK
    replaces the ban columns that lived directly on `guests`
  - Existing ban data migrated from `guests` → `guest_bans` (org_id=1)
  - Ban columns dropped from `guests`
  - `event_log.org_id` FK (nullable, backfilled to 1 for existing rows)
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. organizations ─────────────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.execute(
        sa.text(
            "INSERT INTO organizations (id, name, slug, created_at) "
            "VALUES (1, 'Club Vanta', 'club-vanta', :now)"
        ).bindparams(now=datetime.now(UTC))
    )

    # ── 2. users.org_id ──────────────────────────────────────────────────────
    op.add_column("users", sa.Column("org_id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE users SET org_id = 1"))
    op.alter_column("users", "org_id", nullable=False)
    op.create_foreign_key("fk_users_org_id", "users", "organizations", ["org_id"], ["id"])

    # ── 3. meetups.org_id ────────────────────────────────────────────────────
    op.add_column("meetups", sa.Column("org_id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE meetups SET org_id = 1"))
    op.alter_column("meetups", "org_id", nullable=False)
    op.create_foreign_key("fk_meetups_org_id", "meetups", "organizations", ["org_id"], ["id"])

    # ── 4. guest_bans table ──────────────────────────────────────────────────
    op.create_table(
        "guest_bans",
        sa.Column("mazmo_user_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("banned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("banned_by_id", sa.Integer(), nullable=False),
        sa.Column("banned_reason", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["mazmo_user_id"], ["guests.mazmo_user_id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["banned_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("mazmo_user_id", "org_id"),
    )

    # ── 5. migrate existing ban data → guest_bans ────────────────────────────
    op.execute(
        sa.text(
            "INSERT INTO guest_bans (mazmo_user_id, org_id, banned_at, banned_by_id, banned_reason) "
            "SELECT mazmo_user_id, 1, banned_at, banned_by_id, banned_reason "
            "FROM guests "
            "WHERE is_banned = TRUE "
            "  AND banned_at IS NOT NULL "
            "  AND banned_by_id IS NOT NULL "
            "  AND banned_reason IS NOT NULL"
        )
    )

    # ── 6. drop ban columns from guests ─────────────────────────────────────
    op.drop_index("ix_guests_is_banned", table_name="guests")
    op.drop_column("guests", "is_banned")
    op.drop_column("guests", "banned_at")
    op.drop_column("guests", "banned_by_id")
    op.drop_column("guests", "banned_reason")

    # ── 7. event_log.org_id ──────────────────────────────────────────────────
    op.add_column("event_log", sa.Column("org_id", sa.Integer(), nullable=True))
    op.execute(sa.text("UPDATE event_log SET org_id = 1"))
    op.create_foreign_key("fk_event_log_org_id", "event_log", "organizations", ["org_id"], ["id"])


def downgrade() -> None:
    # ── 7. event_log.org_id ──────────────────────────────────────────────────
    op.drop_constraint("fk_event_log_org_id", "event_log", type_="foreignkey")
    op.drop_column("event_log", "org_id")

    # ── 6. restore ban columns on guests ────────────────────────────────────
    op.add_column("guests", sa.Column("banned_reason", sa.String(length=500), nullable=True))
    op.add_column("guests", sa.Column("banned_by_id", sa.Integer(), nullable=True))
    op.add_column("guests", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("guests", sa.Column("is_banned", sa.Boolean(), nullable=False, server_default="false"))
    op.create_index("ix_guests_is_banned", "guests", ["is_banned"])

    # ── 5. restore ban data from guest_bans → guests ─────────────────────────
    op.execute(
        sa.text(
            "UPDATE guests g "
            "SET is_banned = TRUE, "
            "    banned_at = gb.banned_at, "
            "    banned_by_id = gb.banned_by_id, "
            "    banned_reason = gb.banned_reason "
            "FROM guest_bans gb "
            "WHERE g.mazmo_user_id = gb.mazmo_user_id AND gb.org_id = 1"
        )
    )

    # ── 4. guest_bans ────────────────────────────────────────────────────────
    op.drop_table("guest_bans")

    # ── 3. meetups.org_id ────────────────────────────────────────────────────
    op.drop_constraint("fk_meetups_org_id", "meetups", type_="foreignkey")
    op.drop_column("meetups", "org_id")

    # ── 2. users.org_id ──────────────────────────────────────────────────────
    op.drop_constraint("fk_users_org_id", "users", type_="foreignkey")
    op.drop_column("users", "org_id")

    # ── 1. organizations ─────────────────────────────────────────────────────
    op.drop_table("organizations")
