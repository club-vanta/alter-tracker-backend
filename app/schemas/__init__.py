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
from app.schemas.auth import (
    OrgMembershipPublic,
    RecoveryCodeResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    RolePublic,
    StaffRegisterRequest,
    TokenResponse,
    UserPublic,
    VerifyRecoveryCodeRequest,
)
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
    CreateGuestRequest,
    CreateManualGuestRequest,
    GuestListResponse,
    GuestPublic,
    GuestTypeUpdateRequest,
    GuestWithBanPublic,
    LinkMazmoRequest,
    MeetupGuestListResponse,
    MeetupGuestPublic,
    PaymentResponse,
    RsvpPublic,
    UpdateGuestRequest,
)
from app.schemas.mazmo import MazmoRsvpEntry, MazmoUserEntry
from app.schemas.meetups import (
    MAZMO_URL_PATTERN,
    AttendanceStats,
    CancellationStats,
    GuestTypeStats,
    MeetupCreate,
    MeetupListResponse,
    MeetupPublic,
    MeetupStatsPublic,
    PaymentStats,
    SyncResponse,
)
from app.schemas.organizations import (
    AddOrgMemberRequest,
    OrgCreate,
    OrgListResponse,
    OrgMemberListResponse,
    OrgMemberPublic,
    OrgPublic,
    OrgUpdate,
)
from app.schemas.users import UserSearchResult

__all__ = [
    "MAZMO_URL_PATTERN",
    "AddOrgMemberRequest",
    "ApproveUserRequest",
    "AttendanceStats",
    "BanGuestRequest",
    "BannedGuestListResponse",
    "BannedGuestPublic",
    "CancellationStats",
    "CheckInResponse",
    "CheckedInByPublic",
    "CreateGuestRequest",
    "CreateManualGuestRequest",
    "DisableUserRequest",
    "EventActorPublic",
    "EventGuestPublic",
    "EventLogListResponse",
    "EventLogPublic",
    "EventLogQuery",
    "EventTypeFilter",
    "GuestListResponse",
    "GuestPublic",
    "GuestTypeStats",
    "GuestTypeUpdateRequest",
    "GuestWithBanPublic",
    "LinkMazmoRequest",
    "MazmoRsvpEntry",
    "MazmoUserEntry",
    "MeetupCreate",
    "MeetupGuestListResponse",
    "MeetupGuestPublic",
    "MeetupListResponse",
    "MeetupPublic",
    "MeetupStatsPublic",
    "OrgCreate",
    "OrgListResponse",
    "OrgMemberListResponse",
    "OrgMemberPublic",
    "OrgMembershipPublic",
    "OrgPublic",
    "OrgUpdate",
    "PaymentResponse",
    "PaymentStats",
    "RecoveryCodeResponse",
    "ResetPasswordRequest",
    "ResetPasswordResponse",
    "RolePublic",
    "RoleRequest",
    "RsvpPublic",
    "StaffRegisterRequest",
    "SyncResponse",
    "TokenResponse",
    "UpdateGuestRequest",
    "UserPublic",
    "UserSearchResult",
    "VerifyRecoveryCodeRequest",
]
