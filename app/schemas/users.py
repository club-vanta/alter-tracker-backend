"""Schemas for user search."""

from pydantic import BaseModel


class UserSearchResult(BaseModel):
    """Minimal user info returned by the search endpoint."""

    id: int
    username: str
