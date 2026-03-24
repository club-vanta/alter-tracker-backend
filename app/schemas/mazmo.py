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


class MazmoUserEntry(BaseModel):
    """
    One entry in the /users response dict returned by Mazmo.

    Shape from Mazmo:
    {
        "195749": { "username": "alice", "displayname": "Alice W." },
        "153151": { "username": "bob", "displayname": "Bob" },
        ...
    }

    Note: The keys are string representations of user IDs.
    """

    model_config = ConfigDict(strict=False)

    username: str
    displayname: str
