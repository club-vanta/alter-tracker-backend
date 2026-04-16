"""
Authentication and user-related schemas.

These schemas handle user registration, login responses, and user representation.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.models import PossibleRoles


class RolePublic(BaseModel):
    """
    Public representation of a user role.

    Used when returning user data that includes their role information.
    Maps directly to the Role model via from_attributes.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: PossibleRoles


class StaffRegisterRequest(BaseModel):
    """
    Request body for staff registration.

    Strict mode ensures exact type matching - no coercion from strings to ints, etc.
    Password has a minimum length of 15 characters for security.
    """

    model_config = ConfigDict(strict=True)

    username: str = Field(max_length=64)
    password: str = Field(min_length=15, max_length=128)


class TokenResponse(BaseModel):
    """
    Response returned after successful authentication.

    Contains the JWT access token that the client should include in
    subsequent requests via the Authorization header.
    """

    access_token: str
    token_type: str = "bearer"


class UserPublic(BaseModel):
    """
    Public representation of a staff user.

    Returned when fetching user details. Includes the nested role object
    for convenience. Never includes the hashed password.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    is_approved: bool
    role: RolePublic
    created_at: datetime

    # Disable state (soft-delete)
    is_disabled: bool = False
    disabled_at: datetime | None = None
    disabled_reason: str | None = None


class VerifyRecoveryCodeRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    username: str
    code: str


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    username: str
    code: str
    new_password: str = Field(min_length=15, max_length=128)


class ResetPasswordResponse(BaseModel):
    username: str


class RecoveryCodeResponse(BaseModel):
    username: str
    code: str
