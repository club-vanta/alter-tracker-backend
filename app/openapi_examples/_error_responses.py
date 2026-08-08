"""
Reusable error response factories for OpenAPI examples.

These factories generate consistent error response examples across all routers.
Each factory returns a dict suitable for use in FastAPI's `responses` parameter.

Usage:
    @router.post("/ban", responses={**error_404_guest(), **error_409_already_banned()})
"""

from typing import Any

# Type alias for FastAPI responses dict
ResponsesDict = dict[int | str, dict[str, Any]]

# ── Generic HTTP Errors ───────────────────────────────────────────────────────


def error_401_invalid_credentials() -> ResponsesDict:
    """401 - Invalid or missing JWT. Use on all endpoints that require authentication."""
    return {
        401: {
            "description": "Invalid or missing credentials",
            "content": {
                "application/json": {
                    "examples": {
                        "missing_token": {
                            "summary": "No token provided",
                            "value": {"detail": "Not authenticated"},
                        },
                        "invalid_token": {
                            "summary": "Invalid or expired JWT",
                            "value": {"detail": "Could not validate credentials"},
                        },
                    }
                }
            },
        }
    }


def error_401_wrong_password() -> ResponsesDict:
    """401 - Wrong username or password. Only for POST /auth/token (form-based login)."""
    return {
        401: {
            "description": "Wrong username or password",
            "content": {
                "application/json": {
                    "examples": {
                        "wrong_password": {
                            "summary": "Wrong username or password",
                            "value": {"detail": "Incorrect username or password."},
                        },
                    }
                }
            },
        }
    }


def error_403_not_approved() -> ResponsesDict:
    """403 - User account not yet approved by admin."""
    return {
        403: {
            "description": "Account not approved or disabled",
            "content": {
                "application/json": {
                    "examples": {
                        "pending_approval": {
                            "summary": "Account pending approval",
                            "value": {"detail": "Your account is pending admin approval. Please try again later."},
                        },
                        "account_disabled": {
                            "summary": "Account disabled",
                            "value": {"detail": "Your account has been disabled."},
                        },
                    }
                }
            },
        }
    }


def error_403_admin_required() -> ResponsesDict:
    """403 - Admin role required for this operation."""
    return {
        403: {
            "description": "Admin role required",
            "content": {
                "application/json": {
                    "examples": {
                        "admin_required": {
                            "summary": "Org admin privileges needed",
                            "value": {"detail": "Organization admin privileges required."},
                        },
                    }
                }
            },
        }
    }


def error_403_site_admin_required() -> ResponsesDict:
    """403 - Site admin role required for this operation."""
    return {
        403: {
            "description": "Site admin role required",
            "content": {
                "application/json": {
                    "examples": {
                        "site_admin_required": {
                            "summary": "Site admin privileges needed",
                            "value": {"detail": "Site admin privileges required."},
                        },
                    }
                }
            },
        }
    }


def error_403_staff_own_events_only() -> ResponsesDict:
    """403 - Staff can only view their own events."""
    return {
        403: {
            "description": "Staff can only view their own events",
            "content": {
                "application/json": {
                    "examples": {
                        "not_own_events": {
                            "summary": "Cannot view another staff member's events",
                            "value": {
                                "detail": (
                                    "You can only view your own events. "
                                    "You are user id=2, but requested events for user id=5. "
                                    "Admins can view any staff member's events."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


# ── 400 Bad Request Errors ────────────────────────────────────────────────────


def error_400_self_approve_revoke() -> ResponsesDict:
    """400 - Admin cannot revoke their own approval. Only for PATCH /staff/{id}/approve."""
    return {
        400: {
            "description": "Invalid self-operation",
            "content": {
                "application/json": {
                    "examples": {
                        "self_revoke": {
                            "summary": "Cannot revoke own approval",
                            "value": {"detail": "Admins cannot revoke their own approval."},
                        },
                    }
                }
            },
        }
    }


def error_400_self_role() -> ResponsesDict:
    """400 - Admin cannot demote themselves. Only for PATCH /staff/{id}/role."""
    return {
        400: {
            "description": "Invalid self-operation",
            "content": {
                "application/json": {
                    "examples": {
                        "self_demote": {
                            "summary": "Cannot demote yourself",
                            "value": {"detail": "Admins cannot demote themselves."},
                        },
                    }
                }
            },
        }
    }


def error_400_self_disable() -> ResponsesDict:
    """400 - Admin cannot disable their own account. Only for PATCH /staff/{id}/disable."""
    return {
        400: {
            "description": "Invalid self-operation",
            "content": {
                "application/json": {
                    "examples": {
                        "self_disable": {
                            "summary": "Cannot disable own account",
                            "value": {"detail": "Admins cannot disable their own account."},
                        },
                    }
                }
            },
        }
    }


def error_400_invalid_event_type() -> ResponsesDict:
    """400 - Invalid event type in filter."""
    return {
        400: {
            "description": "Invalid event type filter",
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_type": {
                            "summary": "Unknown event type",
                            "value": {
                                "detail": (
                                    "Invalid event type 'INVALID' in filter. "
                                    "Valid types are: BAN, CHECK_IN, UNBAN, UNDO_CHECK_IN. "
                                    "You can combine multiple types with commas, e.g. ?type=CHECK_IN,UNDO_CHECK_IN"
                                )
                            },
                        },
                    }
                }
            },
        }
    }


# ── 404 Not Found Errors ──────────────────────────────────────────────────────


def error_404_staff() -> ResponsesDict:
    """404 - Staff user not found."""
    return {
        404: {
            "description": "Staff user not found",
            "content": {
                "application/json": {
                    "examples": {
                        "staff_not_found": {
                            "summary": "Staff ID does not exist",
                            "value": {"detail": "Staff user not found."},
                        },
                    }
                }
            },
        }
    }


def error_404_guest() -> ResponsesDict:
    """404 - Guest not found in database."""
    return {
        404: {
            "description": "Guest not found",
            "content": {
                "application/json": {
                    "examples": {
                        "guest_not_found": {
                            "summary": "Guest not in system",
                            "value": {
                                "detail": (
                                    "Guest with id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 does not exist in our "
                                    "database. Guests are added when they RSVP to a meetup and we sync from "
                                    "Mazmo, or when registered manually via POST /guests/mazmo or "
                                    "POST /guests/manual. Try POST /meetups/{meetup_id}/sync, or verify the id."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_404_guest_mazmo_handle() -> ResponsesDict:
    """404 - No guest with that Mazmo handle. Only for GET /guests/by-mazmo-handle/{mazmo_handle}."""
    return {
        404: {
            "description": "Guest not found by Mazmo handle",
            "content": {
                "application/json": {
                    "examples": {
                        "guest_handle_not_found": {
                            "summary": "Handle not registered in this system",
                            "value": {
                                "detail": (
                                    "No guest with Mazmo handle 'unknownuser' found in the system. "
                                    "They may not have RSVPed to any meetup yet, or may not have a Mazmo "
                                    "account at all. Use POST /guests/mazmo to register them by handle, "
                                    "or POST /guests/manual if they don't use Mazmo."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_404_mazmo_username_not_found() -> ResponsesDict:
    """404 - Username not found on Mazmo."""
    return {
        404: {
            "description": "Username not found on Mazmo",
            "content": {
                "application/json": {
                    "examples": {
                        "username_not_found": {
                            "summary": "Mazmo doesn't know this username",
                            "value": {
                                "detail": (
                                    "Username 'unknownuser' was not found on Mazmo. Check the spelling and try again."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_404_meetup() -> ResponsesDict:
    """404 - Meetup not found."""
    return {
        404: {
            "description": "Meetup not found",
            "content": {
                "application/json": {
                    "examples": {
                        "meetup_not_found": {
                            "summary": "Meetup UUID does not exist",
                            "value": {
                                "detail": (
                                    "Meetup with id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 does not exist. "
                                    "It may have been deleted, or the UUID is incorrect. "
                                    "List all meetups via GET /meetups/ to find the correct ID."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_404_org() -> ResponsesDict:
    """404 - Organization not found."""
    return {
        404: {
            "description": "Organization not found",
            "content": {
                "application/json": {
                    "examples": {
                        "org_not_found": {
                            "summary": "Organization UUID does not exist",
                            "value": {"detail": "Organization c3d4e5f6-a7b8-9012-cdef-123456789012 not found."},
                        },
                    }
                }
            },
        }
    }


def error_404_rsvp(action: str = "check in") -> ResponsesDict:
    """404 - Guest not RSVPed to this meetup. `action` customizes the leading verb (e.g. "mark payment")."""
    return {
        404: {
            "description": "Guest not RSVPed to meetup",
            "content": {
                "application/json": {
                    "examples": {
                        "not_rsvped": {
                            "summary": "Guest didn't RSVP to this meetup",
                            "value": {
                                "detail": (
                                    f"Cannot {action}: guest mazmo_user_id=12345 is not RSVPed. "
                                    f"Either: (1) they haven't RSVPed on Mazmo yet, "
                                    f"(2) RSVP list needs syncing - try POST /meetups/{{meetup_id}}/sync, "
                                    f"or (3) wrong ID - check GET /meetups/{{meetup_id}}/guests."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


# ── 409 Conflict Errors ───────────────────────────────────────────────────────


def error_409_username_taken() -> ResponsesDict:
    """409 - Username already exists."""
    return {
        409: {
            "description": "Username already taken",
            "content": {
                "application/json": {
                    "examples": {
                        "username_taken": {
                            "summary": "Username already registered",
                            "value": {"detail": "Username 'maria_admin' is already taken."},
                        },
                    }
                }
            },
        }
    }


def error_409_already_disabled() -> ResponsesDict:
    """409 - Account is already disabled."""
    return {
        409: {
            "description": "Account already disabled",
            "content": {
                "application/json": {
                    "examples": {
                        "already_disabled": {
                            "summary": "Cannot disable twice",
                            "value": {"detail": "This account is already disabled."},
                        },
                    }
                }
            },
        }
    }


def error_409_not_disabled() -> ResponsesDict:
    """409 - Account is not currently disabled."""
    return {
        409: {
            "description": "Account not disabled",
            "content": {
                "application/json": {
                    "examples": {
                        "not_disabled": {
                            "summary": "Cannot enable an active account",
                            "value": {"detail": "This account is not disabled."},
                        },
                    }
                }
            },
        }
    }


def error_409_already_banned() -> ResponsesDict:
    """409 - Guest is already banned."""
    return {
        409: {
            "description": "Guest already banned",
            "content": {
                "application/json": {
                    "examples": {
                        "already_banned": {
                            "summary": "Cannot ban twice",
                            "value": {
                                "detail": (
                                    "Cannot ban guest: 'usuario_problematico' (mazmo_user_id=99999) "
                                    "is already banned. They were banned on 2024-03-20 18:30:00+00:00 "
                                    "for reason: 'Comportamiento agresivo'. To update the ban reason, "
                                    "unban first via PATCH /guests/99999/unban, then re-ban."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_not_banned() -> ResponsesDict:
    """409 - Guest is not currently banned."""
    return {
        409: {
            "description": "Guest not banned",
            "content": {
                "application/json": {
                    "examples": {
                        "not_banned": {
                            "summary": "Cannot unban a non-banned guest",
                            "value": {
                                "detail": (
                                    "Cannot unban guest: 'fiestero_feliz' (mazmo_user_id=12345) "
                                    "is not currently banned. They may have been unbanned by another admin. "
                                    "Check audit trail at GET /events/guests/12345."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_already_checked_in() -> ResponsesDict:
    """409 - Guest already checked in."""
    return {
        409: {
            "description": "Guest already checked in",
            "content": {
                "application/json": {
                    "examples": {
                        "already_checked_in": {
                            "summary": "Cannot check in twice",
                            "value": {
                                "detail": (
                                    "Cannot check in: guest 'fiestero_feliz' (mazmo_user_id=12345) "
                                    "is already checked in. They arrived at 2024-03-23 20:05:32+00:00 "
                                    "(arrival #1). "
                                    "To undo this, use PATCH /meetups/{meetup_id}/guests/12345/undo-checkin."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_not_checked_in() -> ResponsesDict:
    """409 - Guest not currently checked in."""
    return {
        409: {
            "description": "Guest not checked in",
            "content": {
                "application/json": {
                    "examples": {
                        "not_checked_in": {
                            "summary": "Cannot undo non-existent check-in",
                            "value": {
                                "detail": (
                                    "Cannot undo check-in: guest 'fiestero_feliz' "
                                    "(mazmo_user_id=12345) is not currently checked in. "
                                    "They may have been un-checked by someone else. "
                                    "See event log: GET /events/meetups/{meetup_id}?type=CHECK_IN,UNDO_CHECK_IN"
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_payment_not_required() -> ResponsesDict:
    """409 - Meetup does not require payment."""
    return {
        409: {
            "description": "Meetup does not require payment",
            "content": {
                "application/json": {
                    "examples": {
                        "payment_not_required": {
                            "summary": "Cannot mark payment on a free event",
                            "value": {
                                "detail": (
                                    "Cannot mark payment: meetup 'Alter Córdoba - Marzo 2024' "
                                    "does not require payment. "
                                    "Enable it first via PATCH /meetups/{meetup_id}/enable-payment."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_already_paid() -> ResponsesDict:
    """409 - Guest already marked as paid."""
    return {
        409: {
            "description": "Guest already paid",
            "content": {
                "application/json": {
                    "examples": {
                        "already_paid": {
                            "summary": "Cannot mark payment twice",
                            "value": {
                                "detail": (
                                    "Cannot mark payment: guest 'fiestero_feliz' (mazmo_user_id=12345) "
                                    "already paid at 2024-03-23 19:30:00+00:00. "
                                    "To undo this, use PATCH /meetups/{meetup_id}/guests/12345/payment/undo."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_not_paid() -> ResponsesDict:
    """409 - Guest is not currently marked as paid."""
    return {
        409: {
            "description": "Guest not marked as paid",
            "content": {
                "application/json": {
                    "examples": {
                        "not_paid": {
                            "summary": "Cannot undo a non-existent payment mark",
                            "value": {
                                "detail": (
                                    "Cannot undo payment: guest 'fiestero_feliz' "
                                    "(mazmo_user_id=12345) is not currently marked as paid. "
                                    "They may have had their payment undone by someone else. "
                                    "See event log: GET /events/meetups/{meetup_id}"
                                    "?type=PAYMENT_RECORDED,PAYMENT_REVOKED"
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_checkin_payment_required() -> ResponsesDict:
    """409 - Guest hasn't paid the entrance fee for a paid meetup. Only for POST .../checkin."""
    return {
        409: {
            "description": "Guest has not paid",
            "content": {
                "application/json": {
                    "examples": {
                        "payment_required": {
                            "summary": "Cannot check in an unpaid guest at a paid event",
                            "value": {
                                "detail": (
                                    "Cannot check in: guest 'fiestero_feliz' (mazmo_user_id=12345) "
                                    "has not paid the entrance fee for 'Alter Buenos Aires - Abril 2024 (Pago)'. "
                                    "Mark the payment first via PATCH /meetups/{meetup_id}/guests/12345/payment."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_payment_already_enabled() -> ResponsesDict:
    """409 - Meetup already requires payment. Only for PATCH .../enable-payment."""
    return {
        409: {
            "description": "Meetup already requires payment",
            "content": {
                "application/json": {
                    "examples": {
                        "already_enabled": {
                            "summary": "Cannot enable payment twice",
                            "value": {
                                "detail": (
                                    "Cannot enable payment: meetup 'Alter Córdoba - Marzo 2024' "
                                    "already requires payment. "
                                    "To switch it back to free, use PATCH /meetups/{meetup_id}/disable-payment."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_payment_already_disabled() -> ResponsesDict:
    """409 - Meetup does not currently require payment. Only for PATCH .../disable-payment."""
    return {
        409: {
            "description": "Meetup does not require payment",
            "content": {
                "application/json": {
                    "examples": {
                        "already_disabled": {
                            "summary": "Cannot disable payment twice",
                            "value": {
                                "detail": (
                                    "Cannot disable payment: meetup 'Alter Córdoba - Marzo 2024' "
                                    "does not currently require payment. "
                                    "To make it a paid event, use PATCH /meetups/{meetup_id}/enable-payment."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_meetup_finalized() -> ResponsesDict:
    """409 - Meetup is already finalized."""
    return {
        409: {
            "description": "Meetup already finalized",
            "content": {
                "application/json": {
                    "examples": {
                        "meetup_finalized": {
                            "summary": "Cannot modify a finalized meetup",
                            "value": {
                                "detail": (
                                    "Cannot perform this action: meetup 'Alter Córdoba - Marzo 2024' "
                                    "was finalized on 2024-03-23 23:59:00+00:00. "
                                    "Finalized meetups no longer accept check-ins or syncs."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_meetup_not_finalized() -> ResponsesDict:
    """409 - Meetup is not currently finalized."""
    return {
        409: {
            "description": "Meetup not finalized",
            "content": {
                "application/json": {
                    "examples": {
                        "meetup_not_finalized": {
                            "summary": "Cannot un-finalize a non-finalized meetup",
                            "value": {
                                "detail": (
                                    "Cannot un-finalize: meetup 'Alter Córdoba - Marzo 2024' "
                                    "is not currently finalized."
                                ),
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_guest_already_exists() -> ResponsesDict:
    """409 - Guest with this mazmo_user_id already exists."""
    return {
        409: {
            "description": "Guest already exists",
            "content": {
                "application/json": {
                    "examples": {
                        "guest_exists": {
                            "summary": "mazmo_user_id already in system",
                            "value": {
                                "detail": (
                                    "Cannot create guest: mazmo_user_id=12345 already exists "
                                    "in the system as 'Juan El Fiestero' (id=e5f6a7b8-c9d0-1234-efab-345678901234). "
                                    "If you want to add them to a meetup, use "
                                    "POST /organizations/{org_id}/meetups/{meetup_id}/guests/"
                                    "e5f6a7b8-c9d0-1234-efab-345678901234/add-walkin."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_walkin_already_rsvped() -> ResponsesDict:
    """409 - Guest already has an RSVP for this meetup."""
    return {
        409: {
            "description": "Guest already RSVPed",
            "content": {
                "application/json": {
                    "examples": {
                        "already_rsvped": {
                            "summary": "Guest already has an RSVP (Mazmo sync or previous walk-in)",
                            "value": {
                                "detail": (
                                    "Cannot add walk-in: guest 'fiestero_feliz' (mazmo_user_id=12345) "
                                    "already has an RSVP for this meetup. "
                                    "They may have RSVPed on Mazmo or been added as a walk-in previously."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_404_walkin_guest_not_in_system() -> ResponsesDict:
    """404 - Walk-in guest not found in system (never synced or manually created)."""
    return {
        404: {
            "description": "Guest not found in system",
            "content": {
                "application/json": {
                    "examples": {
                        "guest_not_in_system": {
                            "summary": "Guest has no record in the system",
                            "value": {
                                "detail": (
                                    "Cannot add walk-in: guest mazmo_user_id=55555 does not exist in the system. "
                                    "Register them first via POST /guests/ (username lookup) or sync a meetup they've RSVPed to. "  # noqa: E501
                                    "Search known guests via GET /guests/ to find the correct ID."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_409_duplicate_meetup() -> ResponsesDict:
    """409 - Meetup with this Mazmo URL already exists."""
    return {
        409: {
            "description": "Duplicate meetup URL",
            "content": {
                "application/json": {
                    "examples": {
                        "duplicate_url": {
                            "summary": "Mazmo URL already tracked",
                            "value": {
                                "detail": (
                                    "Cannot create meetup: a meetup with this Mazmo URL already exists. "
                                    "Existing meetup: id=a1b2c3d4-e5f6-7890-abcd-ef1234567890, "
                                    "name='Alter Córdoba - Marzo 2024', date=2024-03-23 19:00:00+00:00. "
                                    "Each Mazmo event can only be tracked once."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


# ── 422 Validation Errors ─────────────────────────────────────────────────────


def error_422_validation() -> ResponsesDict:
    """422 - Generic validation error (missing required field). Use when no more specific variant applies."""
    return {
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "examples": {
                        "missing_required_field": {
                            "summary": "Missing required field",
                            "value": {
                                "detail": [
                                    {
                                        "type": "missing",
                                        "loc": ["body", "field_name"],
                                        "msg": "Field required",
                                        "input": {},
                                    }
                                ]
                            },
                        },
                    }
                }
            },
        }
    }


def error_422_validation_password() -> ResponsesDict:
    """422 - Password too short. Only for POST /auth/register."""
    return {
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "examples": {
                        "password_too_short": {
                            "summary": "Password too short",
                            "value": {
                                "detail": [
                                    {
                                        "type": "string_too_short",
                                        "loc": ["body", "password"],
                                        "msg": "String should have at least 15 characters",
                                        "input": "short",
                                        "ctx": {"min_length": 15},
                                    }
                                ]
                            },
                        },
                    }
                }
            },
        }
    }


def error_422_validation_url() -> ResponsesDict:
    """422 - Invalid Mazmo URL format. Only for POST /meetups/."""
    return {
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_url": {
                            "summary": "Invalid Mazmo URL format",
                            "value": {
                                "detail": [
                                    {
                                        "type": "value_error",
                                        "loc": ["body", "mazmo_meetup_url"],
                                        "msg": "Value error, URL must match pattern: https://mazmo.net/{community}/{thread-slug}",
                                        "input": "https://invalid-url.com/meetup",
                                    }
                                ]
                            },
                        },
                    }
                }
            },
        }
    }


def error_422_validation_reason() -> ResponsesDict:
    """422 - Reason too short. For endpoints that require a reason field (ban, disable, undo-checkin)."""
    return {
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "examples": {
                        "reason_too_short": {
                            "summary": "Reason too short",
                            "value": {
                                "detail": [
                                    {
                                        "type": "string_too_short",
                                        "loc": ["body", "reason"],
                                        "msg": "String should have at least 5 characters",
                                        "input": "bad",
                                        "ctx": {"min_length": 5},
                                    }
                                ]
                            },
                        },
                    }
                }
            },
        }
    }


def error_422_validation_username() -> ResponsesDict:
    """422 - Empty username. Only for POST /guests/ (create by username)."""
    return {
        422: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "examples": {
                        "username_empty": {
                            "summary": "Username cannot be empty",
                            "value": {
                                "detail": [
                                    {
                                        "type": "string_too_short",
                                        "loc": ["body", "username"],
                                        "msg": "String should have at least 1 character",
                                        "input": "",
                                        "ctx": {"min_length": 1},
                                    }
                                ]
                            },
                        },
                    }
                }
            },
        }
    }


# ── 502/504 Gateway Errors ────────────────────────────────────────────────────


def error_502_mazmo_create_meetup() -> ResponsesDict:
    """502 - Mazmo API returned an error during meetup creation. Only for POST /meetups/."""
    return {
        502: {
            "description": "Mazmo API error",
            "content": {
                "application/json": {
                    "examples": {
                        "mazmo_error": {
                            "summary": "Mazmo returned an error",
                            "value": {
                                "detail": (
                                    "Cannot create meetup: Mazmo API returned an error. "
                                    "Error: HTTP 404 - Event not found. "
                                    "This could mean the Mazmo URL is invalid or the event doesn't exist. "
                                    "Verify the URL is correct and points to a valid Mazmo event page."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_502_mazmo_sync() -> ResponsesDict:
    """502 - Mazmo API returned an error during sync. Only for POST /meetups/{id}/sync."""
    return {
        502: {
            "description": "Mazmo API error",
            "content": {
                "application/json": {
                    "examples": {
                        "mazmo_api_error": {
                            "summary": "Mazmo returned an HTTP error",
                            "value": {
                                "detail": (
                                    "Sync failed: Mazmo API returned HTTP error 404. "
                                    "The event may have been deleted on Mazmo, or their API is having issues. "
                                    "Check if the meetup URL is still valid on Mazmo's website."
                                )
                            },
                        },
                        "mazmo_parse_error": {
                            "summary": "Mazmo response parse failed",
                            "value": {
                                "detail": (
                                    "Sync failed: Mazmo returned data in an unexpected format. "
                                    "Error: Missing 'date' field in response. "
                                    "This could indicate Mazmo changed their API. Please report this issue."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_504_mazmo_create_meetup() -> ResponsesDict:
    """504 - Mazmo unreachable during meetup creation. Only for POST /meetups/."""
    return {
        504: {
            "description": "Mazmo API timeout",
            "content": {
                "application/json": {
                    "examples": {
                        "mazmo_timeout": {
                            "summary": "Could not connect to Mazmo",
                            "value": {
                                "detail": (
                                    "Cannot create meetup: failed to connect to Mazmo API. "
                                    "This is likely a temporary network issue. Error: Connection timed out. "
                                    "Try again in a few moments, or check if Mazmo is experiencing an outage."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_504_mazmo_sync() -> ResponsesDict:
    """504 - Mazmo unreachable during sync. Only for POST /meetups/{id}/sync."""
    return {
        504: {
            "description": "Mazmo API timeout",
            "content": {
                "application/json": {
                    "examples": {
                        "mazmo_timeout": {
                            "summary": "Could not connect to Mazmo",
                            "value": {
                                "detail": (
                                    "Sync failed: could not connect to Mazmo API. "
                                    "This is likely a temporary network issue. Error: Connection timed out. "
                                    "Try again in a few moments."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


def error_504_mazmo_create_guest() -> ResponsesDict:
    """504 - Mazmo unreachable during guest creation. Only for POST /guests/."""
    return {
        504: {
            "description": "Mazmo API timeout",
            "content": {
                "application/json": {
                    "examples": {
                        "mazmo_timeout": {
                            "summary": "Could not connect to Mazmo",
                            "value": {
                                "detail": (
                                    "Cannot create guest: failed to connect to Mazmo API. "
                                    "Error: Connection timed out. Try again in a few moments."
                                )
                            },
                        },
                    }
                }
            },
        }
    }
