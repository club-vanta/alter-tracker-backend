"""Business logic for the public alters listing."""

from sqlmodel import Session, col, select

from app.models.models import Meetup, Organization
from app.schemas.public import PublicAlter
from app.services.short_name import short_name


def list_public_alters(session: Session, org: Organization) -> list[PublicAlter]:
    """Return the org's meetups as public alters, newest first."""
    meetups = session.exec(select(Meetup).where(Meetup.org_id == org.id).order_by(col(Meetup.date).desc())).all()
    return [
        PublicAlter(
            short_name=short_name(meetup.name),
            title=meetup.name,
            date=meetup.date,
            mazmo_url=meetup.mazmo_meetup_url,
            cover_url=meetup.cover_url,
            excerpt=meetup.excerpt,
        )
        for meetup in meetups
    ]
