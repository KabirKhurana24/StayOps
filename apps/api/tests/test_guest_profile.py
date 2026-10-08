"""Guest profile: preferences, private notes, history, conversations and requests."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.reservations.models import ReservationStatus
from tests.conftest import Member
from tests.factories import make_guest, make_guest_request, make_reservation, transition
from tests.inbox_helpers import (
    GuestStay,
    api_get,
    build_guest_stay,
    events_of,
)


def test_update_preferences_and_notes(
    client: TestClient, db: Session, owner_a: Member, viewer_a: Member, guest_stay: GuestStay
) -> None:
    g = guest_stay.guest
    r = client.patch(
        f"/api/v1/guests/{g.id}",
        headers=owner_a.headers,
        json={
            "phone": "+31 6 1234 5678",
            "preferences": {
                "preferred_language": "Dutch",
                "usually_requests_early_check_in": True,
                "bedding": "Extra pillows",
            },
            "notes": "Returning guest; left the place spotless.",
            "expected_version": 1,
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["preferences"] == {
        "preferred_language": "Dutch",
        "usually_requests_early_check_in": True,
        "bedding": "Extra pillows",
    }
    assert out["notes"].startswith("Returning guest") and out["version"] == 2
    event = events_of(db, owner_a.organization_id, "guest.updated")[-1]
    assert event.payload["fields"] == ["notes", "phone", "preferences"]
    assert "spotless" not in str(event.payload) and "1234" not in str(event.payload)

    stale = client.patch(
        f"/api/v1/guests/{g.id}",
        headers=owner_a.headers,
        json={"notes": "x", "expected_version": 1},
    )
    assert stale.status_code == 409
    bad = client.patch(
        f"/api/v1/guests/{g.id}", headers=owner_a.headers, json={"preferences": {"vip": True}}
    )
    assert bad.status_code == 422
    other = make_guest(db, owner_a, "Lukas", "Becker", email="lukas@example.com")
    clash = client.patch(
        f"/api/v1/guests/{g.id}", headers=owner_a.headers, json={"email": "LUKAS@example.com"}
    )
    assert clash.status_code == 409 and clash.json()["error"]["details"]["guest_id"] == str(
        other.id
    )
    assert (
        client.patch(
            f"/api/v1/guests/{g.id}", headers=viewer_a.headers, json={"notes": "x"}
        ).status_code
        == 403
    )
    # Notes never reach the guest portal.
    assert "spotless" not in str(api_get(client, guest_stay.access.headers, "/guest/stay"))


def test_profile_history_across_properties(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    second = build_guest_stay(db, owner_a, unit_name="L1", first="X", last="Y")
    past = make_reservation(
        db, owner_a, second.unit, s.guest, check_in=utc_now() - timedelta(days=40), nights=2
    )
    transition(db, owner_a, past, ReservationStatus.CHECKED_IN)
    transition(db, owner_a, past, ReservationStatus.CHECKED_OUT)
    make_guest_request(db, owner_a, s.conversation)

    g = api_get(client, owner_a.headers, f"/guests/{s.guest.id}")
    assert g["stats"]["total_stays"] == 2 and g["stats"]["completed_stays"] == 1
    history = {p["property_id"]: p for p in g["stats"]["properties"]}
    assert set(history) == {str(s.prop.id), str(second.prop.id)}  # two properties
    assert history[str(s.prop.id)]["stays"] == 1
    assert history[str(s.prop.id)]["last_check_out"] is None  # upcoming, not completed
    assert history[str(second.prop.id)]["last_check_out"] is not None

    convs = api_get(client, owner_a.headers, "/inbox/conversations", guest_id=str(s.guest.id))
    assert [c["id"] for c in convs["items"]] == [str(s.conversation.id)]
    reqs = api_get(client, owner_a.headers, "/guest-requests", guest_id=str(s.guest.id))
    assert reqs["total"] == 1
