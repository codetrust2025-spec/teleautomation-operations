"""The company an interview is with, from the booking form to every surface.

The submit-slot form now asks (optionally) for the company. The booking keeps
it on the slot's row as `interview_company` -- the field Daily Ops already
shows and the mail pipeline already fills -- and Confirmed slots shows it
beside the technology. A blank form field never clears a company someone else
recorded, and nothing about the booking itself changes.
"""

from __future__ import annotations

from datetime import date, timedelta

from features import candidate_store as cs

from tests.test_public_slot_booking_flow import _booking, _client, _confirm, _profile_booking, _upload

AHEAD = (date.today() + timedelta(days=7)).isoformat()


def _booked_rows():
    return [r for r in cs.list_candidates(stage="all", month="all") if r.get("slot_confirmed")]


def test_the_form_company_is_kept_with_the_booked_slot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    booking = _booking(_upload(client))
    booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}", "company": "  Capgemini   Technology  Services "})
    assert _confirm(client, booking).status_code == 200

    rows = _booked_rows()
    assert len(rows) == 1
    assert rows[0]["interview_company"] == "Capgemini Technology Services"


def test_confirmed_slots_and_daily_ops_show_the_same_company(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    booking = _booking(_upload(client))
    booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}", "company": "Wipro"})
    assert _confirm(client, booking).status_code == 200

    slots = client.get("/public/slots/booked").json()["slots"]
    slot = next(s for s in slots if s["date"] == AHEAD)
    assert slot["company"] == "Wipro"
    assert slot["technology"] == "ETL"

    roster = cs.interview_monitor(AHEAD, AHEAD)["interviews"]
    assert [r["interview_company"] for r in roster] == ["Wipro"]


def test_a_booking_without_a_company_records_none(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    booking = _booking(_upload(client))
    booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}"})
    assert _confirm(client, booking).status_code == 200

    assert not _booked_rows()[0].get("interview_company")
    slot = next(s for s in client.get("/public/slots/booked").json()["slots"] if s["date"] == AHEAD)
    assert slot["company"] == ""


def test_a_blank_company_never_clears_one_already_recorded(monkeypatch, tmp_path):
    """A retry of the same booking with the field left empty keeps the company."""
    client = _client(monkeypatch, tmp_path)
    booking = _profile_booking(key=f"abilash-{AHEAD}")
    booking.update({"date": AHEAD, "company": "Infosys"})
    assert _confirm(client, booking).status_code == 200
    booking.pop("company")
    assert _confirm(client, booking).status_code == 200

    rows = _booked_rows()
    assert len(rows) == 1
    assert rows[0]["interview_company"] == "Infosys"


def test_the_company_is_one_tidy_line():
    assert cs.normalise_interview_company("  Tata \n Consultancy\tServices ") == "Tata Consultancy Services"
    assert cs.normalise_interview_company(None) == ""
    assert len(cs.normalise_interview_company("x" * 500)) == 120
