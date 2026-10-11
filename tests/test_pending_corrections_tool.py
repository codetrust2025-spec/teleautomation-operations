"""The owner-approval corrections tool writes nothing unless told exactly (11 Oct).

    - a dry run lists every change and writes nothing;
    - --apply refuses unless --confirm equals the number of changes planned now;
    - an approved run goes through the store's own functions, one call a change;
    - a resume copy is moved to history (record first, then file), never deleted.

Names and values are invented.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from scripts import pending_corrections as tool


def fake_store(rows, tmp_path):
    calls = []

    def update_candidate(cid, patch):
        calls.append(("update", cid, patch))
        return {"id": cid}

    def patch_row(cid, change):
        row = next(r for r in rows if r["id"] == cid)
        row.update(change(dict(row)))
        calls.append(("patch", cid))
        return row

    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    return SimpleNamespace(
        _load=lambda force=False: {"candidates": rows},
        update_candidate=update_candidate,
        _patch_row_fields=patch_row,
        RESUMES_DIR=str(resumes_dir),
        SUPERSEDED_RESUME_DIR="_superseded",
        _resume_file_present=lambda rid, it: os.path.isfile(os.path.join(str(resumes_dir), rid, it["filename"])),
        _resume_digest=lambda rid, it: it["sha256"],
    ), calls


EMAIL_ARGS = ["--name", "Kiran Fakename", "--from-email", "placeholder@example.com", "--to-email", "kiran.f@example.com"]


@pytest.fixture
def placeholder_rows():
    return [{"id": f"r{i}", "name": "Kiran Fakename", "date": f"2026-09-0{i}", "email": "placeholder@example.com"} for i in range(1, 4)] + \
           [{"id": "r9", "name": "Kiran Fakename", "date": "2026-09-09", "email": "kiran.f@example.com"}]


def test_a_dry_run_lists_the_changes_and_writes_nothing(monkeypatch, tmp_path, placeholder_rows, capsys):
    cs, calls = fake_store(placeholder_rows, tmp_path)
    monkeypatch.setattr(tool, "_cs", lambda: cs)
    assert tool.main(["--item", "F", *EMAIL_ARGS]) == 0
    out = capsys.readouterr().out
    assert out.count("WOULD  Kiran Fakename") == 3 and "item F: 3 change(s)" in out
    assert calls == []


def test_apply_refuses_a_count_that_does_not_match(monkeypatch, tmp_path, placeholder_rows, capsys):
    cs, calls = fake_store(placeholder_rows, tmp_path)
    monkeypatch.setattr(tool, "_cs", lambda: cs)
    assert tool.main(["--item", "F", *EMAIL_ARGS, "--apply", "--confirm", "12"]) == 2
    assert "nothing written" in capsys.readouterr().out
    assert calls == []
    assert tool.main(["--item", "F", *EMAIL_ARGS, "--apply"]) == 2
    assert calls == []


def test_an_approved_run_uses_the_store_once_per_change(monkeypatch, tmp_path, placeholder_rows):
    cs, calls = fake_store(placeholder_rows, tmp_path)
    monkeypatch.setattr(tool, "_cs", lambda: cs)
    assert tool.main(["--item", "F", *EMAIL_ARGS, "--apply", "--confirm", "3"]) == 0
    assert calls == [("update", f"r{i}", {"email": "kiran.f@example.com"}) for i in range(1, 4)]


def test_an_identical_resume_copy_moves_to_history_and_the_newest_stays(monkeypatch, tmp_path):
    rows = [{"id": "a1", "name": "Asha Fakename", "resumes": [
        {"id": "old", "filename": "old.pdf", "sha256": "same", "uploaded_at": "2026-09-01T00:00:00"},
        {"id": "new", "filename": "new.pdf", "sha256": "same", "uploaded_at": "2026-10-01T00:00:00"},
    ], "resume_history": []}]
    cs, _ = fake_store(rows, tmp_path)
    folder = tmp_path / "resumes" / "a1"
    folder.mkdir()
    (folder / "old.pdf").write_bytes(b"%PDF same")
    (folder / "new.pdf").write_bytes(b"%PDF same")
    (folder / "stray.pdf").write_bytes(b"%PDF stray")
    monkeypatch.setattr(tool, "_cs", lambda: cs)
    assert tool.main(["--item", "J", "--apply", "--confirm", "2"]) == 0
    assert [it["id"] for it in rows[0]["resumes"]] == ["new"]
    assert rows[0]["resume_history"][0]["id"] == "old"
    assert rows[0]["resume_history"][0]["archived_file"] == os.path.join("_superseded", "old.pdf")
    assert sorted(os.listdir(folder / "_superseded")) == ["old.pdf", "stray.pdf"]
    assert (folder / "new.pdf").exists() and not (folder / "old.pdf").exists()


def test_the_records_are_named_on_the_command_line_not_in_the_tool(monkeypatch, tmp_path, placeholder_rows):
    cs, calls = fake_store(placeholder_rows, tmp_path)
    monkeypatch.setattr(tool, "_cs", lambda: cs)
    with pytest.raises(SystemExit):
        tool.main(["--item", "F", "--from-email", "placeholder@example.com", "--to-email", "kiran.f@example.com"])
    with pytest.raises(SystemExit):
        tool.main(["--item", "C1", "--name", "Kiran Fakename", "--proof-sha", "abc"])
    assert calls == []
