"""Confirmed slots for an anonymous visitor: names, times, no client or technology.

GET /public/slots/booked is public: the booking page and the dashboard badge
both read it. Since the 11 Oct audit a signed-out caller does not get the
client company or technology. Names were cut to initials then; since 12 Oct,
on the owner's request, they are shown in full to everyone. Signed in, the
full list. Names are invented.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as auth
from core import public_slot_api
from features import candidate_store as cs

SNAPSHOT = {
    "from": "2026-10-01", "to": "2026-12-01", "count": 1, "needs_status_update_count": 0, "upcoming_count": 1,
    "slots": [{
        "name": "Asha Fakename", "technology": "React JS", "company": "Example Client", "interview_round": "L1",
        "date": "2026-10-20", "time": "10:00", "time_end": "10:30", "interview_booking_source": "candidate_booked",
        "booking_type": "Interview", "slot_phase": "upcoming",
    }],
}


@pytest.fixture
def client(monkeypatch):
    import main

    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "test-secret")
    assert auth.auth_enabled()
    monkeypatch.setattr(cs, "public_booked_interview_slots", lambda days=60: {**SNAPSHOT, "slots": list(SNAPSHOT["slots"])})
    return TestClient(main.app)


def test_signed_out_gets_full_names_and_times_but_no_client_or_technology(client):
    client.cookies.clear()
    body = client.get("/public/slots/booked").json()
    [slot] = body["slots"]
    assert body["details_withheld"] is True
    assert slot["name"] == "Asha Fakename"
    assert "company" not in slot and "technology" not in slot
    assert (slot["date"], slot["time"], slot["time_end"], slot["interview_round"], slot["slot_phase"]) == (
        "2026-10-20", "10:00", "10:30", "L1", "upcoming")
    assert "Example Client" not in str(body) and "React JS" not in str(body)
    assert body["count"] == 1 and body["upcoming_count"] == 1


def test_a_signed_in_operator_still_gets_the_full_list(client):
    client.cookies.clear()
    client.cookies.set(auth.SESSION_COOKIE, auth.create_session_token("handler1", role="handler", reference="Owner One"))
    body = client.get("/public/slots/booked").json()
    assert "details_withheld" not in body
    assert body["slots"][0]["name"] == "Asha Fakename"
    assert body["slots"][0]["company"] == "Example Client"


def test_a_forged_cookie_is_treated_as_signed_out(client):
    client.cookies.clear()
    client.cookies.set(auth.SESSION_COOKIE, "not-a-real-token")
    slot = client.get("/public/slots/booked").json()["slots"][0]
    assert slot["name"] == "Asha Fakename"
    assert "company" not in slot and "technology" not in slot


def test_no_name_is_cut_to_initials_any_more():
    assert not hasattr(public_slot_api, "_initials")
