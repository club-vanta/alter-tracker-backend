"""
Shared realistic values for OpenAPI examples.

These constants provide consistent, realistic data across all example modules.
When the same user "maria_admin" appears in auth, staff, and events examples,
it's the same person - making the docs feel coherent and believable.
"""


# -- Timestamps ---------------------------------------------------------------

# Fixed timestamps for reproducible examples
TIMESTAMP_2024_03_15 = "2024-03-15T20:00:00Z"
TIMESTAMP_2024_03_20 = "2024-03-20T18:30:00Z"
TIMESTAMP_2024_03_22 = "2024-03-22T21:15:00Z"
TIMESTAMP_2024_03_23 = "2024-03-23T19:00:00Z"
TIMESTAMP_2024_03_23_CHECKIN = "2024-03-23T20:05:32Z"

# -- JWT Example --------------------------------------------------------------

# A realistic-looking (but invalid) JWT for examples.
# Decoded payload: {"sub": "maria_admin", "role": "SITE_ADMIN", "exp": 1711234567}
EXAMPLE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJtYXJpYV9hZG1pbiIsInJvbGUiOiJTSVRFX0FETUlOIiwiZXhwIjoxNzExMjM0NTY3fQ."
    "fake_signature_for_documentation_purposes_only"
)

# -- Organization UUIDs (referenced in user examples below) ------------------

ORG_UUID = "c3d4e5f6-a7b8-9012-cdef-123456789012"
ORG_UUID_2 = "d4e5f6a7-b8c9-0123-defa-234567890123"

# -- Staff Users --------------------------------------------------------------

ADMIN_USER = {
    "id": 1,
    "username": "maria_admin",
    "is_approved": True,
    "role": {"id": 2, "name": "SITE_ADMIN"},
    "created_at": TIMESTAMP_2024_03_15,
    "is_disabled": False,
    "disabled_at": None,
    "disabled_reason": None,
    "org_memberships": [
        {"org_id": ORG_UUID, "org_name": "Alter Buenos Aires", "role": "ADMIN"},
        {"org_id": ORG_UUID_2, "org_name": "Club Vanta", "role": "ADMIN"},
    ],
}

STAFF_USER_APPROVED = {
    "id": 2,
    "username": "carlos_staff",
    "is_approved": True,
    "role": {"id": 1, "name": "USER"},
    "created_at": TIMESTAMP_2024_03_20,
    "is_disabled": False,
    "disabled_at": None,
    "disabled_reason": None,
}

STAFF_USER_PENDING = {
    "id": 3,
    "username": "nuevo_voluntario",
    "is_approved": False,
    "role": {"id": 1, "name": "USER"},
    "created_at": TIMESTAMP_2024_03_22,
    "is_disabled": False,
    "disabled_at": None,
    "disabled_reason": None,
}

STAFF_USER_DISABLED = {
    "id": 4,
    "username": "ex_voluntario",
    "is_approved": True,
    "role": {"id": 1, "name": "USER"},
    "created_at": TIMESTAMP_2024_03_15,
    "is_disabled": True,
    "disabled_at": TIMESTAMP_2024_03_22,
    "disabled_reason": "Left the organization",
}

# -- Guests -------------------------------------------------------------------

# GuestPublic: identity only, no ban status (bans are per-org)
GUEST_NORMAL = {
    "mazmo_user_id": 12345,
    "username": "fiestero_feliz",
    "displayname": "Juan El Fiestero",
}

GUEST_NORMAL_2 = {
    "mazmo_user_id": 12346,
    "username": "bailarina_nocturna",
    "displayname": "Ana Bailarina",
}

# GuestWithBanPublic: identity + org-scoped ban flag (used in meetup guest lists)
GUEST_IN_ORG_NOT_BANNED = {
    **GUEST_NORMAL,
    "is_banned": False,
}

GUEST_IN_ORG_BANNED = {
    "mazmo_user_id": 99999,
    "username": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "is_banned": True,
}

# BannedGuestPublic: identity + ban metadata (used in org banned list)
GUEST_BANNED_FULL = {
    "mazmo_user_id": 99999,
    "username": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "banned_at": TIMESTAMP_2024_03_20,
    "banned_reason": "Comportamiento agresivo con otros asistentes en el evento del 20/03",
    "banned_by_id": 1,
}

# -- Organizations ------------------------------------------------------------

ORG_EXAMPLE = {
    "id": ORG_UUID,
    "name": "Alter Buenos Aires",
    "slug": "alter-bsas",
    "created_at": TIMESTAMP_2024_03_15,
}

ORG_EXAMPLE_2 = {
    "id": ORG_UUID_2,
    "name": "Club Vanta",
    "slug": "club-vanta",
    "created_at": TIMESTAMP_2024_03_15,
}

ORG_MEMBER_STAFF = {
    "user_id": 2,
    "org_id": ORG_UUID,
    "role": "STAFF",
}

ORG_MEMBER_ADMIN = {
    "user_id": 1,
    "org_id": ORG_UUID,
    "role": "ADMIN",
}

# -- Meetups ------------------------------------------------------------------

MEETUP_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
MEETUP_UUID_2 = "b2c3d4e5-f6a7-8901-bcde-f12345678901"

MEETUP_EXAMPLE = {
    "id": MEETUP_UUID,
    "name": "Alter Córdoba - Marzo 2024",
    "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217",
    "date": TIMESTAMP_2024_03_23,
    "is_finalized": False,
    "finalized_at": None,
}

MEETUP_EXAMPLE_2 = {
    "id": MEETUP_UUID_2,
    "name": "Alter Buenos Aires - Abril 2024",
    "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-bsas-4320",
    "date": "2024-04-15T20:00:00Z",
    "is_finalized": False,
    "finalized_at": None,
}

MEETUP_EXAMPLE_FINALIZED = {
    "id": MEETUP_UUID,
    "name": "Alter Córdoba - Marzo 2024",
    "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217",
    "date": TIMESTAMP_2024_03_23,
    "is_finalized": True,
    "finalized_at": "2024-03-23T23:59:00Z",
}

# -- RSVP Data ----------------------------------------------------------------

RSVP_NOT_ARRIVED = {
    "rsvp_time": TIMESTAMP_2024_03_20,
    "cancelled_rsvp": False,
    "has_arrived": False,
    "arrival_time": None,
    "arrival_order": None,
    "is_walkin": False,
}

RSVP_ARRIVED = {
    "rsvp_time": TIMESTAMP_2024_03_20,
    "cancelled_rsvp": False,
    "has_arrived": True,
    "arrival_time": TIMESTAMP_2024_03_23_CHECKIN,
    "arrival_order": 1,
    "is_walkin": False,
}

# -- Event Log Entries --------------------------------------------------------

EVENT_CHECKIN = {
    "id": 1,
    "event_type": "CHECK_IN",
    "timestamp": TIMESTAMP_2024_03_23_CHECKIN,
    "actor": {"id": 2, "username": "carlos_staff"},
    "guest": {
        "mazmo_user_id": 12345,
        "username": "fiestero_feliz",
        "displayname": "Juan El Fiestero",
    },
    "meetup_id": MEETUP_UUID,
    "reason": None,
}

EVENT_BAN = {
    "id": 2,
    "event_type": "BAN",
    "timestamp": TIMESTAMP_2024_03_20,
    "actor": {"id": 1, "username": "maria_admin"},
    "guest": {
        "mazmo_user_id": 99999,
        "username": "usuario_problematico",
        "displayname": "Persona Conflictiva",
    },
    "meetup_id": None,
    "reason": "Comportamiento agresivo con otros asistentes en el evento del 20/03",
}

# -- Walk-in RSVP -------------------------------------------------------------

RSVP_WALKIN = {
    "rsvp_time": TIMESTAMP_2024_03_23_CHECKIN,
    "cancelled_rsvp": False,
    "has_arrived": False,
    "arrival_time": None,
    "arrival_order": None,
    "is_walkin": True,
}

MEETUP_GUEST_WALKIN = {
    "guest": {
        "mazmo_user_id": 55555,
        "username": "recien_llegado",
        "displayname": "Recién Llegado",
        "is_banned": False,
    },
    "rsvp": RSVP_WALKIN,
}

# -- Sync Response ------------------------------------------------------------

SYNC_RESPONSE_EXAMPLE = {
    "inserted": 5,
    "skipped": 12,
    "total_in_db": 17,
}

# -- Check-in Response --------------------------------------------------------

CHECKIN_RESPONSE_EXAMPLE = {
    "guest": GUEST_NORMAL,
    "arrival_order": 1,
    "arrival_time": TIMESTAMP_2024_03_23_CHECKIN,
    "checked_in_by": {"id": 2, "username": "carlos_staff"},
}
