"""Data Room -> Offers: every page of every offer letter, and an honest count of them.

On 19 Oct four of the five offer letters were found to be one page. They were stored by the WhatsApp import of
5 Oct, which kept a photo of the letter's first page (`source_photo`) in place of the PDF it names (`source_file`);
the fifth is a genuine four-page PDF that needs a password to open. Nothing was lost on upload, in storage or on
download -- the stored, served and downloaded bytes were identical -- but nothing said so either.

Each offer now carries what its stored file holds, read from the file itself: its pages, whether it needs a
password, and whether it is that photo of one page (with the name of the original). Real PDFs, made here with
PyMuPDF (as production has it), through the real routes.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import data_room
from features import data_room_credentials_store as creds
from features import offer_letter_extract

pymupdf = pytest.importorskip("pymupdf")


def text_pdf(pages: int, *, password: str = "") -> bytes:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Example Technologies Limited - offer letter, page {n} of {pages}")
        if n > 1:
            page.insert_text((72, 100), f"Annexure {n - 1}: invented terms for a test.")
    if password:
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")
    return doc.tobytes()


def photo_pdf() -> bytes:
    """What the import stored: one page holding one photo, and no text."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.clear_with(200)
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), stream=pix.tobytes("png"))
    return doc.tobytes()


def pages_of(data: bytes, password: str = "") -> int:
    doc = pymupdf.open(stream=data, filetype="pdf")
    if password:
        assert doc.authenticate(password)
    return doc.page_count


FIVE = text_pdf(5)
LOCKED = text_pdf(4, password="invented-pass")
PHOTO = photo_pdf()
ONE = text_pdf(1)

ROWS = [
    {"id": "offer_five", "filename": "Five_Page_Offer.pdf", "candidate": "Asha Example", "has_pdf": True},
    {"id": "offer_locked", "filename": "Locked_Offer.pdf", "candidate": "Meera Example", "has_pdf": True,
     "review_flag": "needs manual/decryption review"},
    {"id": "offer_photo", "filename": "00000001-PHOTO-2026-06-04.pdf", "candidate": "Ravi Example", "has_pdf": True,
     "source_archive": "Example WhatsApp archive", "source_file": "00000002-OfferLetter.pdf",
     "source_photo": "00000001-PHOTO-2026-06-04.jpg", "import_batch": "whatsapp-import-example"},
    {"id": "offer_one", "filename": "One_Page_Offer.pdf", "candidate": "Kiran Example", "has_pdf": True},
    {"id": "offer_scan", "filename": "Scanned.pdf", "candidate": "Scan Example", "has_pdf": True},
    {"id": "offer_none", "filename": "Missing.pdf", "candidate": "No File", "has_pdf": False},
]
FILES = {"offer_five": FIVE, "offer_locked": LOCKED, "offer_photo": PHOTO, "offer_one": ONE, "offer_scan": PHOTO}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    base = tmp_path / "data_room"
    cache = base / "offer_letters_cache"
    cache.mkdir(parents=True)
    (base / "credentials.json").write_text(json.dumps({"offer_letters": [dict(r) for r in ROWS],
                                                        "offer_letters_rows_migrated_v1": True}), encoding="utf-8")
    for item_id, data in FILES.items():
        (cache / f"{item_id}.pdf").write_bytes(data)
    monkeypatch.setattr(creds, "_FILE", str(base / "credentials.json"))
    monkeypatch.setattr(creds, "_OFFER_CACHE_DIR", str(cache))
    monkeypatch.setattr(creds, "_OFFER_FACTS", {})
    return base


@pytest.fixture()
def client(store, monkeypatch):
    from core import dashboard_access

    monkeypatch.setattr(data_room, "_data_room_admin_only", lambda request: True)
    monkeypatch.setattr(dashboard_access, "require_fleet_admin", lambda request: {"username": "admin", "role": "admin"})
    app = FastAPI()
    app.include_router(data_room.router)
    return TestClient(app)


def listed() -> dict:
    return {r["id"]: r for r in creds.get_credentials()["offer_letters"]}


class TestWhatEachFileHolds:
    def test_every_letter_says_how_many_pages_it_has(self, store):
        files = {item_id: row["file"] for item_id, row in listed().items()}
        assert {k: v.get("pages") for k, v in files.items()} == {
            "offer_five": 5, "offer_locked": 4, "offer_photo": 1, "offer_one": 1, "offer_scan": 1, "offer_none": None}
        assert files["offer_none"] == {"stored": False}

    def test_a_password_protected_pdf_is_counted_and_said_to_need_its_password(self, store):
        locked = listed()["offer_locked"]["file"]
        assert locked["pages"] == 4 and locked["password_protected"] is True
        assert listed()["offer_five"]["file"]["password_protected"] is False

    def test_the_photo_kept_by_the_import_is_named_for_what_it_is(self, store):
        photo = listed()["offer_photo"]["file"]
        assert photo["photo_of_one_page"] is True and photo["original_name"] == "00000002-OfferLetter.pdf"

    def test_a_one_page_letter_is_just_a_one_page_letter(self, store):
        # One page of text, and a one-image page that records no photo of its own: neither is a photo import.
        for item_id in ("offer_one", "offer_scan"):
            facts = listed()[item_id]["file"]
            assert facts["pages"] == 1 and facts["photo_of_one_page"] is False and facts["original_name"] == ""

    def test_what_the_file_holds_is_never_saved_with_the_row(self, store):
        listed()
        saved = json.loads((store / "credentials.json").read_text(encoding="utf-8"))["offer_letters"]
        assert all("file" not in row for row in saved)
        assert saved == ROWS


class TestEveryPageArrives:
    @pytest.mark.parametrize("item_id, pages, password", [
        ("offer_five", 5, ""), ("offer_locked", 4, "invented-pass"), ("offer_photo", 1, ""), ("offer_one", 1, ""),
    ])
    def test_preview_and_download_are_the_stored_file_with_all_its_pages(self, client, item_id, pages, password):
        for verb in ("preview", "download"):
            res = client.get(f"/data-room/offer-letters/{item_id}/{verb}")
            assert res.status_code == 200 and res.content == FILES[item_id]
            assert pages_of(res.content, password) == pages
            assert res.headers["x-offer-page-count"] == str(pages)

    def test_the_preview_says_when_the_file_needs_its_password(self, client):
        assert client.get("/data-room/offer-letters/offer_locked/preview").headers["x-offer-password-protected"] == "1"
        assert client.get("/data-room/offer-letters/offer_five/preview").headers["x-offer-password-protected"] == "0"

    def test_a_new_multi_page_letter_is_stored_whole(self, client, store, monkeypatch):
        monkeypatch.setattr(offer_letter_extract, "_extract_text_from_pdf", lambda _: "Dear Asha Example,\nEmployment with Example Technologies Limited.")
        body = client.post("/data-room/offer-letters/upload-analyze", files={"file": ("Seven.pdf", text_pdf(7), "application/pdf")}).json()
        new = body["offer_letter"]
        assert new["file"]["pages"] == 7
        assert pages_of((store / "offer_letters_cache" / f"{new['id']}.pdf").read_bytes()) == 7
        assert pages_of(client.get(f"/data-room/offer-letters/{new['id']}/download").content) == 7

    def test_uploading_the_original_over_the_photo_restores_its_pages_and_keeps_the_history(self, client, store):
        res = client.post("/data-room/offer-letters/offer_photo/upload",
                          files={"file": ("00000002-OfferLetter.pdf", FIVE, "application/pdf")})
        row = res.json()["offer_letter"]
        assert row["file"] == {"stored": True, "kind": "pdf", "pages": 5, "password_protected": False,
                               "photo_of_one_page": False, "original_name": ""}
        assert row["filename"] == "00000002-OfferLetter.pdf"
        assert row["replaced_filename"] == "00000001-PHOTO-2026-06-04.pdf"
        for key in ("source_archive", "source_file", "source_photo", "import_batch", "candidate"):
            assert row[key] == ROWS[2][key]                                  # the provenance stays
        assert client.get("/data-room/offer-letters/offer_photo/download").content == FIVE

    def test_a_pdf_that_cannot_be_read_is_reported_rather_than_counted(self, client, store):
        (store / "offer_letters_cache" / "offer_one.pdf").write_bytes(b"%PDF-1.7\n" + b"not really a pdf " * 40)
        assert listed()["offer_one"]["file"]["pages"] is None
        assert client.get("/data-room/offer-letters/offer_one/preview").headers["x-offer-page-count"] == ""
