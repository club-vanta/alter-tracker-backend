"""
OpenAPI examples for stats router endpoints.

Endpoints:
  GET /organizations/{org_id}/stats/attendance
"""

from typing import Any

from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_admin_required,
    error_404_meetup,
    error_422_validation,
    merge_error_responses,
)

# -- GET /organizations/{org_id}/stats/attendance --

ATTENDANCE_STATS_EXAMPLE = {
    "meetups": [
        {
            "meetup_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "Alter Abril",
            "date": "2026-04-05T22:00:00Z",
            "signed_up": 40,
            "cancelled": 4,
            "attended": 30,
            "walkin": 3,
            "no_show": 6,
        },
        {
            "meetup_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "name": "Alter Abril II",
            "date": "2026-04-19T22:00:00Z",
            "signed_up": 36,
            "cancelled": 2,
            "attended": 31,
            "walkin": 1,
            "no_show": 4,
        },
    ],
    "totals": {
        "events": 2,
        "signed_up": 76,
        "cancelled": 6,
        "attended": 61,
        "walkin": 4,
        "no_show": 10,
        "cancellation_rate": 0.0789,
        "no_show_rate": 0.1429,
        "avg_signed_up_per_event": 38.0,
        "avg_attended_per_event": 30.5,
    },
    "comparison_kind": "PREVIOUS_PERIOD",
    "comparison": {
        "events": 2,
        "signed_up": 70,
        "cancelled": 7,
        "attended": 54,
        "walkin": 2,
        "no_show": 11,
        "cancellation_rate": 0.1,
        "no_show_rate": 0.1746,
        "avg_signed_up_per_event": 35.0,
        "avg_attended_per_event": 27.0,
    },
}

ATTENDANCE_STATS_RESPONSES: dict[int | str, dict[str, Any]] = merge_error_responses(
    {
        200: {
            "description": "Attendance of finalized meetups in the period",
            "content": {
                "application/json": {
                    "examples": {
                        "attendance": {
                            "summary": "Two meetups compared with the previous period",
                            "value": ATTENDANCE_STATS_EXAMPLE,
                        },
                    }
                }
            },
        }
    },
    error_401_invalid_credentials(),
    error_403_admin_required(),
    error_404_meetup(),
    error_422_validation(),
)
