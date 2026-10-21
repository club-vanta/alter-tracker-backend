"""Tests for POST /guests/refresh-mazmo and the bounded fetch_users concurrency.

The refresh re-fetches every Mazmo-linked guest so guests synced before
GuestMazmoProfile existed get their avatar/gender/pronoun. The Mazmo client
is mocked via the mock_mazmo fixture (which patches app.services.sync).
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import get_settings
from app.domain_types import MazmoUserId
from app.models.models import (
    EventLog,
    EventType,
    Guest,
    GuestDisplaynameHistory,
    GuestDisplaynameSource,
    GuestMazmoProfile,
    Meetup,
    MeetupRsvp,
)
from app.services.mazmo import MazmoClient
from tests.conftest import make_guest, make_meetup, make_org, make_rsvp


def _mazmo_user(username: str, displayname: str, **overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "username": username,
        "displayname": displayname,
        "avatar": None,
        "age": None,
        "gender": None,
        "pronoun": None,
        "suspended": False,
        "banned": False,
        "deleted": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_refresh_fills_profiles_for_every_linked_guest(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """
    Verify that every Mazmo-linked guest gets a GuestMazmoProfile.

    WHY: guests synced before the profile table existed have no avatar,
    gender, or pronoun until something re-fetches them from Mazmo.
    """
    alice = make_guest(session, mazmo_user_id=1101, mazmo_handle="alice_r", displayname="Alice")
    bob = make_guest(session, mazmo_user_id=1102, mazmo_handle="bob_r", displayname="Bob")
    make_guest(session, mazmo_user_id=None, mazmo_handle=None, displayname="Manual Guest")
    mock_mazmo.fetch_users.return_value = {
        MazmoUserId(1101): _mazmo_user(
            "alice_r", "Alice", gender="FEMALE", pronoun="FEMALE", avatar=SimpleNamespace(default="https://a.jpg")
        ),
        MazmoUserId(1102): _mazmo_user("bob_r", "Bob", age=31),
    }

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"total": 2, "refreshed": 2, "not_found": 0}
    requested_ids = set(mock_mazmo.fetch_users.call_args.args[0])
    assert requested_ids == {1101, 1102}

    alice_profile = session.get(GuestMazmoProfile, alice.id)
    bob_profile = session.get(GuestMazmoProfile, bob.id)
    assert alice_profile is not None and alice_profile.gender == "FEMALE"
    assert alice_profile.avatar_url == "https://a.jpg"
    assert bob_profile is not None and bob_profile.age == 31


def test_refresh_records_displayname_changes(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """
    Verify that a changed displayname is updated with history and an audit event.

    WHY: the refresh reuses the sync's displayname upsert, so a rename found
    here must leave the same trail as one found by a meetup sync.
    """
    guest = make_guest(session, mazmo_user_id=1201, mazmo_handle="renamed_r", displayname="Old Name")
    mock_mazmo.fetch_users.return_value = {MazmoUserId(1201): _mazmo_user("renamed_r", "New Name")}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == "New Name"
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert [h.displayname for h in history] == ["New Name"]
    events = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).all()
    assert len(events) == 1


def test_refresh_counts_guests_missing_from_mazmo_and_leaves_them_alone(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """
    Verify that guests Mazmo leaves out are reported as not_found and kept as is.

    WHY: a missing entry is not proof the data changed, so the refresh must
    not blank or rename that guest.
    """
    guest = make_guest(session, mazmo_user_id=1301, mazmo_handle="gone_r", displayname="Still Here")
    mock_mazmo.fetch_users.return_value = {}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == {"total": 1, "refreshed": 0, "not_found": 1}
    session.refresh(guest)
    assert guest.displayname == "Still Here"
    assert session.get(GuestMazmoProfile, guest.id) is None


def test_refresh_never_touches_rsvps(client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock):
    """
    Verify that RSVPs are left alone, including their cancelled flag.

    WHY: the refresh is guest-wide, not meetup-scoped; only a meetup sync
    knows who RSVPed, so it is the only thing allowed to change RSVPs.
    """
    org = make_org(session, name="Refresh Org", slug="refresh-org")
    meetup = make_meetup(session, org=org)
    guest = make_guest(session, mazmo_user_id=1401, mazmo_handle="rsvp_r", displayname="Rsvp Guest")
    rsvp = make_rsvp(session, meetup=meetup, guest=guest, has_arrived=True, arrival_order=1)
    mock_mazmo.fetch_users.return_value = {}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    stored = session.exec(select(MeetupRsvp).where(MeetupRsvp.guest_id == guest.id)).one()
    assert stored is rsvp
    assert stored.cancelled_rsvp is False
    assert stored.has_arrived is True


def test_refresh_returns_502_when_mazmo_errors(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """Verify that a Mazmo HTTP error maps to 502 and writes nothing."""
    guest = make_guest(session, mazmo_user_id=1501, mazmo_handle="err_r", displayname="Err Guest")
    response = httpx.Response(503, request=httpx.Request("GET", "https://prod.mazmoapi.net/users"))
    mock_mazmo.fetch_users.side_effect = httpx.HTTPStatusError("boom", request=response.request, response=response)

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert "No guest data was changed" in resp.json()["detail"]
    assert session.get(GuestMazmoProfile, guest.id) is None


def test_refresh_returns_504_when_mazmo_unreachable(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """Verify that a network error talking to Mazmo maps to 504."""
    make_guest(session, mazmo_user_id=1601, mazmo_handle="net_r", displayname="Net Guest")
    mock_mazmo.fetch_users.side_effect = httpx.ConnectError("timed out")

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_504_GATEWAY_TIMEOUT


def test_refresh_requires_authentication(client: TestClient, mock_mazmo: AsyncMock):
    """Verify that the refresh is not reachable without a token."""
    resp = client.post("/guests/refresh-mazmo")

    assert resp.status_code == status.HTTP_401_UNAUTHORIZED
    mock_mazmo.fetch_users.assert_not_called()


# ── Deleted Mazmo accounts ────────────────────────────────────────────────────

# What Mazmo's batch /users endpoint returns for a deleted account: username
# and displayname replaced by the same hash, profile fields cleared.
DELETED_HASH = "02fb52d32422e1c93478344c2a65f648a503cba3588b396b06d0862fa52163d3"


def _deleted_mazmo_user() -> SimpleNamespace:
    return _mazmo_user(DELETED_HASH, DELETED_HASH, deleted=True)


def _add_history(session: Session, guest: Guest, displayname: str, source: GuestDisplaynameSource) -> None:
    session.add(GuestDisplaynameHistory(guest_id=guest.id, displayname=displayname, source=source))
    session.flush()


def test_refresh_keeps_real_name_and_profile_of_deleted_account(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """
    Verify that a deleted Mazmo account keeps its name and last known profile.

    WHY: Mazmo swaps a deleted account's displayname for a hash and clears its
    profile. Storing that would leave staff looking at a 64-char hash.
    """
    guest = make_guest(session, mazmo_user_id=1701, mazmo_handle="machoarriba", displayname="machoarriba")
    session.add(GuestMazmoProfile(guest_id=guest.id, gender="MALE", pronoun="MALE", age=24))
    session.flush()
    mock_mazmo.fetch_users.return_value = {MazmoUserId(1701): _deleted_mazmo_user()}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == "machoarriba"
    profile = session.get(GuestMazmoProfile, guest.id)
    assert profile is not None
    session.refresh(profile)
    assert profile.mazmo_deleted is True
    assert (profile.gender, profile.pronoun, profile.age) == ("MALE", "MALE", 24)
    history = session.exec(select(GuestDisplaynameHistory).where(GuestDisplaynameHistory.guest_id == guest.id)).all()
    assert history == []


def test_refresh_restores_name_already_replaced_by_hash(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """
    Verify that a guest whose displayname is already the hash gets the
    previous name back from its history.

    WHY: syncs that ran before the deleted flag was read stored the hash as
    a regular rename; the real name is still in the history.
    """
    guest = make_guest(session, mazmo_user_id=1801, mazmo_handle="machoarriba", displayname=DELETED_HASH)
    _add_history(session, guest, "machoarriba", GuestDisplaynameSource.BACKFILL)
    _add_history(session, guest, DELETED_HASH, GuestDisplaynameSource.SYNC)
    mock_mazmo.fetch_users.return_value = {MazmoUserId(1801): _deleted_mazmo_user()}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == "machoarriba"
    latest = session.exec(
        select(GuestDisplaynameHistory)
        .where(GuestDisplaynameHistory.guest_id == guest.id)
        .order_by(GuestDisplaynameHistory.id.desc())  # type: ignore[union-attr]
    ).first()
    assert latest is not None and latest.displayname == "machoarriba"
    event = session.exec(
        select(EventLog)
        .where(EventLog.guest_id == guest.id)
        .where(EventLog.event_type == EventType.GUEST_DISPLAYNAME_CHANGED)
    ).one()
    assert event.reason is not None and "Mazmo anonymized the account" in event.reason


def test_refresh_leaves_hash_when_history_has_no_other_name(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """Verify that a hashed guest with nothing to restore stays as is and is flagged."""
    guest = make_guest(session, mazmo_user_id=1901, mazmo_handle=DELETED_HASH, displayname=DELETED_HASH)
    _add_history(session, guest, DELETED_HASH, GuestDisplaynameSource.SYNC)
    mock_mazmo.fetch_users.return_value = {MazmoUserId(1901): _deleted_mazmo_user()}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == DELETED_HASH
    profile = session.get(GuestMazmoProfile, guest.id)
    assert profile is not None and profile.mazmo_deleted is True


def test_meetup_sync_keeps_deleted_account_rsvp_and_name(
    client: TestClient, admin_headers: dict, session: Session, meetup: Meetup, mock_mazmo: AsyncMock
):
    """
    Verify that a meetup sync treats a deleted account like the refresh does,
    and still keeps its RSVP.

    WHY: Mazmo keeps a deleted account in the event's RSVP list, so the
    meetup sync is where the hash first shows up.
    """
    guest = make_guest(session, mazmo_user_id=2001, mazmo_handle="machoarriba", displayname="machoarriba")
    mock_mazmo.fetch_rsvps.return_value = {
        MazmoUserId(2001): SimpleNamespace(userId=2001, joinedAt=datetime(2026, 3, 17, tzinfo=UTC)),
    }
    mock_mazmo.fetch_users.return_value = {MazmoUserId(2001): _deleted_mazmo_user()}

    resp = client.post(f"/organizations/{meetup.org_id}/meetups/{meetup.id}/sync", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == "machoarriba"
    rsvp = session.exec(select(MeetupRsvp).where(MeetupRsvp.guest_id == guest.id)).one()
    assert rsvp.cancelled_rsvp is False
    profile = session.get(GuestMazmoProfile, guest.id)
    assert profile is not None and profile.mazmo_deleted is True


def test_refresh_restores_name_of_banned_account_with_hashed_name(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """
    Verify that a banned account Mazmo anonymized is treated like a deleted one.

    WHY: Mazmo also hashes the names of banned accounts, with deleted: false
    (seen with real accounts @maxslave and @pua), so the deleted flag alone
    misses them.
    """
    banned_hash = "02cde3bd689d6d428acb5f0c252f3d30e0a2fabf1a9c5b795dbd9289c5a87b28"
    guest = make_guest(session, mazmo_user_id=2101, mazmo_handle="maxslave", displayname=banned_hash)
    _add_history(session, guest, "Maxslave", GuestDisplaynameSource.BACKFILL)
    _add_history(session, guest, banned_hash, GuestDisplaynameSource.SYNC)
    session.add(GuestMazmoProfile(guest_id=guest.id, gender="MALE", age=40))
    session.flush()
    mock_mazmo.fetch_users.return_value = {MazmoUserId(2101): _mazmo_user(banned_hash, banned_hash, banned=True)}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == "Maxslave"
    profile = session.get(GuestMazmoProfile, guest.id)
    assert profile is not None
    session.refresh(profile)
    assert profile.mazmo_banned is True
    assert profile.mazmo_deleted is False
    assert (profile.gender, profile.age) == ("MALE", 40)


def test_refresh_updates_name_of_banned_account_with_readable_name(
    client: TestClient, admin_headers: dict, session: Session, mock_mazmo: AsyncMock
):
    """Verify that a banned account whose name Mazmo left readable is still renamed normally."""
    guest = make_guest(session, mazmo_user_id=2201, mazmo_handle="readable", displayname="Old Name")
    mock_mazmo.fetch_users.return_value = {MazmoUserId(2201): _mazmo_user("readable", "New Name", banned=True)}

    resp = client.post("/guests/refresh-mazmo", headers=admin_headers)

    assert resp.status_code == status.HTTP_200_OK
    session.refresh(guest)
    assert guest.displayname == "New Name"


# ── fetch_users concurrency ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_users_limits_concurrent_batches():
    """
    Verify that fetch_users never has more than mazmo_max_concurrent_requests
    batches in flight.

    WHY: refreshing every guest means dozens of batches; firing them all at
    once risks rate limiting from Mazmo's CDN/WAF.
    """
    settings = get_settings().model_copy(update={"mazmo_user_batch_size": 1, "mazmo_max_concurrent_requests": 2})
    in_flight = 0
    peak = 0

    async def mock_get(url, params=None):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        uid = params["ids"]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {uid: {"username": f"user{uid}", "displayname": f"User {uid}"}}
        mock_resp.is_error = False
        return mock_resp

    with patch.object(httpx.AsyncClient, "get", side_effect=mock_get):
        async with MazmoClient(settings) as client:
            result = await client.fetch_users([MazmoUserId(n) for n in range(1, 7)])

    assert len(result) == 6
    assert peak == 2
