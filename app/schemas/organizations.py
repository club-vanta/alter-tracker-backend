"""Schemas for organizations and org membership."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrgCreate(BaseModel):
    """Request body to create a new organization."""

    name: str = Field(min_length=2, max_length=128)
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")


class OrgPublic(BaseModel):
    """Public representation of an organization."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime


class OrgListResponse(BaseModel):
    total: int
    organizations: list[OrgPublic]


class OrgMemberPublic(BaseModel):
    """A user's membership in an organization."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    org_id: uuid.UUID
    role: str
    username: str


class OrgUpdate(BaseModel):
    """Request body to partially update an organization's name and/or slug."""

    name: str | None = Field(default=None, min_length=2, max_length=128)
    slug: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")


class AddOrgMemberRequest(BaseModel):
    """Request body to add a user to an organization."""

    role: str = Field(pattern=r"^(STAFF|ADMIN)$")


class OrgMemberListResponse(BaseModel):
    total: int
    members: list[OrgMemberPublic]
