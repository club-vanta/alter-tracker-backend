"""
Pydantic v2 schemas (strict mode where appropriate).

We keep these separate from the SQLModel models to maintain a clean boundary
between the database representation and the API contract.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.models import PossibleRoles

# ── Role ──────────────────────────────────────────────────────────────────────


class RolePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: PossibleRoles


# ── Auth ──────────────────────────────────────────────────────────────────────


class StaffRegisterRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    username: str = Field(max_length=64)
    password: str = Field(min_length=15, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_approved: bool
    role: "RolePublic"
    created_at: datetime


# ── Admin ─────────────────────────────────────────────────────────────────────


class ApproveUserRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    is_approved: bool


# ── Guest ─────────────────────────────────────────────────────────────────────


class GuestPublic(BaseModel):
    """Guest representation sent to the frontend door tracker."""

    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    username: str
    displayname: str
    rsvp_time: datetime
    has_arrived: bool
    arrival_time: datetime | None = None
    arrival_order: int | None = None


class GuestListResponse(BaseModel):
    total: int
    guests: list[GuestPublic]


# ── Mazmo External API ────────────────────────────────────────────────────────


class MazmoRsvpEntry(BaseModel):
    """One entry in the event.rsvps dict returned by Mazmo."""

    model_config = ConfigDict(strict=False)  # External API - be lenient

    userId: int
    joinedAt: datetime


class MazmoUserEntry(BaseModel):
    """One entry in the /users response dict returned by Mazmo."""

    model_config = ConfigDict(strict=False)

    username: str
    displayname: str


class SyncResponse(BaseModel):
    inserted: int
    skipped: int
    total_in_db: int


# ── Admin role management ─────────────────────────────────────────────────────


class RoleRequest(BaseModel):
    role: PossibleRoles


# ── Check-in ──────────────────────────────────────────────────────────────────


class CheckInResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mazmo_user_id: int
    displayname: str
    arrival_order: int
    arrival_time: datetime
