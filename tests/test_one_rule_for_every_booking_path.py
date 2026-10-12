"""One genuine interview is one booking, on every path (12 Oct).

On 9 Oct one candidate's 4:00 pm interview was booked twice, three minutes
apart, from the same invite screenshot: the booking form knew a repeat only by
its own form token. Every path now asks one question before it writes --
`find_duplicate_booking` / `same_interview` -- under one lock:

* the same invitation (calendar event, source mail, invite screenshot) is one
  interview;
* two different calendar events, or anything both sides state that differs
  (company, round, end time, booking type), are two interviews -- a person may
  hold any number of them, at the same hour too;
* a field one side leaves blank never tells two interviews apart.

These drive the real routes and the real store against an isolated store.
Names are invented.
"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.candidates import router as candidates_router
from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs
from features import pending_slot_payment as pending

NAME = "Synthetic Candidate"
PHONE = "9000000123"
DAY = "2099-09-18"


@pytest.fixture(autouse=True)
def _far_dates_bookable(monkeypatch):
    monkeypatch.setattr(cs, "INTERVIEW_DATE_MAX_DAYS_AHEAD", 10 ** 6)


@pytest.fixture
def client(monkeypatch, tmp_path):
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
    return TestClient(app)


def _profile() -> dict:
    return cs.create_candidate({
        "name": NAME, "phone": PHONE, "service_type": "profile_service", "technology": "ServiceNow",
        "reference": "Owner", "stage": "in_progress", "payment": 20000, "expected_payment": 20000,
    })


def _form(client, *, key, invite=b"invite-one", company="Example Client", round_="L1",
          time_="02:15 PM", end="02:45 PM"):
    return client.post(
        "/bookings/confirm",
        data={"name": NAME, "service_type": "profile_service", "interview_round": round_, "company": company,
              "date": DAY, "time": time_, "time_end": end, "idempotency_key": key},
        files={"file": ("invite.png", invite, "image/png")},
    )


def _daily_ops(client, candidate_id, *, round_="L1", time_="14:15", end="14:45"):
    return client.post("/candidates/interviews/slots", json={
        "candidate_id": candidate_id, "date": DAY, "time": time_, "time_end": end, "interview_round": round_,
    }).json()


def _standing() -> list[dict]:
    return [r for r in cs._load(force=True)["candidates"] if cs.slot_still_stands(r) and str(r.get("date"))[:10] == DAY]


# ── the one rule ──────────────────────────────────────────────────────────────

def _d(**kw):
    base = {"calendar_uid": "", "source_message_id": "", "invites": set(), "booking_type": "Interview",
            "end": "14:45", "round": "L1", "company": "Example Client"}
    base.update(kw)
    return base


@pytest.mark.parametrize("a, b, same", [
    (_d(), _d(), True),                                                         # nothing tells them apart
    (_d(invites={"x"}, round="L1"), _d(invites={"x"}, round="HR"), True),       # one invite screenshot
    (_d(calendar_uid="evt-1"), _d(calendar_uid="EVT-1"), True),                 # one calendar event
    (_d(calendar_uid="evt-1"), _d(calendar_uid="evt-2"), False),                # two events
    (_d(company="First Client"), _d(company="Second Client"), False),           # two companies
    (_d(company="Evoke"), _d(company="Evoke Technologies Pvt Ltd"), True),      # a short name is the same company
    (_d(company="Evoke Technologies"), _d(company="Evoke Technologies Pvt. Ltd."), True),
    (_d(round="L1"), _d(round="L2"), False),                                    # two rounds
    (_d(company=""), _d(company="Example Client"), True),                       # a blank never separates
    (_d(round=""), _d(round="L2"), True),
    (_d(end="14:45"), _d(end="15:00"), False),
    (_d(booking_type="Assessment"), _d(), False),
    (_d(invites={"x"}), _d(invites={"y"}), True),                               # two screenshots of one interview
])
def test_the_rule(a, b, same):
    assert cs.same_interview(a, b) is same
    assert cs.same_interview(b, a) is same


# ── booking form ──────────────────────────────────────────────────────────────

def test_the_same_invite_sent_again_from_a_fresh_page_is_one_booking(client):
    _profile()
    first = _form(client, key="page-one").json()
    again = _form(client, key="page-two").json()

    assert first["status"] == again["status"] == "ok"
    assert again["already_booked"] is True and again["action"] == "skip_exists"
    assert again["candidate"]["id"] == first["candidate"]["id"]
    assert len(_standing()) == 1


def test_a_retry_of_the_same_submission_is_answered_with_its_own_booking(client):
    _profile()
    first = _form(client, key="same-key").json()
    retry = _form(client, key="same-key").json()

    assert retry["status"] == "ok" and retry["action"] == "skip_exists"
    assert "already_booked" not in retry        # a plain retry still reads "Slot confirmed"
    assert retry["candidate"]["id"] == first["candidate"]["id"]
    assert len(_standing()) == 1


def test_a_different_interview_at_the_same_hour_books(client):
    _profile()
    _form(client, key="first", invite=b"invite-one", company="First Client")
    other = _form(client, key="second", invite=b"invite-two", company="Second Client").json()

    assert other["status"] == "ok" and not other.get("already_booked")
    assert len(_standing()) == 2


def test_a_different_round_at_the_same_hour_books(client):
    _profile()
    _form(client, key="first", invite=b"invite-l1", round_="L1")
    hr = _form(client, key="second", invite=b"invite-hr", round_="HR").json()

    assert hr["status"] == "ok" and not hr.get("already_booked")
    assert len(_standing()) == 2


def test_two_fresh_submissions_of_one_invite_at_the_same_moment_book_once(client, monkeypatch):
    _profile()
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
    results, errors = [None, None], []

    def run(index, key, wait=None):
        try:
            if wait is not None:
                assert wait.wait(10)
            results[index] = _form(TestClient(client.app), key=key).json()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(0, "tab-one")),
               threading.Thread(target=run, args=(1, "tab-two", started))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(_standing()) == 1
    assert sum(1 for r in results if r and r.get("already_booked")) == 1


# ── Daily Ops and the store every path writes through ──────────────────────

def test_daily_ops_books_a_repeat_once_and_a_different_round_again(client):
    person = _profile()
    first = _daily_ops(client, person["id"])
    again = _daily_ops(client, person["id"])
    other_round = _daily_ops(client, person["id"], round_="HR")

    assert again["already_booked"] is True and again["candidate"]["id"] == first["candidate"]["id"]
    assert other_round["status"] == "ok" and not other_round.get("already_booked")
    assert len(_standing()) == 2


def test_a_hand_booking_without_a_company_is_the_form_booking_it_repeats(client):
    _profile()
    form = _form(client, key="form", time_="02:15 PM", end="02:45 PM").json()
    person_row = form["candidate"]
    again = _daily_ops(client, person_row["id"], time_="14:15", end="14:45")

    assert again["already_booked"] is True
    assert len(_standing()) == 1


def test_gmail_bookings_are_one_per_calendar_event(client):
    person = _profile()
    first = cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                                     interview_calendar_uid="evt-A", interview_booking_source="ai_auto_booked")
    other = cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                                     interview_calendar_uid="evt-B", interview_booking_source="ai_auto_booked")
    with pytest.raises(cs.DuplicateBookingError) as same_event:
        cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                                 interview_calendar_uid="evt-A", interview_booking_source="ai_auto_booked")

    assert first["id"] != other["id"]                      # two invitations, two interviews
    assert same_event.value.existing["id"] == first["id"]  # one invitation, one booking
    assert len(_standing()) == 2


def test_an_invite_for_an_interview_already_booked_by_hand_is_that_booking(client):
    person = _profile()
    hand = _daily_ops(client, person["id"], time_="16:00", end="16:30")
    with pytest.raises(cs.DuplicateBookingError) as refused:
        cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                                 interview_round="L1", interview_calendar_uid="evt-A",
                                 interview_booking_source="ai_auto_booked")
    assert refused.value.existing["id"] == hand["candidate"]["id"]


# ── what the screens are told ────────────────────────────────────────────────

def test_only_one_interview_held_twice_is_flagged(client):
    person = _profile()
    first = cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                                     interview_company="Example Client")
    # Written past the guard, as the 9 Oct pair was, to stand for a historical duplicate.
    twin = cs._duplicate_candidate_slot(cs.get_candidate(first["id"]), date=DAY, time="16:00", time_end="16:30",
                                        interview_company="Example Client")
    cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                             interview_company="Other Client")

    rows = cs.annotate_duplicate_bookings([dict(r) for r in _standing()])
    flagged = {r["id"]: r for r in rows if r.get("duplicate_count")}
    assert set(flagged) == {first["id"], twin["id"]}
    assert flagged[first["id"]]["duplicate_rank"] == 1 and flagged[twin["id"]]["duplicate_of"] == first["id"]

    public = cs.public_booked_interview_slots(days=10 ** 5)["slots"]
    assert sorted(s.get("duplicate_count", 0) for s in public if s["date"] == DAY) == [0, 2, 2]


# ── the lock ─────────────────────────────────────────────────────────────────

def test_the_booking_lock_nests_and_holds_other_threads_out():
    order = []
    with cs.BOOKING_LOCK:
        with cs.BOOKING_LOCK:            # the booking paths nest
            order.append("outer-held")
        t = threading.Thread(target=lambda: (cs.BOOKING_LOCK.__enter__(), order.append("other"), cs.BOOKING_LOCK.__exit__(None, None, None)))
        t.start()
        time.sleep(0.2)
        order.append("released")
    t.join(timeout=5)
    assert order == ["outer-held", "released", "other"]


# ── edits: the path that put the 12 Oct pair on one interview ────────────────

def test_the_12_oct_pair_cannot_happen_again(client):
    """The first booking was stored on 10 Dec (the invite's 12/10 read the wrong
    way round); the corrected re-submission booked 12 Oct; then the first was
    edited to 12 Oct, onto the booking already there. The edit is refused."""
    person = _profile()
    misdated = cs.assign_interview_slot(candidate_id=person["id"], date="2099-12-10", time="16:00", time_end="16:30",
                                        interview_round="L1", interview_company="Example Client")
    corrected = cs.assign_interview_slot(candidate_id=person["id"], date="2099-10-12", time="16:00", time_end="16:30",
                                         interview_round="L1", interview_company="Example Client")
    with pytest.raises(cs.DuplicateBookingError) as refused:
        cs.update_candidate(misdated["id"], {"date": "2099-10-12"})

    assert refused.value.existing["id"] == corrected["id"]
    assert "cancel one of them instead" in str(refused.value)
    assert cs.get_candidate(misdated["id"])["date"] == "2099-12-10"     # nothing changed


def test_daily_ops_cannot_move_a_booking_onto_the_same_interview_but_can_reschedule(client):
    person = _profile()
    first = _daily_ops(client, person["id"], time_="14:15", end="14:45")["candidate"]
    other = _daily_ops(client, person["id"], time_="17:00", end="17:30")["candidate"]

    onto_it = client.patch(f"/candidates/interviews/slots/{other['id']}", json={
        "date": DAY, "time": "14:15", "time_end": "14:45", "interview_round": "L1", "technology": "ServiceNow",
    }).json()
    elsewhere = client.patch(f"/candidates/interviews/slots/{other['id']}", json={
        "date": DAY, "time": "18:00", "time_end": "18:30", "interview_round": "L1", "technology": "ServiceNow",
    }).json()

    assert onto_it["status"] == "error" and "already holds this interview" in onto_it["message"]
    assert elsewhere["status"] == "ok"
    assert cs.get_candidate(other["id"])["time"] == "18:00"
    assert cs.get_candidate(first["id"])["time"] == "14:15"


def test_moving_a_booking_onto_a_different_interview_at_that_hour_is_allowed(client):
    person = _profile()
    cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="11:00", time_end="11:30",
                             interview_company="First Client")
    second = cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="15:00", time_end="15:30",
                                      interview_company="Second Client")
    moved = cs.update_candidate(second["id"], {"time": "11:00", "time_end": "11:30"}, allow_slot_without_rules=True)
    assert moved["time"] == "11:00"


# ── the approval-gated correction for duplicates already stored ──────────────

def _tool():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "pending_corrections", Path(__file__).resolve().parent.parent / "scripts" / "pending_corrections.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_dup_item_keeps_the_copy_with_an_outcome_and_cancels_the_other_keeping_both(client, capsys):
    person = _profile()
    first = cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30",
                                     interview_company="Example Client")
    twin = cs._duplicate_candidate_slot(cs.get_candidate(first["id"]), date=DAY, time="16:00", time_end="16:30",
                                        interview_company="Example Client")
    cs.set_interview_attendance(twin["id"], status="attended", remark="Went well", by="test", allow_future=True)
    tool = _tool()

    assert tool.main(["--item", "DUP"]) == 0
    out = capsys.readouterr().out
    assert "item DUP: 1 change(s)" in out
    assert cs.get_candidate(first["id"])["interview_attendance_status"] in ("", None)   # dry run wrote nothing

    assert tool.main(["--item", "DUP", "--apply", "--confirm", "1"]) == 0
    kept, cancelled = cs.get_candidate(twin["id"]), cs.get_candidate(first["id"])
    assert kept["interview_attendance_status"] == "attended"
    assert cancelled["interview_attendance_status"] == "cancelled"
    assert "Duplicate booking of the same interview" in cancelled["interview_attendance_remark"]
    assert cancelled["date"] == DAY and cancelled["time"] == "16:00"        # the record and its history stay
    capsys.readouterr()
    assert tool.main(["--item", "DUP"]) == 0
    assert "item DUP: 0 change(s)" in capsys.readouterr().out


def test_the_dup_item_leaves_two_outcomes_to_the_owner(client, capsys):
    person = _profile()
    first = cs.assign_interview_slot(candidate_id=person["id"], date=DAY, time="16:00", time_end="16:30")
    twin = cs._duplicate_candidate_slot(cs.get_candidate(first["id"]), date=DAY, time="16:00", time_end="16:30")
    cs.set_interview_attendance(first["id"], status="attended", remark="a", by="test", allow_future=True)
    cs.set_interview_attendance(twin["id"], status="not_attended", remark="b", by="test", allow_future=True)

    assert _tool().main(["--item", "DUP"]) == 0
    out = capsys.readouterr().out
    assert "left for your decision" in out and "item DUP: 0 change(s)" in out
