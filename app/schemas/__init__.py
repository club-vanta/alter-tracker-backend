"""
Pydantic v2 schemas for API request/response validation.

All schemas are re-exported here for convenience. You can import from
the submodules directly or from this package:

    from app.schemas import GuestPublic, MeetupCreate
    # or
    from app.schemas.guests import GuestPublic
    from app.schemas.meetups import MeetupCreate
"""

from app.schemas.admin import ApproveUserRequest, DisableUserRequest, RoleRequest
from app.schemas.auth import RolePublic, StaffRegisterRequest, TokenResponse, UserPublic
from app.schemas.events import (
    EventActorPublic,
    EventGuestPublic,
    EventLogListResponse,
    EventLogPublic,
    EventLogQuery,
    EventTypeFilter,
)
from app.schemas.guests import (
    BanGuestRequest,
    BannedGuestListResponse,
    BannedGuestPublic,
    CheckedInByPublic,
    CheckInResponse,
    GuestListResponse,
    GuestPublic,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    RsvpPublic,
)
from app.schemas.mazmo import MazmoRsvpEntry, MazmoUserEntry
from app.schemas.meetups import (
    MAZMO_URL_PATTERN,
    MeetupCreate,
    MeetupListResponse,
    MeetupPublic,
    SyncResponse,
)

__all__ = [
    "MAZMO_URL_PATTERN",
    "ApproveUserRequest",
    "BanGuestRequest",
    "BannedGuestListResponse",
    "BannedGuestPublic",
    "CheckInResponse",
    "CheckedInByPublic",
    "DisableUserRequest",
    "EventActorPublic",
    "EventGuestPublic",
    "EventLogListResponse",
    "EventLogPublic",
    "EventLogQuery",
    "EventTypeFilter",
    "GuestListResponse",
    "GuestPublic",
    "MazmoRsvpEntry",
    "MazmoUserEntry",
    "MeetupCreate",
    "MeetupGuestListResponse",
    "MeetupGuestPublic",
    "MeetupListResponse",
    "MeetupPublic",
    "RolePublic",
    "RoleRequest",
    "RsvpPublic",
    "StaffRegisterRequest",
    "SyncResponse",
    "TokenResponse",
    "UserPublic",
]
