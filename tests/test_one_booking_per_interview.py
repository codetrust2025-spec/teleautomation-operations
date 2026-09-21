"""One interview is one booking, whichever way it arrives.

On 18 Sep one candidate's 14:15-14:45 interview was booked twice, eight seconds
apart: once through the Daily Ops slot route, which had no key, no duplicate
check and no share in the booking form's lock, and once through the form. The
same afternoon the team booked a 16:30-17:30 interview through the form and the
Gmail automation booked it again from the invite, because nothing on the
hand-booked row named the calendar event. All four rows were marked attended.

These tests drive the real entry points -- /bookings/confirm, the Daily Ops
slot route, execute_auto_booking -- against an isolated store.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.candidates import router as candidates_router
from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending

ROOT = Path(__file__).resolve().parent.parent
NAME = "Synthetic Candidate"
PHONE = "9000000123"


def _app(monkeypatch, tmp_path) -> FastAPI:
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "candidate-proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr(pending, "PENDING_PAYMENT_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(pending, "PENDING_PAYMENT_INDEX", str(tmp_path / "pending" / "index.json"))
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr("features.payment_proof_validator.validate_interview_invite", lambda *_a, **_k: (True, ""))
    app = FastAPI()
    install_public_slot_routes(app)
    app.include_router(candidates_router)
    return app


def _profile() -> dict:
    """A paid-up profile-service candidate: books without a payment proof."""
    return cs.create_candidate({
        "name": NAME, "phone": PHONE, "service_type": "profile_service",
        "technology": "ServiceNow", "reference": "Owner", "stage": "in_progress",
        "payment": 20000, "expected_payment": 20000,
    })


def _form(client: TestClient, *, time="02:15 PM", time_end="02:45 PM", key="form-key", round_="L1"):
    return client.post(
        "/bookings/confirm",
        data={
            "name": NAME, "service_type": "profile_service", "interview_round": round_,
            "date": "2099-09-18", "time": time, "time_end": time_end, "idempotency_key": key,
        },
        files={"file": ("invite.png", b"invite", "image/png")},
    )


def _daily_ops(client: TestClient, candidate_id: str, *, time="14:15", time_end="14:45", round_="L1"):
    return client.post("/candidates/interviews/slots", json={
        "candidate_id": candidate_id, "date": "2099-09-18",
        "time": time, "time_end": time_end, "interview_round": round_,
    }).json()


def _standing(date="2099-09-18") -> list[dict]:
    """Every stored booking still standing that day -- read raw, not through
    the list view, which shows one row per profile candidate."""
    return [row for row in (cs._load(force=True).get("candidates") or [])
            if cs.slot_still_stands(row) and str(row.get("date"))[:10] == date]


# ── Daily Ops slot route ────────────────────────────────────────────────────

def test_the_same_booking_asked_for_twice_is_one_booking(monkeypatch, tmp_path):
    client = TestClient(_app(monkeypatch, tmp_path))
    person = _profile()

    first = _daily_ops(client, person["id"])
    second = _daily_ops(client, person["id"])

    assert first["status"] == second["status"] == "ok"
    assert second["already_booked"] is True
    assert second["candidate"]["id"] == first["candidate"]["id"]
    assert "Nothing new was created" in second["message"]
    assert len(_standing()) == 1


@pytest.mark.parametrize("change", [
    {"time": "16:30", "time_end": "17:30"},          # a later interview that day
    {"round_": "L2"},                                  # both rounds stated, and different
])
def test_a_different_interview_the_same_day_still_books(monkeypatch, tmp_path, change):
    client = TestClient(_app(monkeypatch, tmp_path))
    person = _profile()

    _daily_ops(client, person["id"])
    second = _daily_ops(client, person["id"], **change)

    assert second["status"] == "ok" and not second.get("already_booked")
    assert len(_standing()) == 2


def test_a_cancelled_booking_does_not_stop_the_slot_being_booked_again(monkeypatch, tmp_path):
    client = TestClient(_app(monkeypatch, tmp_path))
    person = _profile()
    first = _daily_ops(client, person["id"])["candidate"]
    cs.set_interview_attendance(first["id"], status="cancelled", by="operations_admin")

    again = _daily_ops(client, person["id"])

    assert not again.get("already_booked")
    assert again["candidate"]["id"] != first["id"]
    assert [row["id"] for row in _standing()] == [again["candidate"]["id"]]


# ── The booking form ────────────────────────────────────────────────────────

def test_the_form_after_daily_ops_returns_that_booking(monkeypatch, tmp_path):
    """The form submits "02:15 PM"; the row stores "14:15". Comparing the first
    five characters never matched, so this used to be refused as a clash with
    the candidate's own booking."""
    client = TestClient(_app(monkeypatch, tmp_path))
    person = _profile()
    booked = _daily_ops(client, person["id"])["candidate"]

    response = _form(client)

    assert response.status_code == 200, response.text
    assert response.json()["candidate"]["id"] == booked["id"]
    assert len(_standing()) == 1


def test_the_form_sees_every_booking_of_a_profile_candidate(monkeypatch, tmp_path):
    """The list view keeps one row per profile candidate -- the newest -- so a
    booking behind a more recently edited row was invisible to the form."""
    client = TestClient(_app(monkeypatch, tmp_path))
    person = _profile()
    booked = _daily_ops(client, person["id"])["candidate"]
    later = _daily_ops(client, person["id"], time="18:00", time_end="18:30")["candidate"]
    cs.update_candidate(later["id"], {"notes": "edited after the 14:15 booking"}, allow_slot_without_rules=True)

    response = _form(client)

    assert response.status_code == 200, response.text
    assert response.json()["candidate"]["id"] == booked["id"]
    assert len(_standing()) == 2


def test_a_retry_with_the_same_key_returns_the_first_booking(monkeypatch, tmp_path):
    client = TestClient(_app(monkeypatch, tmp_path))
    _profile()

    first = _form(client)
    retry = _form(client)

    assert first.status_code == retry.status_code == 200
    assert retry.json()["candidate"]["id"] == first.json()["candidate"]["id"]
    assert len(_standing()) == 1


def test_a_cancelled_booking_is_not_replayed_as_a_confirmation(monkeypatch, tmp_path):
    """The same key after a cancellation used to return the cancelled row, and
    the form said "Slot confirmed" for an interview that stayed cancelled."""
    client = TestClient(_app(monkeypatch, tmp_path))
    _profile()
    first = _form(client).json()["candidate"]
    cs.set_interview_attendance(first["id"], status="cancelled", by="operations_admin")

    again = _form(client)

    assert again.status_code == 200, again.text
    assert again.json()["candidate"]["id"] != first["id"]
    assert [row["id"] for row in _standing()] == [again.json()["candidate"]["id"]]
    assert cs.row_interview_attendance_status(cs.get_candidate(first["id"])) == "cancelled"


# ── Races ───────────────────────────────────────────────────────────────────

def _first_write_is_slow(monkeypatch) -> threading.Event:
    """Hold the first booking write open, as a busy store does, and signal once
    it is under way: the second request is sent into exactly that gap, after
    the first has decided "not booked yet" and before its row exists."""
    real = cs.assign_interview_slot
    started = threading.Event()
    calls = []

    def slow(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            started.set()
            time.sleep(0.4)
        return real(**kwargs)

    monkeypatch.setattr(cs, "assign_interview_slot", slow)
    return started


def _race(first, second, started: threading.Event):
    """Run `first`; send `second` while first's write is still open."""
    results, errors = [None, None], []

    def run(index, call, wait=None):
        try:
            if wait is not None:
                assert wait.wait(10), "the first request never reached its write"
            results[index] = call()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(0, first)),
               threading.Thread(target=run, args=(1, second, started))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    return results


def test_daily_ops_during_a_form_booking_books_once(monkeypatch, tmp_path):
    """The 18 Sep double booking: the form had read "not booked yet" and was
    still writing when the slot route, holding no lock, booked the same
    interview; the form then wrote a second row."""
    app = _app(monkeypatch, tmp_path)
    person = _profile()
    started = _first_write_is_slow(monkeypatch)

    form, ops = _race(lambda: _form(TestClient(app)),
                      lambda: _daily_ops(TestClient(app), person["id"]), started)

    assert form.status_code == 200 and ops["status"] == "ok", form.text
    assert ops.get("already_booked") is True
    assert ops["candidate"]["id"] == form.json()["candidate"]["id"]
    assert len(_standing()) == 1


def test_a_double_click_on_the_form_books_once(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    _profile()
    started = _first_write_is_slow(monkeypatch)

    first, second = _race(lambda: _form(TestClient(app)), lambda: _form(TestClient(app)), started)

    assert first.status_code == second.status_code == 200
    assert first.json()["candidate"]["id"] == second.json()["candidate"]["id"]
    assert len(_standing()) == 1


def test_daily_ops_twice_at_the_same_moment_books_once(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    person = _profile()
    started = _first_write_is_slow(monkeypatch)

    first, second = _race(lambda: _daily_ops(TestClient(app), person["id"]),
                          lambda: _daily_ops(TestClient(app), person["id"]), started)

    assert not first.get("already_booked") and second.get("already_booked") is True
    assert second["candidate"]["id"] == first["candidate"]["id"]
    assert len(_standing()) == 1


def test_the_booking_lock_is_enough_only_because_production_runs_one_worker():
    """BOOKING_LOCK serialises threads in one process. A second worker process
    would have its own lock and bring the double booking back, so scaling out
    has to come with a lock that spans processes."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    command = re.search(r'^CMD \[(.*)\]\s*$', dockerfile, re.MULTILINE)
    assert command, "the Dockerfile no longer states how the server starts"
    words = [part.strip().strip('"') for part in command.group(1).split(",")]
    assert words[words.index("--workers") + 1] == "1"


# ── A Gmail invite for an interview booked by hand ─────────────────────────

@pytest.fixture
def gmail(monkeypatch):
    from services import interview_auto_booking as booking
    import test_interview_auto_booking as harness

    monkeypatch.setenv("AI_INTERVIEW_AUTO_BOOKING_ENABLED", "true")
    linked = []
    monkeypatch.setattr(booking.candidate_store, "link_invite_to_slot",
                        lambda cid, **identity: linked.append((cid, identity)) or {"id": cid},
                        raising=False)

    def run(rows):
        _candidate, audits = harness.install_store_fakes(monkeypatch, rows=rows)
        created = []
        monkeypatch.setattr(booking.candidate_store, "assign_interview_slot",
                            harness.slot_writer("new-slot", capture=created))
        invite = harness.result()
        invite["calendar"] = {"uid": "invite-uid-1", "sequence": 0}
        outcome = harness.execute(invite)
        return outcome, audits, created

    run.linked = linked
    return run


def _by_hand(**overrides):
    row = {"id": "hand-1", "name": "Rahul", "slot_confirmed": True, "booking_type": "Interview",
           "date": "2099-07-20", "time": "15:00", "time_end": "15:30",
           "interview_booking_source": "candidate_booked"}
    row.update(overrides)
    return row


def test_an_invite_for_a_hand_booked_interview_is_linked_not_booked_again(gmail):
    outcome, audits, created = gmail([_by_hand()])

    assert created == [], "a second booking was created for the same interview"
    assert outcome["status"] == "Duplicate Ignored"
    assert audits[-1]["booking_id"] == "hand-1"
    assert outcome["notification"]["booking_id"] == "hand-1"
    ((cid, identity),) = gmail.linked
    assert cid == "hand-1"
    assert identity["interview_calendar_uid"] == "invite-uid-1"
    assert identity["interview_source_message_id"] == "gm1"


def test_a_hand_booking_stated_in_12_hour_time_is_still_the_same_interview(gmail):
    outcome, _audits, created = gmail([_by_hand(time="03:00 PM", time_end="03:30 PM")])

    assert created == [] and outcome["status"] == "Duplicate Ignored"


@pytest.mark.parametrize("difference", [
    {"time_end": "16:00"},                           # a different end is a different interview
    {"interview_calendar_uid": "another-invite"},    # carries its own event: never matched by time
    {"interview_source_message_id": "another-mail"},
    {"booking_type": "Assessment"},
])
def test_anything_short_of_the_same_interview_still_books(gmail, difference):
    outcome, _audits, created = gmail([_by_hand(**difference)])

    assert outcome["status"] == "Auto Booked"
    assert len(created) == 1
    assert gmail.linked == []


def test_two_hand_bookings_at_that_time_are_not_guessed_between(gmail):
    outcome, _audits, created = gmail([_by_hand(), _by_hand(id="hand-2")])

    assert outcome["status"] == "Auto Booked"
    assert len(created) == 1
    assert gmail.linked == []


def test_a_duplicate_invite_alert_names_the_booking_it_duplicates(gmail):
    """"Already booked" used to carry no booking id, so it could not be followed
    to the booking or corrected when that booking went."""
    outcome, audits, created = gmail([_by_hand(interview_source_message_id="gm1")])

    assert created == [] and outcome["status"] == "Duplicate Ignored"
    assert audits[-1]["booking_id"] == "hand-1"
    assert gmail.linked == []
