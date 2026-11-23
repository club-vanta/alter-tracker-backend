"""Schemas for the public, unauthenticated site endpoints."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PublicAlter(BaseModel):
    """One alter as shown on the public site. Never includes venue or attendee data."""

    model_config = ConfigDict(from_attributes=True)

    short_name: str
    title: str
    date: datetime
    mazmo_url: str
    cover_url: str | None
    excerpt: str | None


class PublicAltersResponse(BaseModel):
    alters: list[PublicAlter]
