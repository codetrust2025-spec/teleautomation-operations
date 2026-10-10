"""The company an interview is with, from the booking form to every surface.

The submit-slot form asks for the company. It is read from the invite when the
invite names it and typed when it does not, so a booking is refused without one:
a blank company is the form not having done either. The booking keeps it on the
slot's row as `interview_company` -- the field Daily Ops already shows and the
mail pipeline already fills -- and Confirmed slots shows it beside the
technology. A refusal books nothing and spends no payment.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

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


REFUSAL = "Company is required. Enter the company this interview is with."


@pytest.mark.parametrize("sent", [None, "", "   ", "\n\t "], ids=["absent", "empty", "spaces", "whitespace"])
def test_a_booking_without_a_company_is_refused_and_books_nothing(monkeypatch, tmp_path, sent):
    """The rule: no company, no booking. Nothing is created and the payment is not spent."""
    client = _client(monkeypatch, tmp_path)
    proof_id = _upload(client)
    booking = _booking(proof_id)
    booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}"})
    if sent is None:
        booking.pop("company")
    else:
        booking["company"] = sent

    refused = _confirm(client, booking)
    assert refused.status_code == 400
    assert refused.json()["message"] == REFUSAL
    assert cs.list_candidates(stage="all", month="all") == []
    assert client.get("/public/slots/booked").json()["slots"] == []

    # The same payment books the moment the company is given: the refusal spent nothing.
    booking["company"] = "Capgemini"
    assert _confirm(client, booking).status_code == 200
    assert [r["interview_company"] for r in _booked_rows()] == ["Capgemini"]


def test_a_profile_service_booking_needs_one_too(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    booking = _profile_booking(key=f"abilash-{AHEAD}")
    booking.update({"date": AHEAD})
    booking.pop("company")
    refused = _confirm(client, booking)
    assert refused.status_code == 400 and refused.json()["message"] == REFUSAL
    assert not _booked_rows()


def test_the_other_required_fields_are_still_reported_first(monkeypatch, tmp_path):
    """The company is checked after the round, so a form missing both is told about the round."""
    client = _client(monkeypatch, tmp_path)
    booking = _profile_booking(key=f"abilash-{AHEAD}")
    booking.update({"date": AHEAD, "interview_round": ""})
    booking.pop("company")
    refused = _confirm(client, booking)
    assert refused.status_code == 400
    assert "Interview round is required" in refused.json()["message"]


def test_retrying_the_same_booking_keeps_its_company_and_books_once(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    booking = _profile_booking(key=f"abilash-{AHEAD}")
    booking.update({"date": AHEAD, "company": "Infosys"})
    assert _confirm(client, booking).status_code == 200
    assert _confirm(client, booking).status_code == 200

    rows = _booked_rows()
    assert len(rows) == 1
    assert rows[0]["interview_company"] == "Infosys"


def test_the_company_is_one_tidy_line():
    assert cs.normalise_interview_company("  Tata \n Consultancy\tServices ") == "Tata Consultancy Services"
    assert cs.normalise_interview_company(None) == ""
    assert len(cs.normalise_interview_company("x" * 500)) == 120
