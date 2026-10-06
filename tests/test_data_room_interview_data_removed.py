"""Interview Data is no longer a Data Room section.

The tab, its store section and its routes are gone. A credentials file written
while the section existed may still hold an "interview_data" list: nothing
returns it, the vault routes refuse it, and saving any other section carries
it over byte-for-byte rather than dropping it -- removing the feature never
deletes stored records on its own.
"""

from __future__ import annotations

import json

import pytest

from features import data_room_credentials_store as creds

OLD_RECORDS = [
    {"id": "a1", "candidate": "Asha Verma", "category": "interview", "event_date": "2026-06-26",
     "summary": "L1 interview attended", "source_file": "chat.txt"},
    {"id": "a2", "candidate": "Ravi Kumar", "category": "process", "event_date": "2026-06-27",
     "summary": "Profile shared"},
]


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "data_room" / "credentials.json"
    monkeypatch.setattr(creds, "_FILE", str(path))
    return path


def _write_old_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "admin": None,
        "handlers": [],
        "resources": [{"id": "site", "url": "https://x"}],
        "interview_data": OLD_RECORDS,
    }))


def test_credentials_no_longer_carry_the_section(vault):
    assert "interview_data" not in creds.get_credentials()
    _write_old_file(vault)
    got = creds.get_credentials()
    assert "interview_data" not in got
    assert got["resources"] == [{"id": "site", "url": "https://x"}]


def test_the_vault_routes_refuse_the_section(vault):
    _write_old_file(vault)
    got, err = creds.create_vault_item("interview_data", {"id": "a3", "summary": "x"})
    assert got is None and err == "Invalid section"
    assert creds.update_vault_item("interview_data", "a1", {"summary": "changed"}) is None
    assert creds.delete_vault_item("interview_data", "a1") is None
    assert json.loads(vault.read_text())["interview_data"] == OLD_RECORDS


def test_saving_another_section_keeps_the_stored_records(vault):
    _write_old_file(vault)
    got, err = creds.create_vault_item("service_accounts", {"id": "ops_gmail", "label": "Ops Gmail"})
    assert err is None
    assert [r["id"] for r in got["service_accounts"]] == ["ops_gmail"]
    creds.update_vault_item("resources", "site", {"url": "https://y"})
    creds.merge_vault_entries(prompts=[{"id": "p1", "title": "Prompt"}])

    stored = json.loads(vault.read_text())
    assert stored["interview_data"] == OLD_RECORDS
    assert stored["resources"] == [{"id": "site", "url": "https://y"}]
