from __future__ import annotations

from features import offer_letter_extract


def test_extract_offer_letter_fields_from_embedded_text(monkeypatch):
    text = """
    ACME Technologies Private Limited
    Date: 24 July 2026

    Dear Dinesh Kulkarni,

    We are pleased to offer you employment with ACME Technologies Private Limited
    as a QA Analyst.
    Joining Date: 01-08-2026
    Annual CTC: INR 8,50,000
    """
    monkeypatch.setattr(offer_letter_extract, "_extract_text_from_pdf", lambda _: text)

    result = offer_letter_extract.extract_offer_letter_fields(
        b"%PDF-sample", "Sakthivel_ACME_Offer.pdf"
    )

    assert result["filename"] == "Sakthivel_ACME_Offer.pdf"
    assert result["candidate"] == "Dinesh Kulkarni"
    assert result["company_name"] == "ACME Technologies Private Limited"
    assert result["size_kb"] == 1
    assert result["analysis_method"] == "embedded_text"
    assert "Joining date: 01-08-2026" in result["notes"]
    assert "CTC: ₹8,50,000" in result["notes"]


def test_extract_offer_letter_fields_uses_local_ocr(monkeypatch):
    monkeypatch.setattr(offer_letter_extract, "_extract_text_from_pdf", lambda _: None)
    monkeypatch.setattr(offer_letter_extract, "_pdf_first_page_to_image", lambda _: "image")
    monkeypatch.setattr(
        offer_letter_extract,
        "_ocr_text_from_image_base64",
        lambda _: "Dear Ravi Kumar,\nEmployment with Example Solutions Limited.",
    )

    result = offer_letter_extract.extract_offer_letter_fields(
        b"%PDF-scanned", "offer.pdf"
    )

    assert result["candidate"] == "Ravi Kumar"
    assert result["company_name"] == "Example Solutions Limited"
    assert result["analysis_method"] == "local_ocr"


def test_create_offer_letter_saves_pdf_and_catalog(monkeypatch, tmp_path):
    from features import data_room_credentials_store as store
    from features import offer_letter_extract

    monkeypatch.setattr(store, "_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(store, "_OFFER_CACHE_DIR", str(tmp_path / "offer-cache"))
    monkeypatch.setattr(
        offer_letter_extract,
        "extract_offer_letter_fields",
        lambda data, filename: {
            "filename": filename,
            "candidate": "Dinesh",
            "company_name": "Infosys",
            "date_modified": "2026-07-24",
            "size_kb": 1,
            "drive_file_id": "",
            "notes": "",
            "analysis_method": "embedded_text",
            "analysis_confidence": 90,
        },
    )

    row = store.create_offer_letter_from_pdf("Infosys Offer.pdf", b"%PDF-test")

    assert row["id"] == "infosys_offer"
    assert row["candidate"] == "Dinesh"
    assert row["has_pdf"] is True
    assert (tmp_path / "offer-cache" / "infosys_offer.pdf").read_bytes() == b"%PDF-test"
    assert store.find_offer_letter("infosys_offer")["company_name"] == "Infosys"

    store.delete_vault_item("offer_letters", "infosys_offer")
    assert not (tmp_path / "offer-cache" / "infosys_offer.pdf").exists()
