"""Data Room -> Offers: every offer letter's own file, for the preview and the download.

The file is served exactly as it was uploaded -- a PDF, or a photo or scan of the letter (PNG, JPEG, WebP) -- typed from
its bytes, never from its name. Inline for the preview, as an attachment for the download, never cached. Driven through
the real routes against a store in a temporary directory.
"""
from __future__ import annotations

import base64
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import data_room
from features import data_room_credentials_store as creds
from features import offer_letter_extract

# Invented letters. The bytes only need to start the way their kind does; nothing here parses them.
PDF = b"%PDF-1.7\n" + b"0" * 2048 + b"\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + bytes(range(200))
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + bytes(range(64))
GIF = b"GIF89a" + bytes(32)

ROWS = [
    {"id": "offer_asha_example", "filename": "Asha_Example_Offer.pdf", "candidate": "Asha Example",
     "company_name": "Example Technologies Private Limited", "size_kb": 3, "has_pdf": True, "notes": "Signed copy",
     "source_photo": "IMG_0001.jpg", "verification": "document-derived claim, not authenticity"},
    {"id": "offer_no_file", "filename": "Missing.pdf", "candidate": "No File", "company_name": "Example Ltd",
     "has_pdf": False},
    {"id": "offer_from_drive", "filename": "Drive.pdf", "candidate": "Drive Letter", "drive_file_id": "drive-file-1"},
]


@pytest.fixture()
def store(tmp_path, monkeypatch):
    base = tmp_path / "data_room"
    cache = base / "offer_letters_cache"
    cache.mkdir(parents=True)
    (base / "credentials.json").write_text(json.dumps({"offer_letters": [dict(r) for r in ROWS],
                                                        "offer_letters_rows_migrated_v1": True}), encoding="utf-8")
    (cache / "offer_asha_example.pdf").write_bytes(PDF)        # stored the way production stores its five
    monkeypatch.setattr(creds, "_FILE", str(base / "credentials.json"))
    monkeypatch.setattr(creds, "_OFFER_CACHE_DIR", str(cache))
    return cache


@pytest.fixture()
def api(store, monkeypatch):
    from core import dashboard_access

    state = {"signed_in": True}
    monkeypatch.setattr(data_room, "_data_room_admin_only", lambda request: state["signed_in"])
    monkeypatch.setattr(dashboard_access, "require_fleet_admin", lambda request: {"username": "admin", "role": "admin"})
    app = FastAPI()
    app.include_router(data_room.router)
    return TestClient(app), state


def row(item_id: str) -> dict:
    return creds.find_offer_letter(item_id) or {}


class TestWhatAFileIs:
    @pytest.mark.parametrize("data, kind", [
        (PDF, ("pdf", "application/pdf")), (PNG, ("png", "image/png")),
        (JPEG, ("jpg", "image/jpeg")), (WEBP, ("webp", "image/webp")),
    ])
    def test_judged_from_its_bytes(self, data, kind):
        assert creds.offer_file_kind(data) == kind

    @pytest.mark.parametrize("data", [GIF, b"<html>not a letter</html>", b"", b"RIFF\x00\x00\x00\x00WAVE"])
    def test_anything_else_is_not_an_offer_letter(self, data):
        assert creds.offer_file_kind(data) is None


class TestThePreview:
    def test_an_existing_pdf_is_served_exactly_inline_and_never_cached(self, api):
        client, _ = api
        res = client.get("/data-room/offer-letters/offer_asha_example/preview")
        assert res.status_code == 200 and res.content == PDF
        assert res.headers["content-type"] == "application/pdf"
        assert res.headers["content-disposition"].startswith('inline; filename="Asha_Example_Offer.pdf"')
        assert res.headers["cache-control"] == "private, no-store"
        assert res.headers["x-content-type-options"] == "nosniff"

    def test_the_download_is_the_same_file_as_an_attachment(self, api):
        client, _ = api
        res = client.get("/data-room/offer-letters/offer_asha_example/download")
        assert res.content == PDF and res.headers["content-disposition"].startswith('attachment; filename="Asha_Example_Offer.pdf"')

    def test_a_letter_with_no_file_says_so(self, api):
        client, _ = api
        res = client.get("/data-room/offer-letters/offer_no_file/preview")
        assert res.status_code == 404 and "upload" in res.json()["detail"].lower()
        assert client.get("/data-room/offer-letters/nobody/preview").status_code == 404

    def test_a_letter_known_by_its_drive_id_is_fetched_once_then_served(self, api, monkeypatch, store):
        client, _ = api
        fetched = []
        monkeypatch.setattr(creds, "_fetch_url_bytes", lambda url, **kw: fetched.append(url) or PDF)
        assert client.get("/data-room/offer-letters/offer_from_drive/preview").content == PDF
        assert client.get("/data-room/offer-letters/offer_from_drive/preview").content == PDF
        assert len(fetched) == 1 and (store / "offer_from_drive.pdf").read_bytes() == PDF

    def test_only_signed_in_operators(self, api):
        client, state = api
        state["signed_in"] = False
        for verb in ("preview", "download"):
            res = client.get(f"/data-room/offer-letters/offer_asha_example/{verb}")
            assert res.status_code == 403 and PDF not in res.content


class TestImages:
    @pytest.mark.parametrize("data, media, ext", [(PNG, "image/png", "png"), (JPEG, "image/jpeg", "jpg"), (WEBP, "image/webp", "webp")],
                             ids=["png", "jpeg", "webp"])
    def test_an_image_replaces_the_pdf_and_is_served_exactly(self, api, store, data, media, ext):
        client, _ = api
        res = client.post("/data-room/offer-letters/offer_asha_example/upload", files={"file": (f"letter.{ext}", data, "application/octet-stream")})
        assert res.json()["status"] == "ok"
        preview = client.get("/data-room/offer-letters/offer_asha_example/preview")
        assert preview.content == data and preview.headers["content-type"] == media
        assert sorted(os.listdir(store)) == [f"offer_asha_example.{ext}"]          # the PDF it replaced is gone
        saved = row("offer_asha_example")
        assert saved["file_type"] == "image" and saved["has_pdf"] is True
        # the letter takes the new file's name and remembers the one it replaced; everything else is untouched
        assert saved["filename"] == f"letter.{ext}" and saved["replaced_filename"] == "Asha_Example_Offer.pdf"
        for key in ("candidate", "company_name", "notes", "source_photo", "verification"):
            assert saved[key] == ROWS[0][key]
        # the download is the new file, named for it
        download = client.get("/data-room/offer-letters/offer_asha_example/download")
        assert download.content == data
        assert f'filename="letter.{ext}"' in download.headers["content-disposition"]

    def test_a_pdf_can_replace_an_image_again(self, api, store):
        client, _ = api
        client.post("/data-room/offer-letters/offer_asha_example/upload", files={"file": ("a.png", PNG, "image/png")})
        client.post("/data-room/offer-letters/offer_asha_example/upload", files={"file": ("a.pdf", PDF, "application/pdf")})
        assert sorted(os.listdir(store)) == ["offer_asha_example.pdf"]
        assert row("offer_asha_example")["file_type"] == "pdf"
        assert client.get("/data-room/offer-letters/offer_asha_example/preview").headers["content-type"] == "application/pdf"

    def test_anything_else_is_refused_and_the_stored_file_kept(self, api, store):
        client, _ = api
        res = client.post("/data-room/offer-letters/offer_asha_example/upload", files={"file": ("a.gif", GIF, "image/gif")})
        assert res.json()["status"] == "error" and "PDF, PNG, JPG or WebP" in res.json()["message"]
        assert (store / "offer_asha_example.pdf").read_bytes() == PDF and sorted(os.listdir(store)) == ["offer_asha_example.pdf"]

    def test_a_new_offer_from_a_photo_is_read_by_ocr_and_kept_as_the_photo(self, api, store, monkeypatch):
        client, _ = api
        seen = []
        monkeypatch.setattr(offer_letter_extract, "_ocr_text_from_image_base64",
                            lambda b64: seen.append(base64.b64decode(b64)) or "Dear Ravi Example,\nEmployment with Example Solutions Limited.")
        monkeypatch.setattr(offer_letter_extract, "_extract_text_from_pdf", lambda _: pytest.fail("an image is not a PDF"))
        res = client.post("/data-room/offer-letters/upload-analyze", files={"file": ("Ravi Offer.jpg", JPEG, "image/jpeg")})
        body = res.json()
        assert body["status"] == "ok" and seen == [JPEG]                          # OCR read the photo itself
        new = body["offer_letter"]
        assert new["candidate"] == "Ravi Example" and new["company_name"] == "Example Solutions Limited"
        assert new["file_type"] == "image" and new["filename"] == "Ravi Offer.jpg"
        assert (store / f"{new['id']}.jpg").read_bytes() == JPEG
        assert client.get(f"/data-room/offer-letters/{new['id']}/preview").content == JPEG

    def test_a_new_offer_from_a_pdf_still_works(self, api, store, monkeypatch):
        client, _ = api
        monkeypatch.setattr(offer_letter_extract, "_extract_text_from_pdf",
                            lambda _: "Dear Meera Example,\nWe offer you employment with Example Consulting Limited as an analyst.")
        body = client.post("/data-room/offer-letters/upload-analyze", files={"file": ("Meera.pdf", PDF, "application/pdf")}).json()
        assert body["status"] == "ok" and body["offer_letter"]["file_type"] == "pdf"
        assert (store / f"{body['offer_letter']['id']}.pdf").read_bytes() == PDF

    def test_a_new_offer_that_is_neither_is_refused(self, api, store):
        client, _ = api
        body = client.post("/data-room/offer-letters/upload-analyze", files={"file": ("x.gif", GIF, "image/gif")}).json()
        assert body["status"] == "error" and len(creds.get_credentials()["offer_letters"]) == len(ROWS)


class TestNamesAndDeletion:
    def test_a_name_outside_latin1_cannot_break_the_header(self, api):
        client, _ = api
        creds.update_vault_item("offer_letters", "offer_asha_example", {"filename": "Offer – Asha అ.pdf"})
        res = client.get("/data-room/offer-letters/offer_asha_example/download")
        assert res.status_code == 200 and res.content == PDF
        disposition = res.headers["content-disposition"]
        assert 'filename="Offer  Asha .pdf"' in disposition
        assert "filename*=UTF-8''Offer%20%E2%80%93%20Asha%20%E0%B0%85.pdf" in disposition

    def test_deleting_an_offer_removes_its_file_whatever_its_kind(self, api, store):
        client, _ = api
        client.post("/data-room/offer-letters/offer_asha_example/upload", files={"file": ("a.webp", WEBP, "image/webp")})
        creds.delete_vault_item("offer_letters", "offer_asha_example")
        assert os.listdir(store) == []
