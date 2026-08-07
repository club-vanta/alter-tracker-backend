"""guest identity decoupled from mazmo + instagram_username

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-07

Guests could not exist without a Mazmo account: guests.mazmo_user_id was
the primary key, and every table that references a guest (meetup_rsvps,
organization_bans, event_log) had its guest_id FK pointing at it.

This migration:
  - Adds guests.id (UUID) as the new primary key.
  - Makes guests.mazmo_user_id nullable (still UNIQUE + indexed).
  - Renames guests.username to guests.mazmo_handle, makes it nullable.
  - Adds guests.instagram_username (nullable, free text).
  - Retargets meetup_rsvps.guest_id, organization_bans.guest_id, and
    event_log.guest_id from guests.mazmo_user_id to guests.id (UUID).

Existing rows keep their identity: guests.id is backfilled with a fresh
UUID per row, and every FK column is backfilled by joining the old
mazmo_user_id value against the new id before the old column is dropped.

See downgrade() for why this migration cannot be reverted once a guest
with mazmo_user_id IS NULL exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- 0. Drop dependent FKs before touching guests PK -------------------------
    op.drop_constraint("meetup_rsvps_guest_id_fkey", "meetup_rsvps", type_="foreignkey")
    op.drop_constraint("organization_bans_guest_id_fkey", "organization_bans", type_="foreignkey")
    op.drop_constraint("event_log_guest_id_fkey", "event_log", type_="foreignkey")

    # -- 1. guests: add UUID surrogate PK --------------------------------------
    op.add_column("guests", sa.Column("id", sa.UUID(), nullable=True))
    op.execute("UPDATE guests SET id = gen_random_uuid()")
    op.alter_column("guests", "id", nullable=False)

    op.drop_constraint("guests_pkey", "guests", type_="primary")
    op.create_primary_key("guests_pkey", "guests", ["id"])

    # mazmo_user_id is no longer the PK: nullable, but still unique.
    op.alter_column("guests", "mazmo_user_id", nullable=True)
    op.create_index("ix_guests_mazmo_user_id", "guests", ["mazmo_user_id"], unique=True)

    # username -> mazmo_handle, nullable (a manual guest has no handle).
    op.alter_column("guests", "username", new_column_name="mazmo_handle", nullable=True)
    op.drop_index("ix_guests_username", table_name="guests")
    op.create_index("ix_guests_mazmo_handle", "guests", ["mazmo_handle"])

    op.add_column("guests", sa.Column("instagram_username", sa.String(length=64), nullable=True))

    # -- 2. meetup_rsvps: guest_id is part of the composite PK -----------------
    op.add_column("meetup_rsvps", sa.Column("guest_id_new", sa.UUID(), nullable=True))
    op.execute("""
        UPDATE meetup_rsvps mr
        SET guest_id_new = g.id
        FROM guests g
        WHERE mr.guest_id = g.mazmo_user_id
    """)
    op.alter_column("meetup_rsvps", "guest_id_new", nullable=False)
    op.drop_constraint("meetup_rsvps_pkey", "meetup_rsvps", type_="primary")
    op.drop_column("meetup_rsvps", "guest_id")
    op.alter_column("meetup_rsvps", "guest_id_new", new_column_name="guest_id")
    op.create_primary_key("meetup_rsvps_pkey", "meetup_rsvps", ["meetup_id", "guest_id"])
    op.create_foreign_key(
        "meetup_rsvps_guest_id_fkey", "meetup_rsvps", "guests", ["guest_id"], ["id"], ondelete="CASCADE"
    )

    # -- 3. organization_bans: guest_id is a plain indexed FK column -----------
    op.add_column("organization_bans", sa.Column("guest_id_new", sa.UUID(), nullable=True))
    op.execute("""
        UPDATE organization_bans ob
        SET guest_id_new = g.id
        FROM guests g
        WHERE ob.guest_id = g.mazmo_user_id
    """)
    op.alter_column("organization_bans", "guest_id_new", nullable=False)
    op.drop_index("ix_organization_bans_guest_id", table_name="organization_bans")
    op.drop_column("organization_bans", "guest_id")
    op.alter_column("organization_bans", "guest_id_new", new_column_name="guest_id")
    op.create_index("ix_organization_bans_guest_id", "organization_bans", ["guest_id"])
    op.create_foreign_key(
        "organization_bans_guest_id_fkey", "organization_bans", "guests", ["guest_id"], ["id"], ondelete="CASCADE"
    )

    # -- 4. event_log: guest_id is nullable, plain indexed FK column -----------
    op.add_column("event_log", sa.Column("guest_id_new", sa.UUID(), nullable=True))
    op.execute("""
        UPDATE event_log el
        SET guest_id_new = g.id
        FROM guests g
        WHERE el.guest_id = g.mazmo_user_id
    """)
    # No NOT NULL here: event_log.guest_id stays nullable (non-guest events).
    op.drop_index("ix_event_log_guest_meetup", table_name="event_log")
    op.drop_index("ix_event_log_guest_id", table_name="event_log")
    op.drop_column("event_log", "guest_id")
    op.alter_column("event_log", "guest_id_new", new_column_name="guest_id")
    op.create_index("ix_event_log_guest_id", "event_log", ["guest_id"])
    op.create_index("ix_event_log_guest_meetup", "event_log", ["guest_id", "meetup_id"])
    op.create_foreign_key("event_log_guest_id_fkey", "event_log", "guests", ["guest_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    """
    Reverts guests.id back to mazmo_user_id as the PK.

    This is only possible if every guest still has a mazmo_user_id. A
    guest created via POST /guests/manual and never linked has
    mazmo_user_id IS NULL, which cannot become a PK value - reverting
    would either violate NOT NULL or silently drop that guest. Neither
    is acceptable, so this downgrade refuses to run rather than doing
    either silently.
    """
    manual_guests = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM guests WHERE mazmo_user_id IS NULL")).scalar()
    if manual_guests:
        raise RuntimeError(
            f"Cannot downgrade migration 0016: {manual_guests} guest(s) have no "
            f"mazmo_user_id (created via POST /guests/manual and never linked via "
            f"PATCH /guests/{{id}}/link-mazmo). Reverting mazmo_user_id to a "
            f"NOT-NULL primary key would break or drop them. Link or remove those "
            f"guests before downgrading."
        )

    # -- 0. Drop dependent FKs first to allow guests changes ---------------------
    op.drop_constraint("event_log_guest_id_fkey", "event_log", type_="foreignkey")
    op.drop_constraint("organization_bans_guest_id_fkey", "organization_bans", type_="foreignkey")
    op.drop_constraint("meetup_rsvps_guest_id_fkey", "meetup_rsvps", type_="foreignkey")

    # -- event_log: process while guests.id still exists -------------------------
    op.drop_index("ix_event_log_guest_meetup", table_name="event_log")
    op.drop_index("ix_event_log_guest_id", table_name="event_log")
    op.add_column("event_log", sa.Column("guest_id_old", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE event_log el
        SET guest_id_old = g.mazmo_user_id
        FROM guests g
        WHERE el.guest_id = g.id
    """)
    op.drop_column("event_log", "guest_id")
    op.alter_column("event_log", "guest_id_old", new_column_name="guest_id")
    op.create_index("ix_event_log_guest_id", "event_log", ["guest_id"])
    op.create_index("ix_event_log_guest_meetup", "event_log", ["guest_id", "meetup_id"])

    # -- organization_bans: process while guests.id still exists -----------------
    op.drop_index("ix_organization_bans_guest_id", table_name="organization_bans")
    op.add_column("organization_bans", sa.Column("guest_id_old", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE organization_bans ob
        SET guest_id_old = g.mazmo_user_id
        FROM guests g
        WHERE ob.guest_id = g.id
    """)
    op.alter_column("organization_bans", "guest_id_old", nullable=False)
    op.drop_column("organization_bans", "guest_id")
    op.alter_column("organization_bans", "guest_id_old", new_column_name="guest_id")
    op.create_index("ix_organization_bans_guest_id", "organization_bans", ["guest_id"])

    # -- meetup_rsvps: process while guests.id still exists ----------------------
    op.drop_constraint("meetup_rsvps_pkey", "meetup_rsvps", type_="primary")
    op.add_column("meetup_rsvps", sa.Column("guest_id_old", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE meetup_rsvps mr
        SET guest_id_old = g.mazmo_user_id
        FROM guests g
        WHERE mr.guest_id = g.id
    """)
    op.alter_column("meetup_rsvps", "guest_id_old", nullable=False)
    op.drop_column("meetup_rsvps", "guest_id")
    op.alter_column("meetup_rsvps", "guest_id_old", new_column_name="guest_id")
    op.create_primary_key("meetup_rsvps_pkey", "meetup_rsvps", ["meetup_id", "guest_id"])

    # -- guests: restore to original state -----------------------------------------
    op.drop_column("guests", "instagram_username")
    op.drop_index("ix_guests_mazmo_handle", table_name="guests")
    op.alter_column("guests", "mazmo_handle", new_column_name="username", nullable=False)
    op.create_index("ix_guests_username", "guests", ["username"])
    op.drop_index("ix_guests_mazmo_user_id", table_name="guests")
    op.alter_column("guests", "mazmo_user_id", nullable=False)
    op.drop_constraint("guests_pkey", "guests", type_="primary")
    op.create_primary_key("guests_pkey", "guests", ["mazmo_user_id"])
    op.drop_column("guests", "id")

    # -- Recreate FKs now that guests.mazmo_user_id is the PK -------------------
    op.create_foreign_key(
        "event_log_guest_id_fkey", "event_log", "guests", ["guest_id"], ["mazmo_user_id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "organization_bans_guest_id_fkey",
        "organization_bans",
        "guests",
        ["guest_id"],
        ["mazmo_user_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "meetup_rsvps_guest_id_fkey", "meetup_rsvps", "guests", ["guest_id"], ["mazmo_user_id"], ondelete="CASCADE"
    )
