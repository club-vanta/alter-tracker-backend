"""
Attendance service - turns a guest's RSVP flags into one attendance outcome.

Kept as a pure function (no DB access) so the guest profile endpoint and any
future org-wide attendance statistics share the exact same definition of a
no-show.
"""

from app.models.models import GuestAttendanceStatus, Meetup, MeetupRsvp


def attendance_status(rsvp: MeetupRsvp, meetup: Meetup) -> GuestAttendanceStatus:
    """
    Classify one RSVP. Checked in this order, first match wins:

    1. Checked in: WALKIN if added at the door, ATTENDED otherwise. A
       check-in beats a cancelled RSVP: whoever showed up went.
    2. Cancelled on Mazmo: CANCELLED. Withdrawing is not a no-show.
    3. Meetup finalized: NO_SHOW. The door is closed, so no check-in means
       they did not come.
    4. Otherwise UPCOMING: they may still arrive.
    """
    if rsvp.has_arrived:
        return GuestAttendanceStatus.WALKIN if rsvp.is_walkin else GuestAttendanceStatus.ATTENDED
    if rsvp.cancelled_rsvp:
        return GuestAttendanceStatus.CANCELLED
    if meetup.is_finalized:
        return GuestAttendanceStatus.NO_SHOW
    return GuestAttendanceStatus.UPCOMING
