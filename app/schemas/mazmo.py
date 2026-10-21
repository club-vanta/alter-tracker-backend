"""
Schemas for parsing Mazmo external API responses.

These are NOT part of our API contract - they represent the shape of data
returned by Mazmo's internal API. We use lenient parsing (strict=False)
since we don't control this external API.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MazmoRsvpEntry(BaseModel):
    """
    One entry in the event.rsvps dict returned by Mazmo's thread endpoint.

    Shape from Mazmo:
    {
        "0": { "userId": 195749, "joinedAt": "2026-03-17T00:25:32.744Z" },
        "1": { "userId": 153151, "joinedAt": "..." },
        ...
    }

    Note: The keys are string indices, not user IDs. The actual user ID
    is inside the object as 'userId'.
    """

    # strict=False because this is external data we don't control.
    # We'd rather parse successfully with some coercion than fail hard.
    model_config = ConfigDict(strict=False)

    userId: int
    joinedAt: datetime


class MazmoAvatarEntry(BaseModel):
    """
    The `avatar` object Mazmo returns for a user profile.

    Mazmo's avatar object actually has 4 sizes x 2 formats - only
    `default` is modeled here, the only size/format this app has a use
    case for (a single image on an admin page, not responsive images).
    Extra keys in the real payload (other sizes/formats) are ignored,
    not validation errors - BaseModel allows extra fields by default.
    """

    model_config = ConfigDict(strict=False)

    default: str


class MazmoUserEntry(BaseModel):
    """
    One entry in the /users response dict returned by Mazmo, and also
    the shape of the single-user /users/{username} response body (used
    by MazmoClient.fetch_user_by_username - see MazmoUserWithId in
    app/services/mazmo.py). This is the single place that defines which
    fields are read from Mazmo user data for both endpoints.

    Shape from Mazmo (batch /users endpoint):
    {
        "195749": {
            "username": "alice", "displayname": "Alice W.",
            "avatar": {"default": "https://..."}, "age": 29,
            "gender": "female", "pronoun": "she/her",
            "suspended": false, "banned": false
        },
        ...
    }

    Note: The keys are string representations of user IDs.

    avatar/age/gender/pronoun/suspended/banned are all optional (or
    default to a safe value) since Mazmo may not have them set for a
    given user. suspended/banned are Mazmo's own account-level flags,
    unrelated to this app's own ban system - see GuestMazmoProfile in
    app/models/models.py.
    """

    model_config = ConfigDict(strict=False)

    username: str
    displayname: str
    avatar: MazmoAvatarEntry | None = None
    age: int | None = None
    gender: str | None = None
    pronoun: str | None = None
    suspended: bool = False
    banned: bool = False
