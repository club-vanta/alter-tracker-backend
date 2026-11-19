"""
Visits service: how many times a guest came to the org before a meetup.

Door staff see who is new at a meetup, and that has to agree with the stats
tab's first-time figures, so this counts over attendances_cte, the same
attendances the retention stats use, instead of restating the rule.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlmodel import Session

from app.models.models import Meetup
from app.services.stats.retention import attendances_cte


def prior_visits_by_guest(session: Session, meetup: Meetup, guest_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    """
    For each guest, the finalized meetups of the meetup's org that started
    strictly before it and that the guest attended. A guest with none maps
    to 0. One query, whatever the number of guests.
    """
    if not guest_ids:
        return {}
    att = attendances_cte(meetup.org_id)
    rows = session.execute(
        select(att.c.guest_id, func.count())
        .where(att.c.guest_id.in_(guest_ids), att.c.date < meetup.date)
        .group_by(att.c.guest_id)
    ).all()
    counts: dict[uuid.UUID, int] = {row[0]: row[1] for row in rows}
    return {guest_id: counts.get(guest_id, 0) for guest_id in guest_ids}
