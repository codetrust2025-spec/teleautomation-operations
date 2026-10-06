"""The Data Room credentials store has exactly the sections it supports.

The Interview Data section was removed completely (code, routes and stored
records). These tests pin the store's schema to the sections that remain, so
a section cannot come back by accident, and they check that no application
code refers to the removed one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from features import data_room_credentials_store as creds

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = {"service_accounts", "prompts", "resources", "offer_letters"}
RETURNED_KEYS = {"site_url", "vps_host", "admin", "handlers", *SECTIONS, "updated_at", "count"}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "data_room" / "credentials.json"
    monkeypatch.setattr(creds, "_FILE", str(path))
    return path


def test_an_empty_store_has_only_the_supported_sections(vault):
    assert set(creds._empty()) == {"site_url", "vps_host", "admin", "handlers", *SECTIONS, "updated_at"}
    assert set(creds.get_credentials()) == RETURNED_KEYS


def test_only_the_supported_sections_are_editable():
    assert creds._VAULT_SECTIONS == SECTIONS


@pytest.mark.parametrize("section", ["interview_data", "notes", ""])
def test_any_other_section_is_refused(vault, section):
    got, err = creds.create_vault_item(section, {"id": "x1", "summary": "x"})
    assert got is None and err == "Invalid section"
    assert creds.update_vault_item(section, "x1", {"summary": "y"}) is None
    assert creds.delete_vault_item(section, "x1") is None
    assert not vault.exists() or section not in json.loads(vault.read_text())


def test_a_file_with_an_unknown_list_returns_only_the_supported_sections(vault):
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(json.dumps({"admin": None, "handlers": [], "resources": [{"id": "site", "url": "https://x"}],
                                 "legacy_list": [{"id": "old"}]}))
    got = creds.get_credentials()
    assert set(got) == RETURNED_KEYS
    assert got["resources"] == [{"id": "site", "url": "https://x"}]


def test_no_application_code_refers_to_the_removed_section():
    pattern = re.compile(r"interview_data|Interview\s*Data|InterviewData|dr-interview")
    sources = [
        *ROOT.joinpath("features").rglob("*.py"),
        *ROOT.joinpath("core").rglob("*.py"),
        *(p for p in ROOT.joinpath("dashboard", "src").rglob("*") if p.suffix in {".js", ".jsx", ".css"}
          and ".test." not in p.name),
    ]
    offenders = [str(p.relative_to(ROOT)) for p in sources if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []
