"""The anonymous slot picker never carries a phone number or an email.

`GET /public/slots/candidates` is open to the internet (the booking page has no
sign-in) and returned every in-progress candidate's full phone number, payment
balance and technology. The page reads none of the contact fields: a booking is
made by candidate id, and a round-wise client types their own number. Names and
numbers below are invented.
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.public_slot_api import install_public_slot_routes
from features import candidate_store as cs

ROWS = [
    {"id": "c1", "name": "Probe Person", "technology": "Java", "phone": "9000000701", "email": "probe@example.test",
     "date": "2026-10-03", "time": "11:00", "service_type": "profile_service", "balance_due": 5000,
     "needs_payment_proof": True, "payment_blocked": False},
]


def client(monkeypatch):
    monkeypatch.setattr(cs, "interview_slot_picker_rows", lambda **_kw: [dict(r) for r in ROWS])
    app = FastAPI()
    install_public_slot_routes(app)
    return TestClient(app)


def test_no_phone_or_email_in_the_public_response(monkeypatch):
    body = client(monkeypatch).get("/public/slots/candidates").json()
    assert body["status"] == "ok" and body["count"] == 1
    row = body["candidates"][0]
    assert "phone" not in row and "email" not in row
    assert "9000000701" not in str(body) and "example.test" not in str(body)


def test_what_the_page_needs_is_still_there(monkeypatch):
    row = client(monkeypatch).get("/public/slots/candidates").json()["candidates"][0]
    for needed in ("id", "name", "technology", "date", "time", "service_type", "balance_due", "needs_payment_proof", "payment_blocked"):
        assert needed in row, needed


def test_the_internal_picker_function_still_returns_the_full_row():
    """Only the anonymous route withholds; internal callers are unchanged."""
    import inspect

    assert '"phone"' in inspect.getsource(cs.interview_slot_picker_rows)


def test_the_booking_page_does_not_read_a_phone_from_the_list():
    import os

    page = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "dashboard", "src", "pages", "SubmitSlotPage.jsx")
    src = open(page, encoding="utf-8").read()
    import re

    # `.phone` of a picker row, as opposed to the round-wise client's own typed number.
    assert not re.search(r"\b(?:c|cand|candidate|selected|picked|row)\.phone\b", src)
