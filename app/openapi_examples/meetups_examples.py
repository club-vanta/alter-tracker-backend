"""
OpenAPI examples for meetups router endpoints.

Endpoints:
  POST  /organizations/{org_id}/meetups/
  GET   /organizations/{org_id}/meetups/
  GET   /organizations/{org_id}/meetups/{id}
  POST  /organizations/{org_id}/meetups/{id}/sync
  GET   /organizations/{org_id}/meetups/{id}/guests
  POST  /organizations/{org_id}/meetups/{id}/guests/{mazmo_user_id}/add-walkin
  POST  /organizations/{org_id}/meetups/{id}/guests/{mazmo_user_id}/checkin
  PATCH /organizations/{org_id}/meetups/{id}/guests/{mazmo_user_id}/undo-checkin
  PATCH /organizations/{org_id}/meetups/{id}/finalize
  PATCH /organizations/{org_id}/meetups/{id}/unfinalize
"""

from typing import Any

from app.openapi_examples._constants import (
    CHECKIN_RESPONSE_EXAMPLE,
    GUEST_IN_ORG_BANNED,
    GUEST_IN_ORG_NOT_BANNED,
    GUEST_NORMAL_2,
    MEETUP_EXAMPLE,
    MEETUP_EXAMPLE_2,
    MEETUP_EXAMPLE_FINALIZED,
    MEETUP_GUEST_WALKIN,
    PAYMENT_RESPONSE_EXAMPLE,
    RSVP_ARRIVED,
    RSVP_NOT_ARRIVED,
    SYNC_RESPONSE_EXAMPLE,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_404_meetup,
    error_404_rsvp,
    error_404_rsvp_for_payment,
    error_404_rsvp_for_undo_payment,
    error_404_walkin_guest_not_in_system,
    error_409_already_checked_in,
    error_409_already_paid,
    error_409_checkin_payment_required,
    error_409_duplicate_meetup,
    error_409_meetup_finalized,
    error_409_meetup_not_finalized,
    error_409_not_checked_in,
    error_409_not_paid,
    error_409_payment_not_required,
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
    "new_paid_meetup": {
        "summary": "Create a new paid-entrance meetup",
        "description": "requires_payment=true blocks check-in until an org admin marks each guest as paid",
        "value": {
            "name": "Alter Buenos Aires - Abril 2024 (Pago)",
            "mazmo_meetup_url": "https://mazmo.net/eventos-reuniones-argentina/alter-bsas-paid-4321",
            "requires_payment": True,
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
                        "description": "guest.is_banned reflects org-scoped ban status for this event",
                        "value": {
                            "total": 2,
                            "guests": [
                                {"guest": GUEST_IN_ORG_NOT_BANNED, "rsvp": RSVP_ARRIVED},
                                {"guest": {**GUEST_NORMAL_2, "is_banned": False}, "rsvp": RSVP_NOT_ARRIVED},
                            ],
                        },
                    },
                    "with_banned_guest": {
                        "summary": "Guest list with a banned attendee",
                        "description": "is_banned=true lets the frontend show a warning at check-in",
                        "value": {
                            "total": 2,
                            "guests": [
                                {"guest": GUEST_IN_ORG_NOT_BANNED, "rsvp": RSVP_ARRIVED},
                                {"guest": GUEST_IN_ORG_BANNED, "rsvp": RSVP_NOT_ARRIVED},
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
    **error_409_checkin_payment_required(),
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
                        "value": GUEST_IN_ORG_NOT_BANNED,
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

# ── PATCH /meetups/{id}/guests/{mazmo_user_id}/payment ────────────────────────

MARK_PAYMENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest marked as paid",
        "content": {
            "application/json": {
                "examples": {
                    "paid": {
                        "summary": "Payment recorded",
                        "description": "Returns who marked the payment and when",
                        "value": PAYMENT_RESPONSE_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_404_rsvp_for_payment(),
    **error_409_already_paid(),
    **error_409_meetup_finalized(),
    **error_409_payment_not_required(),
}

# ── PATCH /meetups/{id}/guests/{mazmo_user_id}/payment/undo ───────────────────

UNDO_PAYMENT_REQUEST_EXAMPLES: dict[str, Any] = {
    "undo_mistake": {
        "summary": "Undo an accidental payment mark",
        "description": "A reason is required for the audit trail",
        "value": {
            "reason": "Marked the wrong guest as paid by mistake",
        },
    },
}

UNDO_PAYMENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Payment mark undone",
        "content": {
            "application/json": {
                "examples": {
                    "undone": {
                        "summary": "Payment mark reversed",
                        "description": "Guest is back to not-paid state",
                        "value": GUEST_NORMAL_2,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
    **error_404_rsvp_for_undo_payment(),
    **error_409_not_paid(),
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
