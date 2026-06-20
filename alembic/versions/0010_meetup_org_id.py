"""add org_id to meetups, create Club Vanta as first org

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-20

Data migration:
  - Creates "Club Vanta" as the first organization (slug: club-vanta).
  - Assigns all existing meetups to Club Vanta.
  - Adds all existing ADMIN users to Club Vanta with ADMIN org-role.
  - Adds all existing STAFF users to Club Vanta with STAFF org-role.
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
    # Add org_id as nullable first so we can backfill before enforcing NOT NULL
    op.add_column("meetups", sa.Column("org_id", UUID(as_uuid=True), nullable=True))

    # Create Club Vanta - the first (and currently only) organization
    op.execute("INSERT INTO organizations (name, slug) VALUES ('Club Vanta', 'club-vanta')")

    # Assign all existing meetups to Club Vanta
    op.execute("""
        UPDATE meetups
        SET org_id = (SELECT id FROM organizations WHERE slug = 'club-vanta')
    """)

    # Add all existing ADMIN users to Club Vanta with ADMIN org-role
    op.execute("""
        INSERT INTO user_organizations (user_id, org_id, role)
        SELECT u.id, o.id, 'ADMIN'
        FROM users u, organizations o
        WHERE o.slug = 'club-vanta'
          AND u.role_id = (SELECT id FROM user_roles WHERE name = 'ADMIN')
        ON CONFLICT DO NOTHING
    """)

    # Add all existing STAFF users to Club Vanta with STAFF org-role
    op.execute("""
        INSERT INTO user_organizations (user_id, org_id, role)
        SELECT u.id, o.id, 'STAFF'
        FROM users u, organizations o
        WHERE o.slug = 'club-vanta'
          AND u.role_id = (SELECT id FROM user_roles WHERE name = 'STAFF')
        ON CONFLICT DO NOTHING
    """)

    # Now that all rows have an org_id, make the column NOT NULL
    op.alter_column("meetups", "org_id", nullable=False)

    op.create_foreign_key("fk_meetups_org_id", "meetups", "organizations", ["org_id"], ["id"])
    op.create_index("ix_meetups_org_id", "meetups", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_meetups_org_id", "meetups")
    op.drop_constraint("fk_meetups_org_id", "meetups", type_="foreignkey")
    op.drop_column("meetups", "org_id")
    op.execute("DELETE FROM user_organizations")
    op.execute("DELETE FROM organizations WHERE slug = 'club-vanta'")
