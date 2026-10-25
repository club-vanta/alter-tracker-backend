"""Tests for PATCH /organizations/{org_id}/meetups/{meetup_id}/location.

The location (label, latitude, longitude) is set or cleared as a unit by an
org admin, also on finalized meetups, and is visible to every org member.
"""

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.models import Organization, OrgRole, User
from tests.conftest import make_meetup, make_org, make_org_member

LOCATION = {"location_label": "Club Vanta, Palermo, Buenos Aires", "latitude": -34.5875, "longitude": -58.4306}
CLEARED = {"location_label": None, "latitude": None, "longitude": None}


def _url(meetup) -> str:
    return f"/organizations/{meetup.org_id}/meetups/{meetup.id}/location"


@pytest.fixture()
def org_staff_member(session: Session, org: Organization, staff_user: User):
    """Add the staff_user to the default org with OrgRole.STAFF."""
    return make_org_member(session, org=org, user=staff_user, role=OrgRole.STAFF)


def test_set_location_returns_200_with_the_three_fields(client: TestClient, admin_headers: dict, meetup):
    """WHY: Org admins attach a venue to a meetup; the response carries the stored values."""
    resp = client.patch(_url(meetup), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["id"] == str(meetup.id)
    assert data["location_label"] == "Club Vanta, Palermo, Buenos Aires"
    assert data["latitude"] == -34.5875
    assert data["longitude"] == -58.4306


def test_set_location_trims_the_label(client: TestClient, admin_headers: dict, meetup):
    """WHY: Whitespace around a label is noise; the stored value is trimmed."""
    resp = client.patch(_url(meetup), json={**LOCATION, "location_label": "  Club Vanta  "}, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["location_label"] == "Club Vanta"


def test_clear_location_sets_all_three_fields_to_null(client: TestClient, admin_headers: dict, meetup):
    """WHY: A wrong venue must be removable, and the fields are cleared together."""
    client.patch(_url(meetup), json=LOCATION, headers=admin_headers)

    resp = client.patch(_url(meetup), json=CLEARED, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert (data["location_label"], data["latitude"], data["longitude"]) == (None, None, None)


def test_set_location_accepts_boundary_coordinates(client: TestClient, admin_headers: dict, meetup):
    """WHY: The ranges are inclusive, so the poles and the antimeridian are valid."""
    body = {"location_label": "Edge", "latitude": 90, "longitude": -180}

    resp = client.patch(_url(meetup), json=body, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert (resp.json()["latitude"], resp.json()["longitude"]) == (90, -180)


@pytest.mark.parametrize(
    "body",
    [
        {"location_label": "Club Vanta", "latitude": None, "longitude": None},
        {"location_label": None, "latitude": -34.5, "longitude": -58.4},
        {"location_label": "Club Vanta", "latitude": -34.5, "longitude": None},
        {"location_label": "x" * 256, "latitude": -34.5, "longitude": -58.4},
        {"location_label": "   ", "latitude": -34.5, "longitude": -58.4},
        {"location_label": "", "latitude": -34.5, "longitude": -58.4},
        {"location_label": "Club Vanta", "latitude": 91, "longitude": -58.4},
        {"location_label": "Club Vanta", "latitude": -91, "longitude": -58.4},
        {"location_label": "Club Vanta", "latitude": -34.5, "longitude": 181},
        {"location_label": "Club Vanta", "latitude": -34.5, "longitude": -181},
    ],
    ids=[
        "label-without-coordinates",
        "coordinates-without-label",
        "missing-longitude",
        "label-too-long",
        "label-blank",
        "label-empty",
        "latitude-above-90",
        "latitude-below-minus-90",
        "longitude-above-180",
        "longitude-below-minus-180",
    ],
)
def test_set_location_returns_422_for_invalid_body(client: TestClient, admin_headers: dict, meetup, body: dict):
    """WHY: A half-set or out-of-range location would break the map link on the meetup page."""
    resp = client.patch(_url(meetup), json=body, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_set_location_422_message_names_the_problem(client: TestClient, admin_headers: dict, meetup):
    """WHY: Error messages must say what failed and what to do (repo style)."""
    resp = client.patch(
        _url(meetup), json={"location_label": "Club Vanta", "latitude": 91, "longitude": 0}, headers=admin_headers
    )

    msg = resp.json()["detail"][0]["msg"]
    assert msg.startswith("Value error, Cannot set location: latitude must be between -90 and 90")


def test_set_location_returns_422_when_a_key_is_missing(client: TestClient, admin_headers: dict, meetup):
    """WHY: All three keys are required (null clears), so an empty body is not a silent clear."""
    resp = client.patch(_url(meetup), json={}, headers=admin_headers)

    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_invalid_location_leaves_the_stored_one_untouched(client: TestClient, admin_headers: dict, meetup):
    """WHY: A rejected request must not partially apply."""
    client.patch(_url(meetup), json=LOCATION, headers=admin_headers)

    client.patch(_url(meetup), json={**LOCATION, "latitude": 91}, headers=admin_headers)

    data = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}", headers=admin_headers).json()
    assert data["latitude"] == -34.5875


def test_set_location_works_on_a_finalized_meetup(client: TestClient, admin_headers: dict, session: Session, org):
    """WHY: The venue is metadata, not door state; fixing it after the event must be allowed."""
    finalized = make_meetup(session, org=org, is_finalized=True)

    resp = client.patch(_url(finalized), json=LOCATION, headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["is_finalized"] is True
    assert resp.json()["latitude"] == -34.5875


def test_set_location_returns_403_for_org_staff(client: TestClient, staff_headers: dict, meetup, org_staff_member):
    """WHY: Only org admins edit the venue."""
    resp = client.patch(_url(meetup), json=LOCATION, headers=staff_headers)

    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_set_location_returns_401_without_auth(client: TestClient, meetup):
    """WHY: The route is not public."""
    resp = client.patch(_url(meetup), json=LOCATION)

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED


def test_set_location_returns_404_for_a_meetup_of_another_org(
    client: TestClient, admin_headers: dict, session: Session, meetup
):
    """WHY: Org admins must not touch another org's meetups."""
    other_org = make_org(session, name="Other Org", slug="other-org")
    foreign = make_meetup(session, org=other_org, mazmo_meetup_url="https://mazmo.net/other/meetup-1")

    resp = client.patch(
        f"/organizations/{meetup.org_id}/meetups/{foreign.id}/location", json=LOCATION, headers=admin_headers
    )

    assert resp.status_code == status.HTTP_404_NOT_FOUND


def test_get_meetup_includes_location_for_org_staff(
    client: TestClient, admin_headers: dict, staff_headers: dict, meetup, org_staff_member
):
    """WHY: Every member sees the address on the meetup page, not only admins."""
    client.patch(_url(meetup), json=LOCATION, headers=admin_headers)

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}", headers=staff_headers)

    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["location_label"] == "Club Vanta, Palermo, Buenos Aires"
    assert (data["latitude"], data["longitude"]) == (-34.5875, -58.4306)


def test_get_meetup_without_location_returns_explicit_nulls(client: TestClient, admin_headers: dict, meetup):
    """WHY: The fields are always present so the frontend type is nullable, not optional."""
    resp = client.get(f"/organizations/{meetup.org_id}/meetups/{meetup.id}", headers=admin_headers)

    data = resp.json()
    assert {k: data[k] for k in CLEARED} == CLEARED


def test_list_meetups_includes_location(client: TestClient, admin_headers: dict, meetup):
    """WHY: The list uses the same MeetupPublic shape."""
    client.patch(_url(meetup), json=LOCATION, headers=admin_headers)

    resp = client.get(f"/organizations/{meetup.org_id}/meetups/", headers=admin_headers)

    assert resp.json()["meetups"][0]["location_label"] == "Club Vanta, Palermo, Buenos Aires"
