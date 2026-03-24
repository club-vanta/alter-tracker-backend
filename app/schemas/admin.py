"""
Admin-related schemas.

These schemas handle admin operations like approving users and managing roles.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.models.models import PossibleRoles


class ApproveUserRequest(BaseModel):
    """
    Request body for approving or revoking a user's access.

    Admins use this to flip the is_approved flag on staff accounts.
    New registrations start unapproved and require admin action.
    """

    model_config = ConfigDict(strict=True)

    is_approved: bool


class RoleRequest(BaseModel):
    """
    Request body for changing a user's role.

    Used by admins to promote staff to admin or demote admins to staff.
    """

    role: PossibleRoles


class DisableUserRequest(BaseModel):
    """
    Request body for disabling a staff account.

    A reason is required for audit trail purposes. Disabled accounts
    cannot log in but their data is preserved for historical reference.
    """

    model_config = ConfigDict(strict=True)

    reason: str = Field(min_length=5, max_length=500)
