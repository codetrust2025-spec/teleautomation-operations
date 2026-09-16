"""The identity rules, wired into the one place events are created.

Deciding correctly is not enough — the decision has to be consulted before a row
is written, in both arrival orders, and it must leave every non-interview path
alone.
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store

UID = "syntheticuid000000000001ab@google.com"


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.executed = []
        self.description = [
            type("D", (), {"name": n})
            for n in ("id", "candidate_id", "interview_date", "interview_time",
                      "recruiter_email", "company_domain", "calendar_uid", "calendar_sequence")
        ]

    def execute(self, sql, params=None):
        self.executed.append((" ".join(str(sql).split()), params))

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._c = cursor

    def cursor(self):
        return self._c

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def row(**changes):
    value = {
        "id": "evt-covering", "candidate_id": "9317567fd2",
        "interview_date": "2026-08-11", "interview_time": "04:15 PM",
        "recruiter_email": "neha@sourcebae.com", "company_domain": "sourcebae.com",
        "calendar_uid": None, "calendar_sequence": None,
    }
    value.update(changes)
    return tuple(value[k] for k in (
        "id", "candidate_id", "interview_date", "interview_time",
        "recruiter_email", "company_domain", "calendar_uid", "calendar_sequence"))


def result(*, classification="interview_confirmed", uid=None, sequence=None,
           date="2026-08-11", time="04:15 PM", email="neha@sourcebae.com"):
    return {
        "classification": classification,
        "calendar_uid": uid, "calendar_sequence": sequence,
        "interview": {"date": date, "time": time},
        "recruiter": {"email": email}, "company": {"domain": "sourcebae.com"},
    }


@pytest.fixture
def stub(monkeypatch):
    def install(rows):
        cursor = FakeCursor(rows)
        monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
        return cursor
    return install


# ── both arrival orders ──────────────────────────────────────────────────────


def test_the_invitation_finds_the_covering_mail_already_recorded(stub):
    """Covering note first (05:12), invitation second (05:13) — the real order."""
    stub([row()])
    found = store.existing_interview_event("9317567fd2", result(uid=UID, sequence=0))
    assert found is not None and found["id"] == "evt-covering"


def test_the_covering_mail_finds_the_invitation_already_recorded(stub):
    """Reverse order must give the same answer."""
    stub([row(id="evt-invitation", calendar_uid=UID, calendar_sequence=0)])
    found = store.existing_interview_event("9317567fd2", result())
    assert found is not None and found["id"] == "evt-invitation"


def test_a_first_interview_is_not_a_duplicate(stub):
    stub([])
    assert store.existing_interview_event("9317567fd2", result(uid=UID)) is None


def test_a_reschedule_is_not_swallowed(stub):
    stub([row(id="evt-invitation", calendar_uid=UID, calendar_sequence=0)])
    moved = result(uid=UID, sequence=1, time="05:00 PM")
    assert store.existing_interview_event("9317567fd2", moved) is None


def test_a_different_employer_at_the_same_slot_is_separate(stub):
    stub([row(recruiter_email="swati.sharma@winwire.com", company_domain="winwire.com")])
    assert store.existing_interview_event("9317567fd2", result()) is None


# ── scope: only interviews ───────────────────────────────────────────────────


@pytest.mark.parametrize("classification", ["offer_received", "joining_confirmed", "needs_review"])
def test_non_interview_classifications_are_left_alone(stub, classification):
    """Offers and their covering notes are a different problem with different
    rules; collapsing them here would be a silent change to a path nobody
    asked about."""
    stub([row()])
    assert store.existing_interview_event("9317567fd2", result(classification=classification)) is None


def test_nothing_to_match_on_means_no_duplicate(stub):
    stub([row()])
    assert store.existing_interview_event("9317567fd2", result(date=None, time=None)) is None


def test_the_lookup_is_scoped_to_the_candidate_and_a_window(stub):
    cursor = stub([])
    store.existing_interview_event("9317567fd2", result(uid=UID))
    sql, params = cursor.executed[-1]
    assert "WHERE candidate_id=%s" in sql
    assert "interval" in sql
    assert params[0] == "9317567fd2"


# ── the guard actually short-circuits creation ───────────────────────────────


def test_create_event_returns_the_existing_row_without_inserting(monkeypatch):
    existing = {"id": "evt-covering", "calendar_uid": None}
    monkeypatch.setattr(store, "existing_interview_event", lambda *a, **k: existing)

    attached = {}
    monkeypatch.setattr(store, "attach_calendar_identity",
                        lambda eid, uid, seq: attached.update(id=eid, uid=uid, seq=seq))

    def explode():
        raise AssertionError("create_event must not open a connection to INSERT a duplicate")

    monkeypatch.setattr(store, "get_connection", explode)

    out = store.create_event("9317567fd2", "msg-2", result(uid=UID, sequence=0),
                             model="m", duration_ms=1)
    assert out is existing
    assert attached == {"id": "evt-covering", "uid": UID, "seq": 0}


def test_the_calendar_identity_is_written_onto_the_earlier_event(stub):
    """So the *next* copy is recognised by UID rather than by schedule."""
    cursor = stub([])
    store.attach_calendar_identity("evt-covering", UID, 0)
    sql, params = cursor.executed[-1]
    assert "UPDATE ai_recruitment_events SET calendar_uid=%s" in sql
    assert "calendar_uid IS NULL" in sql, "must not overwrite an identity already recorded"
    assert params == (UID, 0, "evt-covering")


def test_no_uid_means_nothing_is_written(monkeypatch):
    def explode():
        raise AssertionError("nothing to attach, so no write should happen")

    monkeypatch.setattr(store, "get_connection", explode)
    store.attach_calendar_identity("evt-covering", None, None)
