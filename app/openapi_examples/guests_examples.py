"""
OpenAPI examples for guests router endpoints.

Endpoints:
  POST  /guests/                       - Manually create a guest (staff+)
  POST  /guests/by-username            - Create a guest by Mazmo username (staff+)
  GET   /guests/                       - List all known guests (staff+)
  GET   /guests/banned                 - List all banned guests (staff+)
  GET   /guests/{mazmo_user_id}        - Get a single guest (staff+)
  PATCH /guests/{mazmo_user_id}/ban    - Ban a guest (admin only)
  PATCH /guests/{mazmo_user_id}/unban  - Unban a guest (admin only)
"""

from typing import Any

from app.openapi_examples._constants import (
    GUEST_BANNED_FULL,
    GUEST_NORMAL,
    GUEST_NORMAL_2,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_admin_required,
    error_403_not_approved,
    error_404_guest,
    error_404_mazmo_username_not_found,
    error_409_already_banned,
    error_409_guest_already_exists,
    error_409_not_banned,
    error_422_validation,
    error_504_mazmo_timeout,
)

# ── POST /guests/ ─────────────────────────────────────────────────────────────

CREATE_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "new_attendee": {
        "summary": "Someone who never used Mazmo",
        "description": "Guest shows up at the door with no prior Mazmo history",
        "value": {
            "mazmo_user_id": 55555,
            "username": "recien_llegado",
            "displayname": "Recién Llegado",
        },
    },
    "privacy_conscious": {
        "summary": "Guest who prefers not to appear on Mazmo",
        "description": "Attends the event but doesn't want their RSVP visible on Mazmo publicly",
        "value": {
            "mazmo_user_id": 77777,
            "username": "usuario_discreto",
            "displayname": "Usuario Discreto",
        },
    },
}

CREATE_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Guest created",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Guest successfully registered",
                        "value": {
                            "mazmo_user_id": 55555,
                            "username": "recien_llegado",
                            "displayname": "Recién Llegado",
                            "is_banned": False,
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_409_guest_already_exists(),
    **error_422_validation(),
}

# ── POST /guests/by-username ──────────────────────────────────────────────────

CREATE_GUEST_BY_USERNAME_REQUEST_EXAMPLES: dict[str, Any] = {
    "by_username": {
        "summary": "Look up by Mazmo handle",
        "description": "Staff knows the handle but not the numeric ID",
        "value": {"username": "cindydark"},
    },
}

CREATE_GUEST_BY_USERNAME_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Guest created from Mazmo profile",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Guest successfully registered from Mazmo lookup",
                        "value": {
                            "mazmo_user_id": 39119,
                            "username": "cindydark",
                            "displayname": "⚜️Lissandra⚜️",
                            "is_banned": False,
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_mazmo_username_not_found(),
    **error_409_guest_already_exists(),
    **error_422_validation(),
    **error_504_mazmo_timeout(),
}

# ── GET /guests/ ──────────────────────────────────────────────────────────────

LIST_GUESTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All known guests",
        "content": {
            "application/json": {
                "examples": {
                    "guests_list": {
                        "summary": "Mix of normal and banned guests",
                        "value": {
                            "total": 3,
                            "guests": [
                                GUEST_NORMAL_2,  # alphabetically first by username
                                GUEST_NORMAL,
                                {
                                    **GUEST_NORMAL,
                                    "mazmo_user_id": 99999,
                                    "username": "usuario_problematico",
                                    "displayname": "Persona Conflictiva",
                                    "is_banned": True,
                                },
                            ],
                        },
                    },
                    "empty": {
                        "summary": "No guests yet",
                        "description": "No guests in the system until a meetup is synced",
                        "value": {"total": 0, "guests": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# ── GET /guests/banned ────────────────────────────────────────────────────────

LIST_BANNED_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All banned guests",
        "content": {
            "application/json": {
                "examples": {
                    "banned_list": {
                        "summary": "Banned guests with details",
                        "value": {
                            "total": 1,
                            "guests": [GUEST_BANNED_FULL],
                        },
                    },
                    "no_bans": {
                        "summary": "No banned guests",
                        "value": {"total": 0, "guests": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# ── GET /guests/{mazmo_user_id} ───────────────────────────────────────────────

GET_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest found",
        "content": {
            "application/json": {
                "examples": {
                    "normal_guest": {
                        "summary": "Regular guest",
                        "value": GUEST_NORMAL,
                    },
                    "banned_guest": {
                        "summary": "Banned guest",
                        "description": "is_banned=true indicates this guest is banned",
                        "value": {
                            "mazmo_user_id": 99999,
                            "username": "usuario_problematico",
                            "displayname": "Persona Conflictiva",
                            "is_banned": True,
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}

# ── PATCH /guests/{mazmo_user_id}/ban ─────────────────────────────────────────

BAN_REQUEST_EXAMPLES: dict[str, Any] = {
    "ban_guest": {
        "summary": "Ban a problematic guest",
        "description": "Ban requires a reason for audit purposes (5-500 chars)",
        "value": {
            "reason": "Comportamiento agresivo con otros asistentes en el evento del 20/03",
        },
    },
}

BAN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest banned",
        "content": {
            "application/json": {
                "examples": {
                    "banned": {
                        "summary": "Guest now banned",
                        "value": GUEST_BANNED_FULL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_guest(),
    **error_409_already_banned(),
    **error_422_validation(),
}

# ── PATCH /guests/{mazmo_user_id}/unban ───────────────────────────────────────

UNBAN_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest unbanned",
        "content": {
            "application/json": {
                "examples": {
                    "unbanned": {
                        "summary": "Guest now unbanned",
                        "description": "All ban fields are cleared",
                        "value": {
                            "mazmo_user_id": 99999,
                            "username": "usuario_problematico",
                            "displayname": "Persona Conflictiva",
                            "is_banned": False,
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_guest(),
    **error_409_not_banned(),
}
