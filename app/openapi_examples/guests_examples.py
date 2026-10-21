"""
OpenAPI examples for guests router endpoints.

Endpoints:
  POST  /guests/mazmo                          - Create a guest by Mazmo username
  POST  /guests/manual                         - Create a guest without a Mazmo account
  GET   /guests/                               - List all known guests, optional ?q= search
  POST  /guests/refresh-mazmo                  - Refresh every Mazmo-linked guest from Mazmo
  GET   /guests/{guest_id}                     - Get a single guest by internal id
  GET   /guests/by-mazmo-handle/{mazmo_handle} - Get a single guest by Mazmo handle
  PATCH /guests/{guest_id}/link-mazmo          - Link an existing guest to a Mazmo account
  PATCH /guests/{guest_id}/unlink-mazmo        - Unlink a guest's Mazmo account
  PATCH /guests/{guest_id}                     - Edit displayname/instagram_username

Ban management lives in the organizations router:
  GET   /organizations/{org_id}/guests/banned
  PATCH /organizations/{org_id}/guests/{id}/ban
  PATCH /organizations/{org_id}/guests/{id}/unban
"""

from typing import Any

from app.openapi_examples._constants import (
    GUEST_MANUAL,
    GUEST_NORMAL,
    GUEST_NORMAL_2,
    TIMESTAMP_2024_03_15,
    TIMESTAMP_2024_03_20,
    TIMESTAMP_2024_03_23,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_not_approved,
    error_404_guest,
    error_404_guest_mazmo_handle,
    error_404_mazmo_username_not_found,
    error_409_guest_already_exists,
    error_422_validation_username,
    error_504_mazmo_create_guest,
)

# -- POST /guests/mazmo ---------------------------------------------------------

CREATE_MAZMO_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "by_username": {
        "summary": "Look up by Mazmo handle",
        "description": "Staff knows the handle but not the numeric ID",
        "value": {"username": "cindydark"},
    },
    "with_instagram": {
        "summary": "Look up by Mazmo handle, with Instagram",
        "value": {"username": "cindydark", "instagram_username": "cindy.dark"},
    },
}

CREATE_MAZMO_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Guest created from Mazmo profile",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Guest successfully registered from Mazmo lookup",
                        "value": GUEST_NORMAL,
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

# -- POST /guests/manual ---------------------------------------------------------

CREATE_MANUAL_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "basic": {
        "summary": "Guest without a Mazmo account",
        "description": "Someone at the door who doesn't have a Mazmo profile",
        "value": {"displayname": "Recien Llegado Sin Mazmo"},
    },
    "with_instagram": {
        "summary": "With Instagram handle",
        "value": {"displayname": "Recien Llegado Sin Mazmo", "instagram_username": "recien.llegado"},
    },
}

CREATE_MANUAL_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Guest created without a Mazmo account",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Manual guest successfully registered",
                        "value": GUEST_MANUAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
}

# -- GET /guests/ -----------------------------------------------------------------

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

# -- POST /guests/refresh-mazmo ----------------------------------------------------

REFRESH_MAZMO_GUESTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Refresh completed",
        "content": {
            "application/json": {
                "examples": {
                    "all_found": {
                        "summary": "Every linked guest refreshed",
                        "value": {"total": 312, "refreshed": 312, "not_found": 0},
                    },
                    "some_missing": {
                        "summary": "Mazmo left some guests out",
                        "description": "not_found guests keep their previous data",
                        "value": {"total": 312, "refreshed": 309, "not_found": 3},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    502: {
        "description": "Mazmo API error",
        "content": {
            "application/json": {
                "examples": {
                    "mazmo_api_error": {
                        "summary": "Mazmo returned an HTTP error",
                        "value": {
                            "detail": (
                                "Cannot refresh guests: Mazmo API returned HTTP error 503. "
                                "No guest data was changed. Try again in a few minutes."
                            )
                        },
                    },
                }
            }
        },
    },
    504: {
        "description": "Mazmo API timeout",
        "content": {
            "application/json": {
                "examples": {
                    "mazmo_timeout": {
                        "summary": "Could not connect to Mazmo",
                        "value": {
                            "detail": (
                                "Cannot refresh guests: could not connect to Mazmo API. "
                                "Error: Connection timed out. No guest data was changed. Try again in a few moments."
                            )
                        },
                    },
                }
            }
        },
    },
}

# -- GET /guests/{guest_id} --------------------------------------------------------

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

# -- GET /guests/by-mazmo-handle/{mazmo_handle} -------------------------------------

GET_GUEST_BY_MAZMO_HANDLE_RESPONSES: dict[int | str, dict[str, Any]] = {
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
    **error_404_guest_mazmo_handle(),
}

# -- PATCH /guests/{guest_id}/link-mazmo ----------------------------------------

LINK_MAZMO_REQUEST_EXAMPLES: dict[str, Any] = {
    "link": {
        "summary": "Link to a Mazmo account",
        "value": {"username": "cindydark"},
    },
}

LINK_MAZMO_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest linked to Mazmo",
        "content": {
            "application/json": {
                "examples": {
                    "linked": {
                        "summary": "Guest now has a Mazmo account attached",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
    **error_404_mazmo_username_not_found(),
    **error_409_guest_already_exists(),
    **error_504_mazmo_create_guest(),
}

# -- PATCH /guests/{guest_id}/unlink-mazmo --------------------------------------

UNLINK_MAZMO_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest unlinked from Mazmo",
        "content": {
            "application/json": {
                "examples": {
                    "unlinked": {
                        "summary": "Guest no longer has a Mazmo account attached",
                        "value": GUEST_MANUAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}

# -- PATCH /guests/{guest_id} -----------------------------------------------------

UPDATE_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "update_name": {
        "summary": "Fix a typo in the display name",
        "value": {"displayname": "Nombre Corregido"},
    },
    "add_instagram": {
        "summary": "Add an Instagram handle after the fact",
        "value": {"instagram_username": "nuevo.handle"},
    },
    "clear_instagram": {
        "summary": "Remove the Instagram handle",
        "description": "Sending instagram_username as null clears it. Omitting the key entirely leaves it untouched.",
        "value": {"instagram_username": None},
    },
}

UPDATE_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest updated",
        "content": {
            "application/json": {
                "examples": {
                    "updated": {
                        "summary": "Guest identity after the edit",
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

# -- GET /guests/{guest_id}/displayname-history -----------------------------------

GET_DISPLAYNAME_HISTORY_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest's full displayname history, newest first",
        "content": {
            "application/json": {
                "examples": {
                    "history": {
                        "summary": "A guest whose name changed twice",
                        "value": {
                            "total": 3,
                            "history": [
                                {
                                    "displayname": "Juan P.",
                                    "source": "SYNC",
                                    "recorded_at": TIMESTAMP_2024_03_23,
                                    "actor": None,
                                },
                                {
                                    "displayname": "Juan Perez",
                                    "source": "MANUAL_EDIT",
                                    "recorded_at": TIMESTAMP_2024_03_20,
                                    "actor": {"id": 2, "username": "carlos_staff"},
                                },
                                {
                                    "displayname": "Juan",
                                    "source": "SYNC",
                                    "recorded_at": TIMESTAMP_2024_03_15,
                                    "actor": None,
                                },
                            ],
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
