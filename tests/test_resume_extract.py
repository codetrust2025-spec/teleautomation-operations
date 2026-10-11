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


# --- complete, on-the-page contact fields (11 Oct regression) --------------------------
# The model's reading is kept only when each field is complete and written on the
# page; otherwise the plain-text reading's value is used, or the field is left empty.
import json
import os

import pytest

PAGE = """Raghavendrarao Kolla
SeniorFront-EndEngineer|React.js|Next.js|TypeScript|UIDevelopment
Hyderabad,India | raghav.k@example.test | +919000000777 | LinkedIn
ProfessionalSummary
SeniorFront-EndEngineerwith9+yearsofexperiencedesigningresponsivewebapplications using React.js.
"""


def _read_with_model(monkeypatch, text, model_reply):
    monkeypatch.setattr(resume_extract, "_extract_text_from_pdf", lambda _data: text)
    monkeypatch.setattr(resume_extract, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(resume_extract, "_call_text_model", lambda *_a, **_k: json.dumps(model_reply))
    return resume_extract.extract_resume_with_ollama(b"%PDF", "application/pdf")


MODEL = {"candidate_name": "Raghavendrarao Kolla", "phone": "+919000000777", "email": "raghav.k@example.test",
         "technology": "React.js", "confidence_score": 85}


def test_a_complete_reading_is_kept_with_the_phone_as_ten_digits(monkeypatch):
    result = _read_with_model(monkeypatch, PAGE, MODEL)
    assert (result["candidate_name"], result["phone"], result["email"], result["technology"]) == (
        "Raghavendrarao Kolla", "9000000777", "raghav.k@example.test", "React.js")
    assert result["extraction_source"] == "pdf_text_ai"


def test_a_cut_short_name_and_phone_from_the_model_are_replaced_by_the_pages(monkeypatch):
    result = _read_with_model(monkeypatch, PAGE, {**MODEL, "candidate_name": "Kolla Raghava", "phone": "90000"})
    assert result["candidate_name"] == "Raghavendrarao Kolla"
    assert result["phone"] == "9000000777"


def test_a_name_not_on_the_page_is_not_passed_on(monkeypatch):
    result = _read_with_model(monkeypatch, PAGE, {**MODEL, "candidate_name": "Ravi Kumar"})
    assert result["candidate_name"] == "Raghavendrarao Kolla"


def test_a_name_is_kept_as_the_page_writes_it_and_an_incomplete_phone_is_left_empty(monkeypatch):
    page = PAGE.replace("Raghavendrarao Kolla\n", "Raghavendrarao K\n").replace("+919000000777", "+91 90000")
    result = _read_with_model(monkeypatch, page, {**MODEL, "candidate_name": "Raghavendrarao K", "phone": "+91 90000"})
    assert result["candidate_name"] == "Raghavendrarao K"
    assert result["phone"] == ""
    assert result["email"] == "raghav.k@example.test"


def test_an_email_not_on_the_page_is_not_passed_on(monkeypatch):
    result = _read_with_model(monkeypatch, PAGE, {**MODEL, "email": "someone@example.test"})
    assert result["email"] == "raghav.k@example.test"


# The real resume from the 11 Oct report is not kept in the repository (it is a
# candidate's personal document). Point RESUME_REGRESSION_PDF at it, and
# RESUME_REGRESSION_EXPECT at a JSON file with the four expected values, to run
# these against it: the model's reply is replaced by the cut-short values seen
# in the form, and the page must still give the complete ones.
_REAL_PDF = os.environ.get("RESUME_REGRESSION_PDF", "")
_REAL_EXPECT = os.environ.get("RESUME_REGRESSION_EXPECT", "")


@pytest.mark.skipif(not (_REAL_PDF and _REAL_EXPECT), reason="real resume not supplied")
@pytest.mark.parametrize("reply", ["complete", "cut_short"])
def test_the_reported_resume_gives_all_four_fields_complete(monkeypatch, reply):
    from features import candidate_store as cs

    data = open(_REAL_PDF, "rb").read()
    expect = json.load(open(_REAL_EXPECT, encoding="utf-8"))
    text = resume_extract._extract_text_from_pdf(data)
    model = {"candidate_name": expect["name"], "phone": "+91" + expect["phone"], "email": expect["email"],
             "technology": expect["model_technology"], "confidence_score": 85}
    if reply == "cut_short":
        model.update(candidate_name=expect["cut_short_name"], phone=expect["cut_short_phone"])
    result = _read_with_model(monkeypatch, text, model)
    assert result["candidate_name"] == expect["name"]
    assert result["phone"] == expect["phone"]
    assert result["email"] == expect["email"]
    assert cs.canonical_technology(result["technology"]) == expect["technology"]


# --- a one-word name heading and letters spaced out (11 Oct report) --------------------
# The PDF's first line is the name in capitals; the email in its text layer has
# spaces inside it. The reader dropped the one-word name, took the heading below
# for the name, and could not find the email on the page. Invented values.
SPACED = """ASHA
Gmail: Mobile : +
a sha.v59 @ ex ample.te st 91-9000000611
SUMMARY OF QUALIFICATIONS
6 years
Overall, o f experience in web technolog ies with React.js and Java
"""


def test_the_one_word_heading_is_the_name_not_the_section_below_it():
    assert resume_extract._extract_name_from_text(SPACED) == "Asha"
    assert resume_extract._extract_name_from_text("SUMMARY OF QUALIFICATIONS\nAsha Rao\n") == "Asha Rao"


def test_an_email_with_spaced_letters_is_read_from_the_page():
    result = resume_extract._regex_extract_from_text(SPACED)
    assert (result["candidate_name"], result["phone"], result["email"]) == ("Asha", "9000000611", "asha.v59@example.test")


def test_the_models_one_word_name_and_email_are_kept(monkeypatch):
    result = _read_with_model(monkeypatch, SPACED, {**MODEL, "candidate_name": "ASHA", "phone": "9000000611",
                                                    "email": "asha.v59@example.test", "technology": "React JS"})
    assert (result["candidate_name"], result["phone"], result["email"]) == ("Asha", "9000000611", "asha.v59@example.test")


def test_a_section_heading_from_the_model_is_never_the_name(monkeypatch):
    result = _read_with_model(monkeypatch, SPACED, {**MODEL, "candidate_name": "Summary Of Qualifications", "phone": "9000000611"})
    assert result["candidate_name"] == "Asha"
