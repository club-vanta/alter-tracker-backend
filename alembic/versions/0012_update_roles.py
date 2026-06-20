"""update global roles: add USER and SITE_ADMIN, retire STAFF and ADMIN

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-20

Global roles now represent only the two extremes:
  - USER: a regular approved account. Actual capabilities come from user_organizations.role.
  - SITE_ADMIN: superadmin, bypasses all org checks, can create organizations.

STAFF and ADMIN are removed from user_roles because they are now per-org roles
stored as VARCHAR in user_organizations.role (already migrated in 0010).

All existing users (both former STAFF and ADMIN) become USER globally.
Their per-org privileges were already set up in migration 0010.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new global roles
    op.execute("INSERT INTO user_roles (name) VALUES ('USER'), ('SITE_ADMIN')")

    # All existing users become USER (their per-org roles are in user_organizations)
    op.execute("""
        UPDATE users
        SET role_id = (SELECT id FROM user_roles WHERE name = 'USER')
    """)

    # Remove the old global roles - they are now per-org concepts only
    op.execute("DELETE FROM user_roles WHERE name IN ('STAFF', 'ADMIN')")


def downgrade() -> None:
    # Re-add old roles
    op.execute("INSERT INTO user_roles (name) VALUES ('STAFF'), ('ADMIN')")

    # Move all non-SITE_ADMIN users back to STAFF
    op.execute("""
        UPDATE users
        SET role_id = (SELECT id FROM user_roles WHERE name = 'STAFF')
        WHERE role_id != (SELECT id FROM user_roles WHERE name = 'SITE_ADMIN')
    """)

    # Remove new roles (SITE_ADMIN users need manual handling before downgrade)
    op.execute("DELETE FROM user_roles WHERE name IN ('USER', 'SITE_ADMIN')")
