"""
OpenAPI examples for guests router endpoints.

Endpoints:
  POST  /guests/                           - Create a guest by Mazmo username (staff+)
  GET   /guests/                           - List all known guests (staff+)
  GET   /guests/{mazmo_user_id}            - Get a single guest by ID (staff+)
  GET   /guests/by-username/{username}     - Get a single guest by username (staff+)

Ban management moved to organizations router:
  GET   /organizations/{org_id}/guests/banned
  PATCH /organizations/{org_id}/guests/{id}/ban
  PATCH /organizations/{org_id}/guests/{id}/unban
"""

from typing import Any

from app.openapi_examples._constants import (
    GUEST_NORMAL,
    GUEST_NORMAL_2,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_404_guest,
    error_404_guest_username,
    error_404_mazmo_username_not_found,
    error_409_guest_already_exists,
    error_422_validation_username,
    error_504_mazmo_create_guest,
)

# -- POST /guests/ ------------------------------------------------------------

CREATE_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "by_username": {
        "summary": "Look up by Mazmo handle",
        "description": "Staff knows the handle but not the numeric ID",
        "value": {"username": "cindydark"},
    },
}

CREATE_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
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
    **error_422_validation_username(),
    **error_504_mazmo_create_guest(),
}

# -- GET /guests/ -------------------------------------------------------------

LIST_GUESTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All known guests (global, no ban context)",
        "content": {
            "application/json": {
                "examples": {
                    "guests_list": {
                        "summary": "Known guests",
                        "description": "Identity only - ban status is org-scoped and not included here",
                        "value": {
                            "total": 2,
                            "guests": [GUEST_NORMAL_2, GUEST_NORMAL],
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

# -- GET /guests/{mazmo_user_id} ----------------------------------------------

GET_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest found",
        "content": {
            "application/json": {
                "examples": {
                    "guest": {
                        "summary": "Guest identity",
                        "description": "Ban status is not included - check org-scoped endpoints for ban info",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}

# -- GET /guests/by-username/{username} ---------------------------------------

GET_GUEST_BY_USERNAME_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest found",
        "content": {
            "application/json": {
                "examples": {
                    "guest": {
                        "summary": "Guest identity",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest_username(),
}
