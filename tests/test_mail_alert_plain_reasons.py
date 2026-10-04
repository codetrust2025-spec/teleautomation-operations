"""The alert popup says why an alert exists in words a person can use.

Its "Detection reason" was the detection layer's own text, shown verbatim: on
5 Oct 2026, 79 visible alerts read "Authenticated RFC 5545 calendar
invitation with matching organizer and candidate attendee.", and the mail the
reader gave up on showed a bare code, AI_COULD_NOT_READ_THIS_MAIL (12) or
RECRUITMENT_RELEVANCE_UNRESOLVED (4).

The wording is rebuilt on read, the way blocked-booking reasons already are:
the stored text is evidence and is never rewritten, and it stays on the row as
`ai_reason_technical` for whoever is debugging.
"""
from copy import deepcopy

import pytest

from core import recruitment_mail_store as store


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    def forbid():
        raise AssertionError("a read projection must not touch the database")

    monkeypatch.setattr(store, "get_connection", forbid)


def served(**row):
    return store.reconcile_booking_claims([row])[0]


@pytest.mark.parametrize("stored", sorted(store._PLAIN_DETECTION_REASONS))
def test_each_detection_template_reads_in_plain_words(stored):
    row = served(id="n1", ai_reason=stored)
    assert row["ai_reason"] == store._PLAIN_DETECTION_REASONS[stored]
    assert row["ai_reason_technical"] == stored
    for jargon in ("RFC 5545", "Authenticated", "_"):
        assert jargon not in row["ai_reason"], (jargon, row["ai_reason"])


@pytest.mark.parametrize("code", ["OLLAMA_QUEUE_TIMEOUT", "REVERSE_SSH_TUNNEL_UNAVAILABLE", "BOOKING_AMBIGUOUS"])
def test_any_other_bare_code_is_never_shown_as_the_reason(code):
    row = served(id="n1", ai_reason=code)
    assert row["ai_reason"] == store._GENERIC_CODE_REASON
    assert row["ai_reason_technical"] == code


@pytest.mark.parametrize("reason", [
    "The email explicitly confirms an interview for the recipient on 01 Oct 2026 from 4:00 PM to 4:45 PM IST.",
    "Contextual classification",
    "OK",
    "",
])
def test_a_reason_already_in_words_is_left_alone(reason):
    row = served(id="n1", ai_reason=reason)
    assert row["ai_reason"] == reason
    assert "ai_reason_technical" not in row


def test_a_trusted_invite_summary_drops_the_internal_word():
    row = served(id="n1", ai_summary="Trusted calendar invite: HR Interview - Data Engineer")
    assert row["ai_summary"] == "Calendar invitation: HR Interview - Data Engineer"


def test_the_stored_row_is_not_changed_and_reading_twice_is_stable():
    stored = {"id": "n1", "ai_reason": "AI_COULD_NOT_READ_THIS_MAIL",
              "ai_summary": "Trusted calendar invite: L1 Interview"}
    original = deepcopy(stored)
    once = store.reconcile_booking_claims([deepcopy(stored)])[0]
    twice = store.reconcile_booking_claims([deepcopy(once)])[0]
    assert twice == once
    assert stored == original
