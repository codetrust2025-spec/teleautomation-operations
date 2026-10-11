"""One active resume per candidate.

11 Oct: the Candidates table showed "2 resumes", "3 resumes", "10 resumes". It
added up the resume records of every slot row of a person -- records whose file
was lost in the 25 Aug server move (38 of 59 in production), and the same file
stored again from another slot row. An upload also deleted the file it
replaced.

    - the person's one resume is the newest whose file is stored, across all
      their slot rows; it alone is shown and counted;
    - uploading the same file again, from any of the person's rows, stores
      nothing new;
    - a different file becomes the resume, and the one it replaces is moved
      aside and kept as history -- never deleted -- and not brought back.

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
    monkeypatch.setattr(cs, "candidate_identity_ids", lambda cid, **k: [cid])
    return data


def stored(row_id, rid, when, content=b"%PDF-1.4 one", present=True):
    if present:
        folder = os.path.join(cs.RESUMES_DIR, row_id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{rid}.pdf"), "wb") as handle:
            handle.write(content)
    return {"id": rid, "filename": f"{rid}.pdf", "original_name": f"{rid}.pdf", "mime_type": "application/pdf",
            "uploaded_at": when, "url": f"/candidates/{row_id}/resumes/{rid}"}


def row(cid, resumes=(), **over):
    base = {"id": cid, "name": "Asha Fakename", "phone": "9000000611", "service_type": "profile_service",
            "stage": "in_progress", "date": "2026-09-01", "payment": 0, "expected_payment": 20000,
            "resumes": list(resumes), "payment_proofs": [], "updated_at": "2026-09-01T00:00:00+00:00"}
    base.update(over)
    return base


def table(store):
    return cs._collapse_profile_candidates([cs._with_computed(r) for r in store["candidates"]])


# --- what is shown -------------------------------------------------------------------

def test_the_newest_stored_resume_across_slot_rows_is_the_one_shown(store):
    store["candidates"] += [
        row("a", [stored("a", "r1", "2026-06-01T00:00:00+00:00", b"%PDF old")]),
        row("b", [stored("b", "r2", "2026-09-01T00:00:00+00:00", b"%PDF new")]),
        row("c", [stored("c", "r3", "2026-10-01T00:00:00+00:00", present=False)]),  # lost in the server move
    ]
    [shown] = table(store)
    assert shown["resume_count"] == 1
    assert shown["active_resume"]["id"] == "r2"
    assert shown["latest_resume"]["id"] == "r2"
    assert shown["resume_history_count"] == 3


def test_records_whose_files_are_all_lost_show_no_resume(store):
    store["candidates"] += [row("a", [stored(f"a", f"r{i}", f"2026-06-0{i}T00:00:00+00:00", present=False) for i in range(1, 9)])]
    [shown] = table(store)
    assert shown["resume_count"] == 0
    assert shown["active_resume"] is None


def test_the_same_file_on_two_slot_rows_is_one_resume(store):
    store["candidates"] += [
        row("a", [stored("a", "r1", "2026-09-01T00:00:00+00:00", b"%PDF same")]),
        row("b", [stored("b", "r2", "2026-09-02T00:00:00+00:00", b"%PDF same")]),
    ]
    [shown] = table(store)
    assert shown["resume_count"] == 1


def test_a_single_row_reports_its_active_resume(store):
    store["candidates"].append(row("a", [stored("a", "r1", "2026-09-01T00:00:00+00:00"),
                                         stored("a", "r2", "2026-08-01T00:00:00+00:00", present=False)]))
    enriched = cs._with_computed(store["candidates"][0])
    assert (enriched["resume_count"], enriched["active_resume"]["id"]) == (1, "r1")


# --- uploading -------------------------------------------------------------------------

def test_a_new_file_replaces_the_resume_and_the_old_one_is_kept_aside(store):
    store["candidates"].append(row("a", [stored("a", "r1", "2026-09-01T00:00:00+00:00", b"%PDF old")]))
    entry = cs.add_resume("a", data=b"%PDF new", original_name="Asha_new.pdf", mime_type="application/pdf")
    current = store["candidates"][0]
    assert [item["id"] for item in current["resumes"]] == [entry["id"]]
    [old] = current["resume_history"]
    assert old["id"] == "r1" and old["superseded_by"] == entry["id"]
    archived = os.path.join(cs.RESUMES_DIR, "a", cs.SUPERSEDED_RESUME_DIR, "r1.pdf")
    assert open(archived, "rb").read() == b"%PDF old"          # kept, not deleted
    assert not os.path.exists(os.path.join(cs.RESUMES_DIR, "a", "r1.pdf"))
    # ...and not brought back as a second resume by the metadata repair.
    assert cs.reconcile_resume_metadata() == 0
    assert [item["id"] for item in current["resumes"]] == [entry["id"]]
    assert table(store)[0]["resume_count"] == 1


def test_the_same_file_uploaded_again_from_another_slot_row_stores_nothing(store):
    store["candidates"] += [
        row("a", [stored("a", "r1", "2026-09-01T00:00:00+00:00", b"%PDF same")]),
        row("b"),
    ]
    entry = cs.add_resume("b", data=b"%PDF same", original_name="again.pdf", mime_type="application/pdf")
    assert entry["id"] == "r1"
    assert store["candidates"][1]["resumes"] == []
    assert not os.path.exists(os.path.join(cs.RESUMES_DIR, "b"))


def test_an_upload_never_deletes_a_file(store):
    store["candidates"].append(row("a", [stored("a", "r1", "2026-09-01T00:00:00+00:00", b"%PDF v1")]))
    cs.add_resume("a", data=b"%PDF v2", original_name="v2.pdf", mime_type="application/pdf")
    cs.add_resume("a", data=b"%PDF v3", original_name="v3.pdf", mime_type="application/pdf")
    files = sorted(
        os.path.relpath(os.path.join(folder, name), cs.RESUMES_DIR)
        for folder, _dirs, names in os.walk(cs.RESUMES_DIR) for name in names
    )
    assert len(files) == 3
    assert len(store["candidates"][0]["resume_history"]) == 2
    assert table(store)[0]["resume_count"] == 1
