"""A candidate has one current resume, not a pile of re-uploads.

One candidate accumulated eight byte-identical copies of the same PDF over
eighty minutes, plus another person's offer letter, because every upload
appended a version and nothing compared the contents.
"""
from __future__ import annotations

import os

import pytest

from features import candidate_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(candidate_store, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(candidate_store, "RESUMES_DIR", str(tmp_path / "resumes"))
    monkeypatch.setattr(candidate_store, "_load_cache", None, raising=False)
    monkeypatch.setattr(candidate_store, "_load_cache_at", 0.0, raising=False)
    row = candidate_store.create_candidate({"name": "Mehta Bhaskar", "phone": "9000000104"})
    return candidate_store, row["id"]


def upload(store, cid, data, name="Resume (2).pdf"):
    return store.add_resume(cid, data=data, original_name=name, mime_type="application/pdf")


def resumes(store, cid):
    return store.get_candidate(cid).get("resumes") or []


def test_the_same_file_uploaded_again_is_not_a_new_version(store):
    cs, cid = store
    first = upload(cs, cid, b"%PDF-1.4 resume bytes")
    for _ in range(7):
        upload(cs, cid, b"%PDF-1.4 resume bytes")
    kept = resumes(cs, cid)
    assert len(kept) == 1
    assert kept[0]["id"] == first["id"]


def test_re_uploading_returns_the_entry_that_already_exists(store):
    cs, cid = store
    first = upload(cs, cid, b"%PDF-1.4 resume bytes")
    again = upload(cs, cid, b"%PDF-1.4 resume bytes")
    assert again["id"] == first["id"]
    assert again["sha256"] == first["sha256"]


def test_a_new_resume_supersedes_the_previous_one(store):
    cs, cid = store
    old = upload(cs, cid, b"%PDF-1.4 the old resume", name="Old.pdf")
    new = upload(cs, cid, b"%PDF-1.4 the new resume", name="New.pdf")
    kept = resumes(cs, cid)
    assert len(kept) == 1
    assert kept[0]["id"] == new["id"]
    assert kept[0]["original_name"] == "New.pdf"
    assert old["id"] != new["id"]


def test_the_superseded_file_is_removed_from_disk(store):
    cs, cid = store
    old = upload(cs, cid, b"%PDF-1.4 the old resume", name="Old.pdf")
    old_path = os.path.join(cs._resume_dir(cid), old["filename"])
    assert os.path.exists(old_path)
    upload(cs, cid, b"%PDF-1.4 the new resume", name="New.pdf")
    assert not os.path.exists(old_path)


def test_the_current_resume_is_readable_after_a_replacement(store):
    cs, cid = store
    upload(cs, cid, b"%PDF-1.4 the old resume", name="Old.pdf")
    new = upload(cs, cid, b"%PDF-1.4 the new resume", name="New.pdf")
    found = cs.get_resume(cid, new["id"])
    assert found is not None
    path, entry = found
    assert open(path, "rb").read() == b"%PDF-1.4 the new resume"
    assert entry["original_name"] == "New.pdf"


def test_every_stored_resume_records_its_checksum(store):
    cs, cid = store
    entry = upload(cs, cid, b"%PDF-1.4 resume bytes")
    assert len(entry["sha256"]) == 64
    assert resumes(cs, cid)[0]["sha256"] == entry["sha256"]


def test_an_empty_or_oversized_upload_is_still_refused(store):
    cs, cid = store
    with pytest.raises(ValueError):
        upload(cs, cid, b"")
    with pytest.raises(ValueError):
        upload(cs, cid, b"x" * (candidate_store.MAX_RESUME_BYTES + 1))


def test_a_disallowed_file_type_is_still_refused(store):
    cs, cid = store
    with pytest.raises(ValueError):
        cs.add_resume(cid, data=b"binary", original_name="thing.exe",
                      mime_type="application/octet-stream")


def test_an_unknown_candidate_stores_nothing(store):
    cs, _cid = store
    assert upload(cs, "no-such-candidate", b"%PDF-1.4 resume") is None
