"""create user_organizations join table

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-20

Per-org roles (STAFF, ADMIN) live here as a VARCHAR, separate from the
global user_roles table. A user can be ADMIN in org A and STAFF in org B.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "0010"
down_revision: str = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_organizations",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "org_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_organizations_org_id", "user_organizations", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_user_organizations_org_id", "user_organizations")
    op.drop_table("user_organizations")
