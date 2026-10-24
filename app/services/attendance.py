"""
Attendance service - what a guest's RSVPs say about whether they showed up.

The rule (attendance_status) and the totals (summarize_attendance) live here,
not in a router, so the guest profile endpoint and any future org-wide
attendance statistics share the exact same definition of a no-show.
"""

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.models import GuestAttendanceStatus, Meetup, MeetupRsvp


@dataclass(frozen=True)
class GuestMeetupAttendance:
    """One meetup a guest has an attendance outcome for."""

    rsvp: MeetupRsvp
    meetup: Meetup
    status: GuestAttendanceStatus


@dataclass(frozen=True)
class AttendanceCounts:
    """
    Totals by status. attended includes walk-ins; walkin is the subset of
    attended who came without signing up, not a separate bucket.
    """

    attended: int
    walkin: int
    no_show: int
    cancelled: int
    upcoming: int


def attendance_status(rsvp: MeetupRsvp, meetup: Meetup) -> GuestAttendanceStatus | None:
    """
    Classify one RSVP. Checked in this order, first match wins:

    1. Checked in: WALKIN if added at the door, ATTENDED otherwise. A
       check-in beats a cancelled RSVP: whoever showed up went.
    2. Walk-in never checked in: None. Staff added them at the door but
       they never got in (or the check-in was undone), and they never
       signed up, so there is nothing to count either way.
    3. Cancelled on Mazmo: CANCELLED. Withdrawing is not a no-show.
    4. Meetup finalized: NO_SHOW. The door is closed, so no check-in means
       they did not come.
    5. Otherwise UPCOMING: they may still arrive.
    """
    if rsvp.has_arrived:
        return GuestAttendanceStatus.WALKIN if rsvp.is_walkin else GuestAttendanceStatus.ATTENDED
    if rsvp.is_walkin:
        return None
    if rsvp.cancelled_rsvp:
        return GuestAttendanceStatus.CANCELLED
    if meetup.is_finalized:
        return GuestAttendanceStatus.NO_SHOW
    return GuestAttendanceStatus.UPCOMING


def list_guest_attendance(session: Session, org_id: uuid.UUID, guest_id: uuid.UUID) -> list[GuestMeetupAttendance]:
    """
    Every meetup of org_id the guest has an attendance outcome for, newest
    first. RSVPs with no outcome (see attendance_status) are left out.
    """
    rows = session.exec(
        select(MeetupRsvp, Meetup)
        .join(Meetup, MeetupRsvp.meetup_id == Meetup.id)  # type: ignore[arg-type]
        .where(MeetupRsvp.guest_id == guest_id)
        .where(Meetup.org_id == org_id)
        .order_by(Meetup.date.desc(), Meetup.id)  # type: ignore[attr-defined]
    ).all()

    result = []
    for rsvp, meetup in rows:
        status = attendance_status(rsvp, meetup)
        if status is not None:
            result.append(GuestMeetupAttendance(rsvp=rsvp, meetup=meetup, status=status))
    return result


def summarize_attendance(statuses: Iterable[GuestAttendanceStatus]) -> AttendanceCounts:
    """Count statuses into totals, folding walk-ins into attended."""
    counts = Counter(statuses)
    return AttendanceCounts(
        attended=counts[GuestAttendanceStatus.ATTENDED] + counts[GuestAttendanceStatus.WALKIN],
        walkin=counts[GuestAttendanceStatus.WALKIN],
        no_show=counts[GuestAttendanceStatus.NO_SHOW],
        cancelled=counts[GuestAttendanceStatus.CANCELLED],
        upcoming=counts[GuestAttendanceStatus.UPCOMING],
    )
