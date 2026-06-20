"""add recovery code fields to users

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-16 00:00:00.000000

Adds three columns to support the admin-generated 6-digit password recovery flow:
  - recovery_code: the 6-digit code itself
  - recovery_code_created_at: when it was generated (for 72h expiry check)
  - recovery_code_used: whether it has already been consumed
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("recovery_code", sa.String(6), nullable=True))
    op.add_column(
        "users",
        sa.Column("recovery_code_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "recovery_code_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "recovery_code_used")
    op.drop_column("users", "recovery_code_created_at")
    op.drop_column("users", "recovery_code")
