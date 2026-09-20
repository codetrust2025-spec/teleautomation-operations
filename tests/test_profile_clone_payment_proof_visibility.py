"""A profile candidate's payment screenshot must survive its own clone rows.

A profile-service candidate's interview slots are stored as separate cloned
candidate rows, and the money is recorded on exactly one of them. A screenshot
uploaded against any other clone therefore hangs on a row that shows no
payment. Legacy uploads carry no `attachment_type`, so classification falls
back to asking whether the owning row took money — and for every clone but one
the answer is no.

The consequence in production was a candidate whose Candidates row read
"₹20,000 PAID" with no attachment indicator at all: the screenshot was neither
in `payment_proofs` nor even in the review queue the collapse discarded. It was
reachable nowhere in the API response.

Round-wise rows are never collapsed, so they were never affected; this is why
the failure looked like a PROFILE-WISE-only bug.
"""
from __future__ import annotations

import pytest

from features import candidate_store as cs


@pytest.fixture
def roster(monkeypatch):
    def seed(rows):
        monkeypatch.setattr(cs, "_load", lambda **_: {"candidates": rows})

    return seed


def _screenshot(cid, pid="px1"):
    """An untyped upload, exactly as the pre-typing roster stored it."""
    return {
        "id": pid,
        "filename": f"{pid}.jpg",
        "original_name": "IMG_2291.jpg",
        "mime_type": "image/jpeg",
        "uploaded_at": "2026-08-11T09:12:00+00:00",
        "url": f"/candidates/{cid}/proofs/{pid}",
    }


def _profile_slot(cid, *, date, payment=0, **extra):
    return {
        "id": cid,
        "name": "pujitha",
        "service_type": "profile_service",
        "technology": "Full Stack",
        "reference": "Ravinder",
        "phone": "9652603125",
        "stage": "in_progress",
        "date": date,
        "updated_at": f"{date}T00:00:00+00:00",
        "payment": payment,
        **extra,
    }


def test_screenshot_on_an_unpaid_slot_clone_still_reaches_the_candidates_row(roster):
    """The money is on the 28 Aug slot; the screenshot on the 11 Aug one."""
    roster([
        _profile_slot("slot-28aug", date="2026-08-28", payment=20000, proofs=[]),
        _profile_slot("slot-11aug", date="2026-08-11", proofs=[_screenshot("slot-11aug")]),
    ])

    row = cs.list_candidates(month="2026-08")[0]

    assert row["proof_count"] == 1
    assert row["payment"] == 20000
    assert row["payment_status"] == "paid"
    # The viewer fetches the file from the clone that owns it, not the row.
    proof = row["payment_proofs"][0]
    assert proof["candidate_id"] == "slot-11aug"
    assert proof["url"] == "/candidates/slot-11aug/proofs/px1"


def test_an_attachment_already_parked_by_the_migration_is_reclassified(roster):
    """The one-shot migration judged each clone alone and popped `proofs`.

    Recovery cannot depend on the legacy list, because that list is gone. The
    only surviving copy sits in the review queue, so the queue is where the
    re-reading has to happen.
    """
    parked = {
        **_screenshot("slot-11aug"),
        "legacy_storage": True,
        "review_reason": "legacy_attachment_type_uncertain",
    }
    roster([
        _profile_slot(
            "slot-28aug", date="2026-08-28", payment=20000,
            attachment_schema_version=2, payment_proofs=[],
        ),
        _profile_slot(
            "slot-11aug", date="2026-08-11",
            attachment_schema_version=2, payment_proofs=[],
            attachment_review_queue=[parked],
        ),
    ])

    row = cs.list_candidates(month="2026-08")[0]

    assert row["proof_count"] == 1
    assert row["payment_proofs"][0]["candidate_id"] == "slot-11aug"
    assert row["attachment_review_queue"] == []


def test_round_wise_row_recovers_a_parked_attachment_once_money_is_recorded(roster):
    """Round-wise never collapses, so the row itself must re-read its queue."""
    roster([{
        "id": "rw1",
        "name": "Munni Varma",
        "service_type": "round_wise",
        "technology": "DevOps",
        "stage": "completed",
        "date": "2026-08-12",
        "updated_at": "2026-08-12T00:00:00+00:00",
        "payment": 5000,
        "attachment_schema_version": 2,
        "payment_proofs": [],
        "attachment_review_queue": [
            {**_screenshot("rw1", "q1"), "review_reason": "legacy_attachment_type_uncertain"}
        ],
    }])

    row = cs.list_candidates(month="2026-08")[0]

    assert row["proof_count"] == 1
    assert row["attachment_review_queue"] == []


def test_a_genuinely_ambiguous_attachment_stays_reviewable(roster):
    """No money anywhere in the group means nothing has been established.

    Promoting on a hunch would put an interview screenshot into the payment
    ledger, which is worse than leaving it queued for a human.
    """
    roster([
        _profile_slot("slot-a", date="2026-08-28", proofs=[]),
        _profile_slot("slot-b", date="2026-08-11", proofs=[_screenshot("slot-b")]),
    ])

    row = cs.list_candidates(month="2026-08")[0]

    assert row["proof_count"] == 0
    queued = row["attachment_review_queue"]
    assert [item["id"] for item in queued] == ["px1"]
    assert queued[0]["review_reason"] == "legacy_attachment_type_uncertain"
    assert queued[0]["candidate_id"] == "slot-b"


def test_a_slot_screenshot_is_not_promoted_into_the_payment_ledger(roster):
    """Affirmative slot metadata outranks the group's payment."""
    roster([
        _profile_slot("slot-a", date="2026-08-28", payment=20000, proofs=[]),
        _profile_slot(
            "slot-b", date="2026-08-11",
            proofs=[{**_screenshot("slot-b"), "booking_id": "bk-77"}],
        ),
    ])

    row = cs.list_candidates(month="2026-08")[0]

    assert row["proof_count"] == 0
    assert [item["id"] for item in row["slot_screenshot_proofs"]] == ["px1"]
