"""Tests for GET /public/alters (public, read-only, one configured org)."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import Settings, get_settings
from app.main import app
from app.models.models import Organization
from tests.conftest import make_meetup, make_org

SettingsFactory = Callable[[str | None], None]


@pytest.fixture
def set_public_slug() -> Iterator[SettingsFactory]:
    """Override get_settings with a chosen public_alters_org_slug."""

    def _set(slug: str | None) -> None:
        settings = Settings(
            database_url="postgresql+psycopg://x:x@localhost/x",
            jwt_signing_key="test-key",
            public_alters_org_slug=slug,
        )
        app.dependency_overrides[get_settings] = lambda: settings

    yield _set
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def public_settings(session: Session, set_public_slug: SettingsFactory) -> None:
    """Configure club-vanta as the public org and create it."""
    set_public_slug("club-vanta")
    make_org(session, name="Club Vanta", slug="club-vanta")


def _club(session: Session) -> Organization:
    return session.exec(select(Organization).where(Organization.slug == "club-vanta")).one()


def test_public_alters_returns_200_without_auth(client: TestClient, session: Session, public_settings: None):
    """
    Verify the endpoint answers without any credentials.

    WHY: The public site calls it server-side with no token.
    """
    make_meetup(session, org=_club(session), name="Alter Uno", mazmo_meetup_url="https://mazmo.net/c/uno-1")

    resp = client.get("/public/alters")

    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.json()["alters"]) == 1


def test_public_alters_lists_only_configured_org(client: TestClient, session: Session, public_settings: None):
    """
    Verify meetups of other orgs never appear.

    WHY: Only Club Vanta is public; other orgs use the same backend.
    """
    other = make_org(session, name="Otra", slug="otra-org")
    make_meetup(session, org=_club(session), name="Alter Vanta", mazmo_meetup_url="https://mazmo.net/c/v-1")
    make_meetup(session, org=other, name="Alter Otra", mazmo_meetup_url="https://mazmo.net/c/o-1")

    resp = client.get("/public/alters")

    assert [a["title"] for a in resp.json()["alters"]] == ["Alter Vanta"]


def test_public_alters_ignores_org_in_query_string(client: TestClient, session: Session, public_settings: None):
    """
    Verify org/slug query params cannot select another org.

    WHY: The org is fixed by server config, never by the caller.
    """
    other = make_org(session, name="Otra", slug="otra-org")
    make_meetup(session, org=_club(session), name="Alter Vanta", mazmo_meetup_url="https://mazmo.net/c/v-1")
    make_meetup(session, org=other, name="Alter Otra", mazmo_meetup_url="https://mazmo.net/c/o-1")

    resp = client.get("/public/alters?org=otra-org&slug=otra-org")

    assert resp.status_code == status.HTTP_200_OK
    assert [a["title"] for a in resp.json()["alters"]] == ["Alter Vanta"]


def test_public_alters_has_no_per_org_route(client: TestClient, public_settings: None):
    """
    Verify there is no per-org public route.

    WHY: Exposing arbitrary orgs publicly is out of scope.
    """
    resp = client.get("/public/organizations/otra-org/alters")

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_public_alters_returns_404_when_setting_unset(client: TestClient, set_public_slug: SettingsFactory):
    """
    Verify an unset PUBLIC_ALTERS_ORG_SLUG yields an actionable 404.

    WHY: Fails closed instead of guessing an org.
    """
    set_public_slug(None)

    resp = client.get("/public/alters")

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    detail = resp.json()["detail"]
    assert detail.startswith("Cannot list alters:")
    assert "PUBLIC_ALTERS_ORG_SLUG" in detail
    assert "not set" in detail


def test_public_alters_orders_by_date_desc(client: TestClient, session: Session, public_settings: None):
    """
    Verify alters come newest first.

    WHY: The site shows the latest alter first.
    """
    org = _club(session)
    for i, day in enumerate((5, 20, 12)):
        make_meetup(
            session,
            org=org,
            name=f"Alter {i}",
            mazmo_meetup_url=f"https://mazmo.net/c/a-{i}",
            date=datetime(2026, 5, day, 22, 0, tzinfo=UTC),
        )

    resp = client.get("/public/alters")

    dates = [a["date"] for a in resp.json()["alters"]]
    assert dates == sorted(dates, reverse=True)
    assert [a["title"] for a in resp.json()["alters"]] == ["Alter 1", "Alter 2", "Alter 0"]


def test_public_alters_shape_has_exactly_public_fields(client: TestClient, session: Session, public_settings: None):
    """
    Verify only the public fields are exposed and no venue data leaks.

    WHY: Address and coordinates must never reach the public site.
    """
    meetup = make_meetup(session, org=_club(session), name="Alter Uno", mazmo_meetup_url="https://mazmo.net/c/uno-1")
    meetup.location_label = "Chile 419"
    meetup.latitude = -34.6123
    meetup.longitude = -58.3731
    session.add(meetup)
    session.flush()

    resp = client.get("/public/alters")

    alter = resp.json()["alters"][0]
    assert set(alter) == {"short_name", "title", "date", "mazmo_url", "cover_url", "excerpt"}
    assert alter["mazmo_url"] == "https://mazmo.net/c/uno-1"
    assert "Chile 419" not in resp.text
    assert "-34.6123" not in resp.text
    assert "-58.3731" not in resp.text


def test_public_alters_short_name_from_title(client: TestClient, session: Session, public_settings: None):
    """
    Verify short_name is derived from the title and the title stays intact.

    WHY: The site shows the short name as the alter's headline.
    """
    make_meetup(
        session,
        org=_club(session),
        name="Alter San Telmo - D I S C R E E T",
        mazmo_meetup_url="https://mazmo.net/c/d-1",
    )

    alter = client.get("/public/alters").json()["alters"][0]

    assert alter["short_name"] == "Discreet"
    assert alter["title"] == "Alter San Telmo - D I S C R E E T"


def test_public_alters_returns_404_when_configured_slug_missing(client: TestClient, set_public_slug: SettingsFactory):
    """
    Verify a configured slug with no org yields an actionable 404.

    WHY: A typo in the env var must be diagnosable from the response.
    """
    set_public_slug("no-existe")

    resp = client.get("/public/alters")

    assert resp.status_code == status.HTTP_404_NOT_FOUND
    detail = resp.json()["detail"]
    assert detail.startswith("Cannot list alters:")
    assert "no-existe" in detail


def test_public_alters_returns_empty_list_for_org_without_meetups(client: TestClient, public_settings: None):
    """
    Verify an org with no meetups returns an empty list, not an error.

    WHY: A new org has no alters yet.
    """
    resp = client.get("/public/alters")

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"alters": []}


def test_public_alters_null_cover_and_excerpt(client: TestClient, session: Session, public_settings: None):
    """
    Verify missing cover and excerpt are present as null.

    WHY: The generated client types expect the keys to always exist.
    """
    make_meetup(session, org=_club(session), name="Alter Uno", mazmo_meetup_url="https://mazmo.net/c/uno-1")

    alter = client.get("/public/alters").json()["alters"][0]

    assert alter["cover_url"] is None
    assert alter["excerpt"] is None


def test_public_alters_returns_cover_and_excerpt_when_present(
    client: TestClient, session: Session, public_settings: None
):
    """
    Verify cover and excerpt values are passed through.

    WHY: Concrete values, not just null handling.
    """
    make_meetup(
        session,
        org=_club(session),
        name="Alter Uno",
        mazmo_meetup_url="https://mazmo.net/c/uno-1",
        cover_url="https://cdn.example/cover.jpg",
        excerpt="Una noche tranquila.",
    )

    alter = client.get("/public/alters").json()["alters"][0]

    assert alter["cover_url"] == "https://cdn.example/cover.jpg"
    assert alter["excerpt"] == "Una noche tranquila."


def test_public_alters_sets_cache_control(client: TestClient, public_settings: None):
    """
    Verify the response is cacheable for five minutes.

    WHY: The site and the CDN absorb traffic so the API is not hit per visit.
    """
    resp = client.get("/public/alters")

    assert resp.headers["cache-control"] == "public, max-age=300"
