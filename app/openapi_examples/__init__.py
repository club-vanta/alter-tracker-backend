"""
OpenAPI examples for all API endpoints.

This module provides comprehensive, realistic examples for the FastAPI
OpenAPI documentation. Each router has its own examples module.

Usage:
    from app.openapi_examples.auth_examples import REGISTER_REQUEST_EXAMPLES, REGISTER_RESPONSES

    @router.post("/register", responses=REGISTER_RESPONSES)
    async def register(
        body: Annotated[StaffRegisterRequest, Body(openapi_examples=REGISTER_REQUEST_EXAMPLES)],
    ): ...
"""

# Auth examples
from app.openapi_examples.auth_examples import (
    REGISTER_REQUEST_EXAMPLES,
    REGISTER_RESPONSES,
    TOKEN_RESPONSES,
    USERINFO_RESPONSES,
)

# Events examples
from app.openapi_examples.events_examples import (
    LIST_ALL_EVENTS_RESPONSES,
    LIST_GUEST_EVENTS_RESPONSES,
    LIST_MEETUP_EVENTS_RESPONSES,
    LIST_STAFF_EVENTS_RESPONSES,
)

# Guests examples
from app.openapi_examples.guests_examples import (
    BAN_REQUEST_EXAMPLES,
    BAN_RESPONSES,
    GET_GUEST_RESPONSES,
    LIST_BANNED_RESPONSES,
    LIST_GUESTS_RESPONSES,
    UNBAN_RESPONSES,
)

# Meetups examples
from app.openapi_examples.meetups_examples import (
    CHECKIN_RESPONSES,
    CREATE_MEETUP_REQUEST_EXAMPLES,
    CREATE_MEETUP_RESPONSES,
    GET_MEETUP_RESPONSES,
    LIST_MEETUP_GUESTS_RESPONSES,
    LIST_MEETUPS_RESPONSES,
    SYNC_MEETUP_RESPONSES,
    UNDO_CHECKIN_REQUEST_EXAMPLES,
    UNDO_CHECKIN_RESPONSES,
)

# Staff examples
from app.openapi_examples.staff_examples import (
    APPROVE_REQUEST_EXAMPLES,
    APPROVE_RESPONSES,
    DISABLE_REQUEST_EXAMPLES,
    DISABLE_RESPONSES,
    ENABLE_RESPONSES,
    LIST_PENDING_RESPONSES,
    LIST_STAFF_RESPONSES,
    ROLE_REQUEST_EXAMPLES,
    ROLE_RESPONSES,
)

__all__ = [
    "APPROVE_REQUEST_EXAMPLES",
    "APPROVE_RESPONSES",
    "BAN_REQUEST_EXAMPLES",
    "BAN_RESPONSES",
    "CHECKIN_RESPONSES",
    # Meetups
    "CREATE_MEETUP_REQUEST_EXAMPLES",
    "CREATE_MEETUP_RESPONSES",
    "DISABLE_REQUEST_EXAMPLES",
    "DISABLE_RESPONSES",
    "ENABLE_RESPONSES",
    "GET_GUEST_RESPONSES",
    "GET_MEETUP_RESPONSES",
    # Events
    "LIST_ALL_EVENTS_RESPONSES",
    "LIST_BANNED_RESPONSES",
    # Guests
    "LIST_GUESTS_RESPONSES",
    "LIST_GUEST_EVENTS_RESPONSES",
    "LIST_MEETUPS_RESPONSES",
    "LIST_MEETUP_EVENTS_RESPONSES",
    "LIST_MEETUP_GUESTS_RESPONSES",
    "LIST_PENDING_RESPONSES",
    "LIST_STAFF_EVENTS_RESPONSES",
    # Staff
    "LIST_STAFF_RESPONSES",
    # Auth
    "REGISTER_REQUEST_EXAMPLES",
    "REGISTER_RESPONSES",
    "ROLE_REQUEST_EXAMPLES",
    "ROLE_RESPONSES",
    "SYNC_MEETUP_RESPONSES",
    "TOKEN_RESPONSES",
    "UNBAN_RESPONSES",
    "UNDO_CHECKIN_REQUEST_EXAMPLES",
    "UNDO_CHECKIN_RESPONSES",
    "USERINFO_RESPONSES",
]
