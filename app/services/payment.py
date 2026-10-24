"""
Payment service - who owes entry at a meetup that requires payment.

Only NORMAL guests pay; INVITED, VENDOR and STAFF are exempt whatever their
has_paid flag says (see GuestType). The rule lives here once so the door
card (GET .../meetups/{id}/stats) and the org operations statistics split
paid, pending and exempt the same way.
"""

from enum import StrEnum

from app.models.models import GuestType


class PaymentStanding(StrEnum):
    """Where one RSVP stands against a meetup's payment requirement."""

    PAID = "PAID"
    PENDING = "PENDING"
    EXEMPT = "EXEMPT"


def payment_standing(guest_type: str, has_paid: bool) -> PaymentStanding:
    """PAID or PENDING for a NORMAL guest by has_paid; EXEMPT for every other guest type."""
    if guest_type != GuestType.NORMAL:
        return PaymentStanding.EXEMPT
    return PaymentStanding.PAID if has_paid else PaymentStanding.PENDING
