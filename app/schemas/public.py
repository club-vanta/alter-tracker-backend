"""Schemas for the public, unauthenticated site endpoints."""

from datetime import datetime

from pydantic import BaseModel


class PublicAlter(BaseModel):
    """One alter as shown on the public site. Never includes venue or attendee data."""

    short_name: str
    title: str
    date: datetime
    mazmo_url: str
    cover_url: str | None
    excerpt: str | None


class PublicAltersResponse(BaseModel):
    alters: list[PublicAlter]
