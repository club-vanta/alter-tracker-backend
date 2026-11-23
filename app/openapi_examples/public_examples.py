"""
OpenAPI examples for the public router.

Endpoints:
  GET /public/alters
"""

from typing import Any

from app.openapi_examples._error_responses import ResponsesDict, merge_error_responses

PUBLIC_ALTERS_EXAMPLE = {
    "alters": [
        {
            "short_name": "Discreet",
            "title": "Alter San Telmo - D I S C R E E T",
            "date": "2026-12-12T22:00:00Z",
            "mazmo_url": "https://mazmo.net/club-vanta/alter-san-telmo-d-i-s-c-r-e-e-t-123456",
            "cover_url": "https://cdn.mazmo.net/covers/discreet.jpg",
            "excerpt": "Una noche para charlar sin apuro, con la discrecion de siempre.",
        }
    ]
}


def error_404_public_alters() -> ResponsesDict:
    """404 - The public org is not configured or does not exist."""
    return {
        404: {
            "description": "Public alters org not configured or not found",
            "content": {
                "application/json": {
                    "examples": {
                        "setting_unset": {
                            "summary": "PUBLIC_ALTERS_ORG_SLUG is not set",
                            "value": {
                                "detail": (
                                    "Cannot list alters: PUBLIC_ALTERS_ORG_SLUG is not set on the server. "
                                    "Set it to the slug of the organization to expose and restart the backend."
                                )
                            },
                        },
                        "slug_not_found": {
                            "summary": "Configured slug matches no organization",
                            "value": {
                                "detail": (
                                    "Cannot list alters: no organization with slug 'club-vanta' exists, "
                                    "but PUBLIC_ALTERS_ORG_SLUG points to it. Fix the setting or "
                                    "create the organization."
                                )
                            },
                        },
                    }
                }
            },
        }
    }


LIST_PUBLIC_ALTERS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Alters of the configured organization, newest first",
            "content": {
                "application/json": {
                    "examples": {
                        "alters": {
                            "summary": "One alter",
                            "value": PUBLIC_ALTERS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_404_public_alters(),
)
