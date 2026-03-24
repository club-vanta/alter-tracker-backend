"""
OpenAPI examples for events router endpoints.

Endpoints:
  GET /events/                - List all events (admin only)
  GET /events/meetups/{id}    - Events at a specific meetup (staff+)
  GET /events/guests/{id}     - Events for a specific guest (staff: bans only, admin: all)
  GET /events/staff/{id}      - Events by a staff member (staff: own only, admin: any)
"""

from typing import Any

from app.openapi_examples._constants import (
    EVENT_BAN,
    EVENT_CHECKIN,
    MEETUP_UUID,
)
from app.openapi_examples._error_responses import (
    error_400_invalid_event_type,
    error_401_invalid_credentials,
    error_403_admin_required,
    error_403_not_approved,
    error_403_staff_own_events_only,
    error_404_guest,
    error_404_meetup,
    error_404_staff,
)

# ── Shared event list response examples ───────────────────────────────────────

_EVENT_LIST_EXAMPLE = {
    "total": 2,
    "limit": 50,
    "offset": 0,
    "events": [EVENT_BAN, EVENT_CHECKIN],
}

_EVENT_LIST_CHECKINS_ONLY = {
    "total": 1,
    "limit": 50,
    "offset": 0,
    "events": [EVENT_CHECKIN],
}

_EVENT_LIST_BANS_ONLY = {
    "total": 1,
    "limit": 50,
    "offset": 0,
    "events": [EVENT_BAN],
}

_EVENT_LIST_EMPTY = {
    "total": 0,
    "limit": 50,
    "offset": 0,
    "events": [],
}

# ── GET /events/ ──────────────────────────────────────────────────────────────

LIST_ALL_EVENTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All events (paginated)",
        "content": {
            "application/json": {
                "examples": {
                    "all_events": {
                        "summary": "Mix of event types",
                        "value": _EVENT_LIST_EXAMPLE,
                    },
                    "filtered_by_type": {
                        "summary": "Filtered to CHECK_IN only",
                        "description": "Using ?type=CHECK_IN",
                        "value": _EVENT_LIST_CHECKINS_ONLY,
                    },
                    "paginated": {
                        "summary": "Page 2 of results",
                        "description": "Using ?offset=50&limit=50",
                        "value": {
                            "total": 150,
                            "limit": 50,
                            "offset": 50,
                            "events": [EVENT_CHECKIN],
                        },
                    },
                    "empty": {
                        "summary": "No events match filter",
                        "value": _EVENT_LIST_EMPTY,
                    },
                }
            }
        },
    },
    **error_400_invalid_event_type(),
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
}

# ── GET /events/meetups/{id} ──────────────────────────────────────────────────

LIST_MEETUP_EVENTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Events at this meetup",
        "content": {
            "application/json": {
                "examples": {
                    "meetup_events": {
                        "summary": "Check-in activity at meetup",
                        "value": {
                            "total": 1,
                            "limit": 50,
                            "offset": 0,
                            "events": [
                                {
                                    **EVENT_CHECKIN,
                                    "meetup_id": MEETUP_UUID,
                                }
                            ],
                        },
                    },
                    "no_activity": {
                        "summary": "No events at this meetup yet",
                        "value": _EVENT_LIST_EMPTY,
                    },
                }
            }
        },
    },
    **error_400_invalid_event_type(),
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_meetup(),
}

# ── GET /events/guests/{id} ───────────────────────────────────────────────────

LIST_GUEST_EVENTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Events for this guest",
        "content": {
            "application/json": {
                "examples": {
                    "guest_events_staff": {
                        "summary": "Staff view (bans only)",
                        "description": "Staff can only see BAN/UNBAN events for guests",
                        "value": _EVENT_LIST_BANS_ONLY,
                    },
                    "guest_events_admin": {
                        "summary": "Admin view (all events)",
                        "description": "Admins see all events including check-ins",
                        "value": {
                            "total": 2,
                            "limit": 50,
                            "offset": 0,
                            "events": [EVENT_BAN, EVENT_CHECKIN],
                        },
                    },
                    "no_events": {
                        "summary": "Guest has no recorded events",
                        "value": _EVENT_LIST_EMPTY,
                    },
                }
            }
        },
    },
    **error_400_invalid_event_type(),
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_guest(),
}

# ── GET /events/staff/{id} ────────────────────────────────────────────────────

LIST_STAFF_EVENTS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Events by this staff member",
        "content": {
            "application/json": {
                "examples": {
                    "own_events": {
                        "summary": "Staff viewing own actions",
                        "value": {
                            "total": 1,
                            "limit": 50,
                            "offset": 0,
                            "events": [EVENT_CHECKIN],
                        },
                    },
                    "admin_viewing_staff": {
                        "summary": "Admin viewing another staff's actions",
                        "description": "Admins can view any staff member's events",
                        "value": {
                            "total": 2,
                            "limit": 50,
                            "offset": 0,
                            "events": [EVENT_BAN, EVENT_CHECKIN],
                        },
                    },
                    "no_actions": {
                        "summary": "Staff has no recorded actions",
                        "value": _EVENT_LIST_EMPTY,
                    },
                }
            }
        },
    },
    **error_400_invalid_event_type(),
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_403_staff_own_events_only(),
    **error_404_staff(),
}
