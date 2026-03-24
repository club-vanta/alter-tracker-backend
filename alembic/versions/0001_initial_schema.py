"""initial schema - user and role tables

Revision ID: 0001
Revises:
Create Date: 2026-03-17 00:00:00.000000

Schema decisions:
  - `user_roles` is a plain lookup table (id, name) seeded with STAFF and ADMIN.
    `users.role_id` is a foreign key to it. This is more flexible than a
    Postgres ENUM — adding a new role is just an INSERT, not an ALTER TYPE.

  - `PossibleRoles` in Python is used for validation only, not stored as a DB type.
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


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("user_roles")
