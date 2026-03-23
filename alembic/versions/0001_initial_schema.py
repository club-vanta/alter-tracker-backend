"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-17 00:00:00.000000

Schema decisions:
  - `user_roles` is a plain lookup table (id, name) seeded with STAFF and ADMIN.
    `users.role_id` is a foreign key to it. This is more flexible than a
    Postgres ENUM — adding a new role is just an INSERT, not an ALTER TYPE.

  - `PossibleRoles` in Python is used for validation only, not stored as a DB type.

  - `arrival_order_seq` is a standalone sequence so the check-in endpoint can
    call nextval() inside a single atomic UPDATE.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. user_roles ─────────────────────────────────────────────────────────
    op.create_table(
        "user_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.AutoString(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # Seed the two built-in roles immediately
    op.execute("INSERT INTO user_roles (name) VALUES ('STAFF'), ('ADMIN')")

    # ── 2. users ────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sqlmodel.AutoString(length=64), nullable=False),
        sa.Column("hashed_password", sqlmodel.AutoString(), nullable=False),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["role_id"], ["user_roles.id"]),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ── 3. arrival_order sequence ─────────────────────────────────────────────
    op.execute("CREATE SEQUENCE IF NOT EXISTS arrival_order_seq START 1 INCREMENT 1 NO CYCLE")

    # ── 4. guests ─────────────────────────────────────────────────────────────
    op.create_table(
        "guests",
        sa.Column("mazmo_user_id", sa.Integer(), nullable=False),
        sa.Column("username", sqlmodel.AutoString(), nullable=False),
        sa.Column("displayname", sqlmodel.AutoString(), nullable=False),
        sa.Column("rsvp_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("has_arrived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cancelled_rsvp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("arrival_order", sa.Integer(), nullable=True, unique=True),
        sa.PrimaryKeyConstraint("mazmo_user_id"),
    )
    op.create_index("ix_guests_username", "guests", ["username"])
    op.create_index("ix_guests_has_arrived", "guests", ["has_arrived"])

    # Wire sequence ownership so DROP TABLE guests cascades cleanly
    op.execute("ALTER SEQUENCE arrival_order_seq OWNED BY guests.arrival_order")


def downgrade() -> None:
    op.drop_table("guests")
    op.execute("DROP SEQUENCE IF EXISTS arrival_order_seq")
    op.drop_table("users")
    op.drop_table("user_roles")
