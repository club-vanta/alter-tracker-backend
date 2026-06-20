"""
OpenAPI examples for organizations router endpoints.

Endpoints:
  POST   /organizations/                              - Create org (SITE_ADMIN)
  GET    /organizations/                              - List orgs (SITE_ADMIN)
  GET    /organizations/{org_id}                      - Get org (member or SITE_ADMIN)
  PATCH  /organizations/{org_id}                      - Update org name/slug (SITE_ADMIN)
  GET    /organizations/{org_id}/members              - List members (SITE_ADMIN)
  POST   /organizations/{org_id}/members/{user_id}    - Add member (SITE_ADMIN)
  PATCH  /organizations/{org_id}/members/{user_id}    - Change member role (SITE_ADMIN)
  DELETE /organizations/{org_id}/members/{user_id}    - Remove member (SITE_ADMIN)
  GET    /organizations/{org_id}/guests/banned        - List banned guests (org member)
  PATCH  /organizations/{org_id}/guests/{id}/ban      - Ban a guest (org admin)
  PATCH  /organizations/{org_id}/guests/{id}/unban    - Unban a guest (org admin)
"""

from typing import Any

from app.openapi_examples._constants import (
    GUEST_BANNED_FULL,
    GUEST_NORMAL,
    ORG_EXAMPLE,
    ORG_EXAMPLE_2,
    ORG_MEMBER_ADMIN,
    ORG_MEMBER_STAFF,
)
from app.openapi_examples._error_responses import (
    error_401_invalid_credentials,
    error_403_admin_required,
    error_403_not_approved,
    error_403_site_admin_required,
    error_404_guest,
    error_404_org,
    error_409_already_banned,
    error_409_not_banned,
    error_422_validation_reason,
)

# -- POST /organizations/ -----------------------------------------------------

CREATE_ORG_REQUEST_EXAMPLES: dict[str, Any] = {
    "new_org": {
        "summary": "Create a new organization",
        "value": {
            "name": "Alter Buenos Aires",
            "slug": "alter-bsas",
        },
    },
}

CREATE_ORG_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Organization created",
        "content": {
            "application/json": {
                "examples": {
                    "created": {
                        "summary": "Organization created successfully",
                        "value": ORG_EXAMPLE,
                    },
                }
            }
        },
    },
    409: {
        "description": "Name or slug already taken",
        "content": {
            "application/json": {
                "examples": {
                    "duplicate_name": {
                        "summary": "Name already taken",
                        "value": {"detail": "Cannot create organization: name 'Alter Buenos Aires' is already taken."},
                    },
                    "duplicate_slug": {
                        "summary": "Slug already taken",
                        "value": {"detail": "Cannot create organization: slug 'alter-bsas' is already taken."},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
}

# -- GET /organizations/ ------------------------------------------------------

LIST_ORGS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "All organizations",
        "content": {
            "application/json": {
                "examples": {
                    "orgs_list": {
                        "summary": "Multiple organizations",
                        "value": {
                            "total": 2,
                            "organizations": [ORG_EXAMPLE, ORG_EXAMPLE_2],
                        },
                    },
                    "empty": {
                        "summary": "No organizations yet",
                        "value": {"total": 0, "organizations": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
}

# -- GET /organizations/{org_id} ----------------------------------------------

GET_ORG_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Organization found",
        "content": {
            "application/json": {
                "examples": {
                    "org": {
                        "summary": "Single organization",
                        "value": ORG_EXAMPLE,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_org(),
}

# -- PATCH /organizations/{org_id} --------------------------------------------

UPDATE_ORG_REQUEST_EXAMPLES: dict[str, Any] = {
    "rename": {
        "summary": "Rename the organization",
        "value": {"name": "Alter BA"},
    },
    "reslug": {
        "summary": "Change the slug",
        "value": {"slug": "alter-ba"},
    },
    "both": {
        "summary": "Update both name and slug",
        "value": {"name": "Alter BA", "slug": "alter-ba"},
    },
}

UPDATE_ORG_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Organization updated",
        "content": {
            "application/json": {
                "examples": {
                    "updated": {
                        "summary": "Organization updated successfully",
                        "value": {**ORG_EXAMPLE, "name": "Alter BA", "slug": "alter-ba"},
                    },
                }
            }
        },
    },
    409: {
        "description": "Name or slug already taken by another organization",
        "content": {
            "application/json": {
                "examples": {
                    "duplicate_name": {
                        "summary": "Name already taken",
                        "value": {"detail": "Cannot update organization: name 'Club Vanta' is already taken."},
                    },
                    "duplicate_slug": {
                        "summary": "Slug already taken",
                        "value": {"detail": "Cannot update organization: slug 'club-vanta' is already taken."},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
    **error_404_org(),
}

# -- GET /organizations/{org_id}/members --------------------------------------

LIST_ORG_MEMBERS_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Members of this organization",
        "content": {
            "application/json": {
                "examples": {
                    "members": {
                        "summary": "Mix of staff and admin members",
                        "value": {
                            "total": 2,
                            "members": [ORG_MEMBER_ADMIN, ORG_MEMBER_STAFF],
                        },
                    },
                    "empty": {
                        "summary": "No members yet",
                        "value": {"total": 0, "members": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
    **error_404_org(),
}

# -- POST /organizations/{org_id}/members/{user_id} ---------------------------

ADD_MEMBER_REQUEST_EXAMPLES: dict[str, Any] = {
    "add_staff": {
        "summary": "Add as staff",
        "value": {"role": "STAFF"},
    },
    "add_admin": {
        "summary": "Add as org admin",
        "value": {"role": "ADMIN"},
    },
}

ADD_MEMBER_RESPONSES: dict[int | str, dict[str, Any]] = {
    201: {
        "description": "Member added",
        "content": {
            "application/json": {
                "examples": {
                    "added_staff": {
                        "summary": "Added as staff",
                        "value": ORG_MEMBER_STAFF,
                    },
                    "added_admin": {
                        "summary": "Added as org admin",
                        "value": ORG_MEMBER_ADMIN,
                    },
                }
            }
        },
    },
    404: {
        "description": "Organization or user not found",
        "content": {
            "application/json": {
                "examples": {
                    "org_not_found": {
                        "summary": "Organization not found",
                        "value": {"detail": "Organization c3d4e5f6-a7b8-9012-cdef-123456789012 not found."},
                    },
                    "user_not_found": {
                        "summary": "User not found",
                        "value": {"detail": "User 99 not found."},
                    },
                }
            }
        },
    },
    409: {
        "description": "User is already a member",
        "content": {
            "application/json": {
                "examples": {
                    "already_member": {
                        "summary": "User already belongs to this org",
                        "value": {
                            "detail": (
                                "User 'carlos_staff' is already a member of this organization "
                                "with role STAFF. Use PATCH to change their role."
                            )
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
}

# -- PATCH /organizations/{org_id}/members/{user_id} --------------------------

UPDATE_MEMBER_ROLE_REQUEST_EXAMPLES: dict[str, Any] = {
    "promote_to_admin": {
        "summary": "Promote to org admin",
        "value": {"role": "ADMIN"},
    },
    "demote_to_staff": {
        "summary": "Demote back to staff",
        "value": {"role": "STAFF"},
    },
}

UPDATE_MEMBER_ROLE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Role updated",
        "content": {
            "application/json": {
                "examples": {
                    "promoted": {
                        "summary": "Promoted to org admin",
                        "value": ORG_MEMBER_ADMIN,
                    },
                }
            }
        },
    },
    404: {
        "description": "User is not a member of this organization",
        "content": {
            "application/json": {
                "examples": {
                    "not_member": {
                        "summary": "User not in this org",
                        "value": {
                            "detail": "User 99 is not a member of organization c3d4e5f6-a7b8-9012-cdef-123456789012."
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
}

# -- DELETE /organizations/{org_id}/members/{user_id} -------------------------

REMOVE_MEMBER_RESPONSES: dict[int | str, dict[str, Any]] = {
    204: {"description": "Member removed"},
    404: {
        "description": "User is not a member of this organization",
        "content": {
            "application/json": {
                "examples": {
                    "not_member": {
                        "summary": "User not in this org",
                        "value": {
                            "detail": "User 99 is not a member of organization c3d4e5f6-a7b8-9012-cdef-123456789012."
                        },
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_site_admin_required(),
}

# -- GET /organizations/{org_id}/guests/banned --------------------------------

LIST_BANNED_IN_ORG_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Banned guests in this organization",
        "content": {
            "application/json": {
                "examples": {
                    "banned_list": {
                        "summary": "Guests currently banned in this org",
                        "value": {
                            "total": 1,
                            "guests": [GUEST_BANNED_FULL],
                        },
                    },
                    "no_bans": {
                        "summary": "No banned guests in this org",
                        "value": {"total": 0, "guests": []},
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_not_approved(),
    **error_404_org(),
}

# -- PATCH /organizations/{org_id}/guests/{id}/ban ----------------------------

BAN_GUEST_REQUEST_EXAMPLES: dict[str, Any] = {
    "ban_guest": {
        "summary": "Ban a problematic guest",
        "description": "Ban requires a reason for audit purposes (5-500 chars)",
        "value": {
            "reason": "Comportamiento agresivo con otros asistentes en el evento del 20/03",
        },
    },
}

BAN_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest banned in this organization",
        "content": {
            "application/json": {
                "examples": {
                    "banned": {
                        "summary": "Guest now banned",
                        "value": GUEST_BANNED_FULL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_guest(),
    **error_404_org(),
    **error_409_already_banned(),
    **error_422_validation_reason(),
}

# -- PATCH /organizations/{org_id}/guests/{id}/unban --------------------------

UNBAN_GUEST_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Guest unbanned in this organization",
        "content": {
            "application/json": {
                "examples": {
                    "unbanned": {
                        "summary": "Guest now unbanned",
                        "description": "Returns the guest's identity without ban data",
                        "value": GUEST_NORMAL,
                    },
                }
            }
        },
    },
    **error_401_invalid_credentials(),
    **error_403_admin_required(),
    **error_404_guest(),
    **error_404_org(),
    **error_409_not_banned(),
}
