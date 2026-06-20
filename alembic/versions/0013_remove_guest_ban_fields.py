"""migrate existing bans to organization_bans and drop ban fields from guests

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-20

Ban data that was stored directly on the guests table is moved to
organization_bans under Club Vanta (the only existing organization).
After migration the ban columns are dropped from guests.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Copy active bans to organization_bans under Club Vanta
    op.execute("""
        INSERT INTO organization_bans (org_id, guest_id, banned_by_id, banned_at, reason)
        SELECT
            (SELECT id FROM organizations WHERE slug = 'club-vanta'),
            g.mazmo_user_id,
            g.banned_by_id,
            COALESCE(g.banned_at, NOW()),
            COALESCE(g.banned_reason, 'Migrated from legacy global ban')
        FROM guests g
        WHERE g.is_banned = true
        ON CONFLICT DO NOTHING
    """)

    # Drop the now-redundant ban columns from guests
    op.drop_constraint("fk_guests_banned_by_id", "guests", type_="foreignkey")
    op.drop_index("ix_guests_is_banned", table_name="guests")
    op.drop_column("guests", "banned_reason")
    op.drop_column("guests", "banned_by_id")
    op.drop_column("guests", "banned_at")
    op.drop_column("guests", "is_banned")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column("guests", sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_guests_is_banned", "guests", ["is_banned"])
    op.add_column("guests", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("guests", sa.Column("banned_by_id", sa.Integer(), nullable=True))
    op.add_column("guests", sa.Column("banned_reason", sa.String(500), nullable=True))
    op.create_foreign_key("fk_guests_banned_by_id", "guests", "users", ["banned_by_id"], ["id"], ondelete="SET NULL")
