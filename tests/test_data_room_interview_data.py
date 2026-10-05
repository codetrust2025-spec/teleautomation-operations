"""Interview Data is a Data Room vault section like the others.

It holds dated operational history taken from chats and documents, each with
its source. It goes through the same admin-only vault routes and store
functions as Accounts, Links and Offers, and it is a record of what a source
said: nothing in it reaches the candidate, slot or payment stores.
"""

from __future__ import annotations

import json

import pytest

from features import data_room_credentials_store as creds


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "data_room" / "credentials.json"
    monkeypatch.setattr(creds, "_FILE", str(path))
    return path


def record(rid, **extra):
    row = {
        "id": rid,
        "candidate": "Asha Verma",
        "category": "interview",
        "event_date": "2026-06-26",
        "summary": "L1 interview attended",
        "review_status": "recorded",
        "source_file": "WhatsApp Chat - Example.zip/_chat.txt",
        "source_timestamp": "26/06/26 18:02",
        "source_ref": "line 120",
    }
    row.update(extra)
    return row


def test_an_empty_vault_reports_an_empty_section(vault):
    assert creds.get_credentials()["interview_data"] == []


def test_a_file_written_before_the_section_existed_still_loads(vault):
    vault.parent.mkdir(parents=True)
    vault.write_text(json.dumps({"admin": None, "handlers": [], "resources": [{"id": "site", "url": "https://x"}]}))
    got = creds.get_credentials()
    assert got["interview_data"] == []
    assert got["resources"] == [{"id": "site", "url": "https://x"}]


def test_records_are_created_read_updated_and_deleted(vault):
    got, err = creds.create_vault_item("interview_data", record("a1"))
    assert err is None
    assert [r["id"] for r in got["interview_data"]] == ["a1"]

    got = creds.update_vault_item("interview_data", "a1", {"review_status": "conflict", "review_reason": "times differ"})
    row = got["interview_data"][0]
    assert row["review_status"] == "conflict"
    assert row["review_reason"] == "times differ"
    # The provenance the record was created with survives an edit.
    assert row["source_file"] == "WhatsApp Chat - Example.zip/_chat.txt"
    assert row["source_timestamp"] == "26/06/26 18:02"

    got = creds.delete_vault_item("interview_data", "a1")
    assert got["interview_data"] == []


def test_a_duplicate_id_is_refused_rather_than_overwritten(vault):
    creds.create_vault_item("interview_data", record("a1", summary="first"))
    got, err = creds.create_vault_item("interview_data", record("a1", summary="second"))
    assert got is None
    assert "already exists" in err
    assert creds.get_credentials()["interview_data"][0]["summary"] == "first"


def test_records_are_kept_in_date_order(vault):
    creds.create_vault_item("interview_data", record("late", event_date="2026-09-02"))
    creds.create_vault_item("interview_data", record("undated", event_date=""))
    creds.create_vault_item("interview_data", record("early", event_date="2026-06-11"))
    assert [r["id"] for r in creds.get_credentials()["interview_data"]] == ["undated", "early", "late"]


def test_the_other_sections_are_untouched(vault):
    creds.create_vault_item("resources", {"id": "portal", "title": "Portal", "url": "https://x"})
    creds.create_vault_item("interview_data", record("a1"))
    got = creds.get_credentials()
    assert [r["id"] for r in got["resources"]] == ["portal"]
    assert got["service_accounts"] == [] and got["offer_letters"] == []


def test_an_unknown_section_is_still_refused(vault):
    got, err = creds.create_vault_item("candidates", {"id": "x"})
    assert got is None and err == "Invalid section"
    assert creds.update_vault_item("payments", "x", {"a": 1}) is None
