"""Pending Works: "Missing Resume" for Profile service only (11 Oct).

    - a Profile service candidate with no resume stored and openable is listed,
      once, however many slot rows they have;
    - one whose only resume records point to lost files is listed too: the
      check read `resume_count or len(resumes)`, so with no accessible resume it
      fell back to counting the lost records and never listed them;
    - one whose resume sits on another of their slot rows is not;
    - a Round-wise candidate is never listed for a resume, nor counted;
    - the payment-proof tasks are unchanged;
    - the Candidates table's "all required details" tick follows the same rule.

Names and numbers are invented.
"""
from __future__ import annotations

import os

import pytest

from features import candidate_store as cs


@pytest.fixture
def store(monkeypatch, tmp_path):
    data = {"candidates": []}
    monkeypatch.setattr(cs, "_load", lambda *a, **k: data)
    monkeypatch.setattr(cs, "_save", lambda payload: data.update(payload))
    monkeypatch.setattr(cs, "RESUMES_DIR", str(tmp_path))
    return data


def resume(row_id, rid, *, present=True):
    if present:
        folder = os.path.join(cs.RESUMES_DIR, row_id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{rid}.pdf"), "wb") as handle:
            handle.write(b"%PDF " + rid.encode())
    return {"id": rid, "filename": f"{rid}.pdf", "original_name": f"{rid}.pdf", "uploaded_at": "2026-09-01T00:00:00+00:00",
            "url": f"/candidates/{row_id}/resumes/{rid}"}


def row(cid, name, *, service="profile_service", resumes=(), **over):
    base = {"id": cid, "name": name, "phone": "90000006" + cid[-2:].rjust(2, "0"), "service_type": service,
            "stage": "in_progress", "reference": "Owner One", "technology": "Java", "date": "2026-09-01",
            "expected_payment": 20000 if service == "profile_service" else 5000, "payment": 0,
            "resumes": list(resumes), "payment_proofs": []}
    base.update(over)
    return base


def pending(store):
    return cs._pending_works_core([cs._with_computed(r) for r in store["candidates"]])


def resume_tasks(result):
    return sorted(w["candidate_name"] for w in result["works"] if w["kind"] == "missing_resume")


def test_profile_without_a_resume_is_listed_once_however_many_slot_rows(store):
    store["candidates"] += [row(f"a{i}", "Asha Fakename", phone="9000000611") for i in range(3)]
    result = pending(store)
    assert resume_tasks(result) == ["Asha Fakename"]
    assert result["by_kind"]["missing_resume"] == 1


def test_profile_whose_resume_files_are_all_lost_is_listed(store):
    store["candidates"].append(row("b1", "Ravi Kolla", resumes=[resume("b1", f"r{i}", present=False) for i in range(3)]))
    assert resume_tasks(pending(store)) == ["Ravi Kolla"]


def test_profile_with_a_resume_on_another_slot_row_is_not_listed(store):
    store["candidates"] += [row("c1", "Varun Sample", phone="9000000622"),
                            row("c2", "Varun Sample", phone="9000000622", resumes=[resume("c2", "cv")])]
    assert resume_tasks(pending(store)) == []


def test_round_wise_is_never_listed_or_counted_for_a_resume(store):
    store["candidates"] += [row("d1", "Kiran Roundwise", service="round_wise"),
                            row("d2", "Lata Roundwise", service="round_wise", phone="")]
    result = pending(store)
    assert resume_tasks(result) == []
    assert "missing_resume" not in result["by_kind"]
    # Lata is still listed -- for her phone, not a resume; Kiran not at all.
    assert {w["candidate_name"]: w["kind"] for w in result["works"]} == {"Lata Roundwise": "missing_phone"}
    assert result["candidate_count"] == 1


def test_payment_proof_tasks_are_unchanged(store):
    store["candidates"].append(row("e1", "Neha Fakename", payment=10000, resumes=[resume("e1", "cv")]))
    result = pending(store)
    assert [w["kind"] for w in result["works"]] == ["missing_payment_proof"]


def test_the_required_details_tick_follows_the_same_rule(store):
    complete = dict(payment=0)
    round_wise = cs._with_computed(row("f1", "Kiran Roundwise", service="round_wise", **complete))
    assert round_wise["details_complete"] is True
    assert "resume" not in round_wise["completion_missing"]
    lost = cs._with_computed(row("f2", "Ravi Kolla", resumes=[resume("f2", "x", present=False)]))
    assert "resume" in lost["completion_missing"]
    store["candidates"] += [row("g1", "Varun Sample", phone="9000000633"),
                            row("g2", "Varun Sample", phone="9000000633", resumes=[resume("g2", "cv")],
                                updated_at="2026-01-01T00:00:00+00:00")]
    [shown] = [m for m in cs._collapse_profile_candidates([cs._with_computed(r) for r in store["candidates"]])
               if m["name"] == "Varun Sample"]
    assert shown["details_complete"] is True
