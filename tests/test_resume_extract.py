from features import ollama_resume_extract as resume_extract


OCR_RESUME_TEXT = """MEHTA BHASKAR Email: mehtabhaskar@example.com
Mobile: +91 9000000104
Bangalore, Karnataka, India

PROFESSIONAL EXPERIENCE
Java Full Stack Developer with 1.3 years of experience using Java, Spring Boot,
React.js, REST APIs, SQL, PostgreSQL, Git, and Postman.
"""


def test_ocr_name_uses_text_before_contact_label():
    assert resume_extract._extract_name_from_text(OCR_RESUME_TEXT) == "Mehta Bhaskar"


def test_contact_area_email_wins_over_noisy_full_page_email():
    text = """Email: mehta.bhaskar@example.com
MEHTA BHASKAR Email: mehta.bhaskar@example.invalid
Mobile: +91 9000000104
"""

    result = resume_extract._regex_extract_from_text(text)

    assert result["email"] == "mehta.bhaskar@example.com"


def test_scanned_pdf_uses_local_ocr_before_vision(monkeypatch):
    monkeypatch.setattr(resume_extract, "_extract_text_from_pdf", lambda _data: None)
    monkeypatch.setattr(resume_extract, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(resume_extract, "_pdf_first_page_to_image", lambda _data: "image")
    monkeypatch.setattr(
        resume_extract, "_ocr_text_from_image_base64", lambda _image: OCR_RESUME_TEXT
    )
    monkeypatch.setattr(
        resume_extract,
        "_call_vision_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("vision model should not be called when OCR succeeds")
        ),
    )

    result = resume_extract.extract_resume_with_ollama(b"scanned-pdf")

    assert result["candidate_name"] == "Mehta Bhaskar"
    assert result["phone"] == "9000000104"
    assert result["email"] == "mehtabhaskar@example.com"
    assert result["technology"] == "Java Full Stack"
    assert result["extraction_method"] == "tesseract_regex"
