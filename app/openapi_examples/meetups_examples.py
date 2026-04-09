"""
OpenAPI examples for meetups router endpoints.

Endpoints:
  POST  /meetups/                                             - Create a new meetup
  GET   /meetups/                                             - List all meetups
  GET   /meetups/{id}                                         - Get a single meetup
  POST  /meetups/{id}/sync                                    - Sync guests from Mazmo
  GET   /meetups/{id}/guests                                  - List guests at meetup
  POST  /meetups/{id}/guests/{mazmo_user_id}/add-walkin       - Add a walk-in guest
  POST  /meetups/{id}/guests/{mazmo_user_id}/checkin          - Check in a guest
  PATCH /meetups/{id}/guests/{mazmo_user_id}/undo-checkin     - Undo check-in
  PATCH /meetups/{id}/finalize                                - Finalize a meetup
"""

from typing import Any

from app.openapi_examples._constants import (
    CHECKIN_RESPONSE_EXAMPLE,
    GUEST_NORMAL,
    GUEST_NORMAL_2,
    MEETUP_EXAMPLE,
    MEETUP_EXAMPLE_2,
    MEETUP_EXAMPLE_FINALIZED,
    MEETUP_GUEST_WALKIN,
    RSVP_ARRIVED,
    RSVP_NOT_ARRIVED,
    SYNC_RESPONSE_EXAMPLE,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_404_meetup,
    error_404_rsvp,
    error_404_walkin_guest_not_in_system,
    error_409_already_checked_in,
    error_409_duplicate_meetup,
    error_409_meetup_finalized,
    error_409_meetup_not_finalized,
    error_409_not_checked_in,
    error_409_walkin_already_rsvped,
    error_422_validation_reason,
    error_422_validation_url,
    error_502_mazmo_create_meetup,
    error_502_mazmo_sync,
    error_504_mazmo_create_meetup,
    error_504_mazmo_sync,
)

# ── POST /meetups/ ────────────────────────────────────────────────────────────

CREATE_MEETUP_REQUEST_EXAMPLES: dict[str, Any] = {
    "new_meetup": {
        "summary": "Create a new meetup",
        "description": "Link a Mazmo event to track attendance",
        "value": {
            "name": "Alter Córdoba - Marzo 2024",
            "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-cordoba-4217",
        },
    },
}

CREATE_MEETUP_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Meetup created",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Meetup created successfully",
                        "description": "The date is fetched from Mazmo automatically",
                        "value": MEETUP_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_409_duplicate_meetup(),
    **error_422_validation_url(),
    **error_502_mazmo_create_meetup(),
    **error_504_mazmo_create_meetup(),
}

# ── GET /meetups/ ─────────────────────────────────────────────────────────────

LIST_MEETUPS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All meetups",
        "content": {
            "application/json": {
                "examples": {
                    "meetups_list": {
                        "summary": "Multiple meetups",
                        "description": "Ordered by date descending (newest first)",
                        "value": {
                            "total": 2,
                            "meetups": [MEETUP_EXAMPLE_2, MEETUP_EXAMPLE],
                        },
                    },
                    "empty": {
                        "summary": "No meetups yet",
                        "value": {"total": 0, "meetups": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# ── GET /meetups/{id} ─────────────────────────────────────────────────────────

GET_MEETUP_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Meetup found",
        "content": {
            "application/json": {
                "examples": {
                    "meetup": {
                        "summary": "Single meetup",
                        "value": MEETUP_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
}

# ── POST /meetups/{id}/sync ───────────────────────────────────────────────────

SYNC_MEETUP_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Sync completed",
        "content": {
            "application/json": {
                "examples": {
                    "sync_result": {
                        "summary": "Typical sync result",
                        "description": "5 new RSVPs, 12 already in DB",
                        "value": SYNC_RESPONSE_EXAMPLE,
                    },
                    "no_new": {
                        "summary": "No new RSVPs",
                        "description": "All RSVPs already synced",
                        "value": {"inserted": 0, "skipped": 17, "total_in_db": 17},
                    },
                    "first_sync": {
                        "summary": "First sync of a meetup",
                        "description": "All RSVPs are new",
                        "value": {"inserted": 25, "skipped": 0, "total_in_db": 25},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_409_meetup_finalized(),
    **error_502_mazmo_sync(),
    **error_504_mazmo_sync(),
}

# ── GET /meetups/{id}/guests ──────────────────────────────────────────────────

LIST_MEETUP_GUESTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guests at this meetup",
        "content": {
            "application/json": {
                "examples": {
                    "guests_list": {
                        "summary": "Mix of arrived and not arrived",
                        "value": {
                            "total": 2,
                            "guests": [
                                {"guest": GUEST_NORMAL, "rsvp": RSVP_ARRIVED},
                                {"guest": GUEST_NORMAL_2, "rsvp": RSVP_NOT_ARRIVED},
                            ],
                        },
                    },
                    "empty": {
                        "summary": "No RSVPs yet",
                        "description": "Sync the meetup to fetch RSVPs from Mazmo",
                        "value": {"total": 0, "guests": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
}

# ── POST /meetups/{id}/guests/{mazmo_user_id}/add-walkin ──────────────────────

ADD_WALKIN_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Walk-in guest added",
        "content": {
            "application/json": {
                "examples": {
                    "walkin_added": {
                        "summary": "Guest added as walk-in",
                        "description": "is_walkin=true distinguishes this RSVP from a Mazmo-synced one",
                        "value": MEETUP_GUEST_WALKIN,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_404_walkin_guest_not_in_system(),
    **error_409_walkin_already_rsvped(),
    **error_409_meetup_finalized(),
}

# ── POST /meetups/{id}/guests/{mazmo_user_id}/checkin ─────────────────────────

CHECKIN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest checked in",
        "content": {
            "application/json": {
                "examples": {
                    "checked_in": {
                        "summary": "Successful check-in",
                        "description": "Returns arrival order and who performed the check-in",
                        "value": CHECKIN_RESPONSE_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_404_rsvp(),
    **error_409_already_checked_in(),
    **error_409_meetup_finalized(),
}

# ── PATCH /meetups/{id}/guests/{mazmo_user_id}/undo-checkin ───────────────────

UNDO_CHECKIN_REQUEST_EXAMPLES: dict[str, Any] = {
    "undo_mistake": {
        "summary": "Undo accidental check-in",
        "description": "A reason is required for the audit trail",
        "value": {
            "reason": "Checked in the wrong person by mistake",
        },
    },
}

UNDO_CHECKIN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Check-in undone",
        "content": {
            "application/json": {
                "examples": {
                    "undone": {
                        "summary": "Check-in reversed",
                        "description": "Guest is back to not-arrived state",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_404_rsvp(),
    **error_409_not_checked_in(),
    **error_422_validation_reason(),
}

# ── PATCH /meetups/{id}/finalize ──────────────────────────────────────────────

FINALIZE_MEETUP_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Meetup finalized",
        "content": {
            "application/json": {
                "examples": {
                    "finalized": {
                        "summary": "Meetup successfully finalized",
                        "description": "No more check-ins or syncs allowed after this",
                        "value": MEETUP_EXAMPLE_FINALIZED,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_409_meetup_finalized(),
}

# ── PATCH /meetups/{id}/unfinalize ────────────────────────────────────────────

UNFINALIZE_MEETUP_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Meetup un-finalized",
        "content": {
            "application/json": {
                "examples": {
                    "unfinalized": {
                        "summary": "Meetup successfully un-finalized",
                        "description": "Check-ins and syncs are allowed again",
                        "value": MEETUP_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_409_meetup_not_finalized(),
}
