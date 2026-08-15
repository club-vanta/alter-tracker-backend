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

GUEST_UUID = "e5f6a7b8-c9d0-1234-efab-345678901234"
GUEST_UUID_2 = "f6a7b8c9-d0e1-2345-fabc-456789012345"

# GuestPublic: identity only, no ban status (bans are per-org)
GUEST_NORMAL = {
    "id": GUEST_UUID,
    "mazmo_user_id": 12345,
    "mazmo_handle": "fiestero_feliz",
    "displayname": "Juan El Fiestero",
    "instagram_username": "juan.fiestero",
}

GUEST_NORMAL_2 = {
    "id": GUEST_UUID_2,
    "mazmo_user_id": 12346,
    "mazmo_handle": "bailarina_nocturna",
    "displayname": "Ana Bailarina",
    "instagram_username": None,
}

GUEST_MANUAL_UUID = "a7b8c9d0-e1f2-3456-abcd-567890123456"

GUEST_MANUAL = {
    "id": GUEST_MANUAL_UUID,
    "mazmo_user_id": None,
    "mazmo_handle": None,
    "displayname": "Recien Llegado Sin Mazmo",
    "instagram_username": "recien.llegado",
}

# GuestWithBanPublic: identity + org-scoped ban flag (used in meetup guest lists)
GUEST_IN_ORG_NOT_BANNED = {
    **GUEST_NORMAL,
    "is_banned": False,
}

GUEST_BANNED_UUID = "b8c9d0e1-f2a3-4567-bcde-678901234567"

GUEST_IN_ORG_BANNED = {
    "id": GUEST_BANNED_UUID,
    "mazmo_user_id": 99999,
    "mazmo_handle": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "instagram_username": None,
    "is_banned": True,
}

# BannedGuestPublic: identity + ban metadata (used in org banned list)
GUEST_BANNED_FULL = {
    "id": GUEST_BANNED_UUID,
    "mazmo_user_id": 99999,
    "mazmo_handle": "usuario_problematico",
    "displayname": "Persona Conflictiva",
    "instagram_username": None,
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
    "username": "carlos_staff",
}

ORG_MEMBER_ADMIN = {
    "user_id": 1,
    "org_id": ORG_UUID,
    "role": "ADMIN",
    "username": "ana_admin",
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
    "requires_payment": False,
}

MEETUP_EXAMPLE_2 = {
    "id": MEETUP_UUID_2,
    "name": "Alter Buenos Aires - Abril 2024",
    "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-bsas-4320",
    "date": "2024-04-15T20:00:00Z",
    "is_finalized": False,
    "finalized_at": None,
    "requires_payment": False,
}

MEETUP_EXAMPLE_FINALIZED = {
    "id": MEETUP_UUID,
    "name": "Alter Córdoba - Marzo 2024",
    "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217",
    "date": TIMESTAMP_2024_03_23,
    "is_finalized": True,
    "finalized_at": "2024-03-23T23:59:00Z",
    "requires_payment": False,
}

MEETUP_EXAMPLE_PAID = {
    "id": MEETUP_UUID_2,
    "name": "Alter Buenos Aires - Abril 2024 (Pago)",
    "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-bsas-paid-4321",
    "date": "2024-04-15T20:00:00Z",
    "is_finalized": False,
    "finalized_at": None,
    "requires_payment": True,
}

# -- RSVP Data ----------------------------------------------------------------

RSVP_NOT_ARRIVED = {
    "rsvp_time": TIMESTAMP_2024_03_20,
    "cancelled_rsvp": False,
    "has_arrived": False,
    "arrival_time": None,
    "arrival_order": None,
    "is_walkin": False,
    "has_paid": False,
    "paid_at": None,
    "guest_type": "NORMAL",
}

RSVP_ARRIVED = {
    "rsvp_time": TIMESTAMP_2024_03_20,
    "cancelled_rsvp": False,
    "has_arrived": True,
    "arrival_time": TIMESTAMP_2024_03_23_CHECKIN,
    "arrival_order": 1,
    "is_walkin": False,
    "has_paid": False,
    "paid_at": None,
    "guest_type": "NORMAL",
}

# -- Event Log Entries --------------------------------------------------------

EVENT_CHECKIN = {
    "id": 1,
    "event_type": "CHECK_IN",
    "timestamp": TIMESTAMP_2024_03_23_CHECKIN,
    "actor": {"id": 2, "username": "carlos_staff"},
    "guest": {
        "id": GUEST_UUID,
        "mazmo_user_id": 12345,
        "mazmo_handle": "fiestero_feliz",
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
        "id": GUEST_BANNED_UUID,
        "mazmo_user_id": 99999,
        "mazmo_handle": "usuario_problematico",
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
    "has_paid": False,
    "paid_at": None,
    "guest_type": "NORMAL",
}

GUEST_WALKIN_UUID = "c9d0e1f2-a3b4-5678-cdef-789012345678"

MEETUP_GUEST_WALKIN = {
    "guest": {
        "id": GUEST_WALKIN_UUID,
        "mazmo_user_id": 55555,
        "mazmo_handle": "recien_llegado",
        "displayname": "Recien Llegado",
        "instagram_username": None,
        "is_banned": False,
    },
    "rsvp": RSVP_WALKIN,
}

MEETUP_GUEST_VENDOR_EXAMPLE = {
    "guest": {
        "id": GUEST_UUID,
        "mazmo_user_id": 12345,
        "mazmo_handle": "fiestero_feliz",
        "displayname": "Juan El Fiestero",
        "instagram_username": None,
        "is_banned": False,
    },
    "rsvp": {
        "rsvp_time": TIMESTAMP_2024_03_20,
        "cancelled_rsvp": False,
        "has_arrived": False,
        "arrival_time": None,
        "arrival_order": None,
        "is_walkin": False,
        "has_paid": False,
        "paid_at": None,
        "guest_type": "VENDOR",
    },
}

MEETUP_STATS_EXAMPLE = {
    "attendance": {
        "total_rsvps": 20,
        "arrived_count": 15,
        "not_arrived_count": 5,
        "walkin_count": 2,
    },
    "cancellations": {
        "cancelled_count": 3,
        "cancelled_but_paid_count": 1,
    },
    "guest_types": {
        "normal_count": 15,
        "invited_count": 2,
        "vendor_count": 2,
        "staff_count": 1,
    },
    "payment": {
        "paid_count": 10,
        "unpaid_count": 5,
        "exempt_from_payment_count": 5,
    },
}

# -- Sync Response ------------------------------------------------------------

SYNC_RESPONSE_EXAMPLE = {
    "inserted": 5,
    "skipped": 12,
    "total_in_db": 17,
}

# -- Check-in Response --------------------------------------------------------

PAYMENT_RESPONSE_EXAMPLE = {
    "guest": GUEST_NORMAL,
    "paid_at": TIMESTAMP_2024_03_23_CHECKIN,
    "paid_by": {"id": 1, "username": "ana_admin"},
}

CHECKIN_RESPONSE_EXAMPLE = {
    "guest": GUEST_NORMAL,
    "arrival_order": 1,
    "arrival_time": TIMESTAMP_2024_03_23_CHECKIN,
    "checked_in_by": {"id": 2, "username": "carlos_staff"},
}
