"""A resume fills what is missing, and only that.

    - a blank or unusable field is filled; a valid one is never replaced
    - only an AI reading at >= 70 confidence is trusted; regex/OCR guesses are not
    - a resume with a different name is ignored entirely
    - a phone or email another candidate already holds is never written
    - every filled field is traceable to the resume that supplied it

Names, numbers and addresses are invented (the 90000xxxxx block, example.test).
"""
import pytest

from features import candidate_store as cs
from features import resume_autofill as ra

TECHS = ["Java", "ServiceNow", "Salesforce", "React JS"]


def reading(**over):
    base = {
        "is_resume": True, "extraction_source": "pdf_text_ai", "confidence_score": 88,
        "candidate_name": "Asha Rao", "email": "asha.rao@example.test", "phone": "9000000901",
        "technology": "Java",
    }
    base.update(over)
    return base


def plan(candidate, extraction=None, others=()):
    return ra.plan_autofill(
        candidate, reading() if extraction is None else extraction, others=others,
        normalise_phone=cs.candidate_phone_identity, canonical_technology=cs.canonical_technology,
        known_technologies=TECHS,
    )


GAPS = {"name": "Asha Rao", "email": "", "phone": "", "technology": ""}
COMPLETE = {"name": "Asha Rao", "email": "asha@example.test", "phone": "9000000901", "technology": "ServiceNow"}


# --- what is filled -------------------------------------------------------------

def test_every_missing_field_is_filled_from_a_trusted_reading():
    result = plan(GAPS)
    assert result["fill"] == {"email": "asha.rao@example.test", "phone": "9000000901", "technology": "Java"}
    assert result["refused"] == ""


def test_a_valid_value_is_never_replaced_by_a_different_one():
    assert plan(COMPLETE)["fill"] == {}


def test_only_the_missing_field_is_filled_and_the_rest_are_kept():
    result = plan({**COMPLETE, "email": ""})
    assert result["fill"] == {"email": "asha.rao@example.test"}


def test_a_blank_on_the_resume_never_replaces_anything():
    result = plan(COMPLETE, reading(email="", phone="", technology=""))
    assert result["fill"] == {}


def test_a_blank_on_the_resume_leaves_a_gap_a_gap_with_the_reason():
    result = plan(GAPS, reading(email="", phone="", technology=""))
    assert result["fill"] == {}
    assert result["skipped"]["email"] == "no valid email on the resume"


@pytest.mark.parametrize("bad", ["abc", "no-at-sign.example.test", "x@y", "a b@example.test"])
def test_an_unusable_existing_email_is_replaced(bad):
    assert plan({**COMPLETE, "email": bad})["fill"] == {"email": "asha.rao@example.test"}


def test_an_unusable_existing_phone_is_replaced():
    assert plan({**COMPLETE, "phone": "12345"})["fill"] == {"phone": "9000000901"}


def test_a_placeholder_name_is_replaced():
    result = plan({**COMPLETE, "name": "Unknown"}, reading(candidate_name="Asha Rao"))
    assert result["fill"] == {"name": "Asha Rao"}


@pytest.mark.parametrize("bad", ["not-an-email", "someone@example.com", "x@@example.test"])
def test_an_invalid_or_placeholder_email_on_the_resume_is_never_written(bad):
    assert "email" not in plan(GAPS, reading(email=bad))["fill"]


@pytest.mark.parametrize("bad", ["12345", "0123456789", "5123456789", "98765"])
def test_an_invalid_mobile_on_the_resume_is_never_written(bad):
    assert "phone" not in plan(GAPS, reading(phone=bad))["fill"]


def test_a_phone_written_with_a_country_code_is_accepted_as_a_ten_digit_number():
    assert plan(GAPS, reading(phone="+91 90000 00901"))["fill"]["phone"] == "9000000901"


def test_a_technology_the_roster_does_not_use_is_reported_not_written():
    result = plan(GAPS, reading(technology="Quantum Basket Weaving"))
    assert "technology" not in result["fill"]
    assert "not a technology this roster uses" in result["skipped"]["technology"]


def test_a_spelling_variant_of_a_known_technology_is_accepted():
    assert plan(GAPS, reading(technology="reactjs"))["fill"]["technology"] == "React JS"


def test_an_existing_technology_is_kept_even_if_the_resume_says_another():
    assert "technology" not in plan({**GAPS, "technology": "ServiceNow"}, reading(technology="Java"))["fill"]


# --- what is trusted --------------------------------------------------------------

@pytest.mark.parametrize("source", ["regex_only", "tesseract_regex", "failed", ""])
def test_a_guess_that_is_not_an_ai_reading_applies_nothing(source):
    result = plan(GAPS, reading(extraction_source=source))
    assert result["fill"] == {} and "not an AI model" in result["refused"]


def test_a_low_confidence_reading_applies_nothing():
    result = plan(GAPS, reading(confidence_score=ra.MIN_CONFIDENCE - 1))
    assert result["fill"] == {} and "below" in result["refused"]


def test_the_threshold_itself_is_enough():
    assert plan(GAPS, reading(confidence_score=ra.MIN_CONFIDENCE))["fill"]


def test_a_file_that_is_not_a_resume_applies_nothing():
    assert plan(GAPS, reading(is_resume=False))["fill"] == {}


def test_a_non_numeric_confidence_is_treated_as_none():
    assert plan(GAPS, reading(confidence_score="high"))["fill"] == {}


# --- whose resume it is -------------------------------------------------------------

def test_a_resume_with_a_different_name_is_ignored_entirely():
    result = plan(GAPS, reading(candidate_name="Bhanu Prakash"))
    assert result["fill"] == {} and "does not match" in result["refused"]


@pytest.mark.parametrize("on_resume", ["ASHA RAO", "Rao Asha", "Asha Kumari Rao", "Asha R"])
def test_the_same_person_written_differently_is_accepted(on_resume):
    assert plan(GAPS, reading(candidate_name=on_resume))["fill"]


def test_a_spelling_variant_of_the_name_is_accepted():
    result = plan({**GAPS, "name": "Vekateshwarlu Penugonda"}, reading(candidate_name="Venkateshwarlu Penugonda"))
    assert result["fill"]


def test_a_resume_that_names_nobody_cannot_vouch_for_itself():
    assert plan(GAPS, reading(candidate_name="")) ["fill"] == {}


# --- a resume that is not evidence about this candidate ---------------------------------

@pytest.mark.parametrize("filler", ["xyz@example.test", "abc@example.test", "test@example.test", "yourname@example.test", "example@example.test", "name@example.test"])
def test_a_template_placeholder_address_is_never_written(filler):
    assert "email" not in plan(GAPS, reading(email=filler, phone=""))["fill"]


def test_a_real_looking_address_with_a_common_word_is_still_accepted():
    assert plan(GAPS, reading(email="info.asha@example.test", phone=""))["fill"]["email"] == "info.asha@example.test"


def test_a_resume_with_a_different_phone_than_the_candidates_is_ignored_entirely():
    """A resume that read a template placeholder address and a phone that was not the candidate's."""
    candidate = {"name": "Asha Rao", "email": "", "phone": "9000000902", "technology": ""}
    result = plan(candidate, reading(phone="9000000999", email="asha.rao@example.test"))
    assert result["fill"] == {}
    assert "differs from this candidate's" in result["refused"]


def test_a_resume_with_the_same_phone_in_another_format_is_accepted():
    candidate = {"name": "Asha Rao", "email": "", "phone": "9000000901", "technology": ""}
    assert plan(candidate, reading(phone="+91 90000 00901"))["fill"]["email"] == "asha.rao@example.test"


def test_a_candidate_with_no_phone_can_still_be_filled_from_a_resume_that_has_one():
    assert plan(GAPS, reading())["fill"]["phone"] == "9000000901"


def test_a_resume_with_no_phone_is_not_refused_on_that_account():
    candidate = {"name": "Asha Rao", "email": "", "phone": "9000000902", "technology": ""}
    assert plan(candidate, reading(phone=""))["fill"]["email"] == "asha.rao@example.test"


# --- never merge two people -----------------------------------------------------------

def test_an_email_another_candidate_holds_is_never_written():
    other = {"name": "Someone Else", "email": "asha.rao@example.test", "phone": "9000000950"}
    result = plan(GAPS, others=[other])
    assert "email" not in result["fill"] and "belongs to another candidate" in result["skipped"]["email"]


def test_a_phone_another_candidate_holds_is_never_written():
    other = {"name": "Someone Else", "email": "", "phone": "9000000901"}
    result = plan(GAPS, others=[other])
    assert "phone" not in result["fill"] and "belongs to another candidate" in result["skipped"]["phone"]
    assert result["fill"]["email"] == "asha.rao@example.test", "the rest is still filled"


# --- applying it ---------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    return cs


def add(store, rid, **fields):
    data = store._load()
    row = {"id": rid, "name": "Asha Rao", "phone": "", "email": "", "technology": "", "stage": "in_progress",
           "service_type": "profile_service", "expected_payment": 20000, "payment": 7000,
           "reference": "Test Owner", "date": "2026-09-01", "created_at": "2026-09-01T00:00:00+00:00",
           "updated_at": "2026-09-01T00:00:00+00:00", "resumes": [{"id": "r1", "filename": "r1.pdf"}]}
    row.update(fields)
    data.setdefault("candidates", []).append(row)
    store._save(data)


def test_applying_fills_the_gaps_and_records_where_they_came_from(store):
    add(store, "c1", technology="Java")
    add(store, "other", name="Someone Else", technology="ServiceNow", phone="9000000955", email="else@example.test")
    result = ra.apply_autofill("c1", reading(), resume_id="r1")
    assert set(result["filled"]) == {"email", "phone"}
    row = store.get_candidate("c1")
    assert row["email"] == "asha.rao@example.test" and cs.candidate_phone_identity(row["phone"]) == "9000000901"
    note = next(r for r in store._load()["candidates"] if r["id"] == "c1")["resumes"][0]["autofill"]
    assert note["fields"] == ["email", "phone"] and note["confidence"] == 88


def test_applying_never_touches_a_payment_or_an_existing_value(store):
    add(store, "c1", email="keep@example.test", technology="Java")
    before = store.get_candidate("c1")
    ra.apply_autofill("c1", reading(email="different@example.test", technology="ServiceNow"), resume_id="r1")
    after = store.get_candidate("c1")
    assert after["email"] == "keep@example.test" and after["technology"] == "Java"
    assert after["payment"] == before["payment"] == 7000 and after["expected_payment"] == 20000


def test_a_refused_reading_changes_nothing(store):
    add(store, "c1")
    before = store._load()["candidates"][0].copy()
    result = ra.apply_autofill("c1", reading(extraction_source="regex_only"), resume_id="r1")
    assert result["filled"] == {} and result["refused"]
    after = store._load()["candidates"][0]
    assert {k: after.get(k) for k in ("email", "phone", "technology")} == {k: before.get(k) for k in ("email", "phone", "technology")}
    assert "autofill" not in after["resumes"][0]


def test_the_fill_reaches_the_profiles_other_slot_rows(store):
    add(store, "c1", phone="9000000901")  # phone present, email/technology missing
    add(store, "c1-slot2", phone="9000000901", date="2026-09-08")
    ra.apply_autofill("c1", reading(email="asha.rao@example.test", technology="Java"), resume_id="r1")
    emails = {r["id"]: r.get("email") for r in store._load()["candidates"]}
    assert emails["c1"] == emails["c1-slot2"] == "asha.rao@example.test"


def test_an_unknown_candidate_is_refused_without_raising(store):
    assert ra.apply_autofill("nope", reading())["refused"] == "candidate not found"


def test_a_failure_while_applying_never_raises(store, monkeypatch):
    add(store, "c1")
    monkeypatch.setattr(cs, "update_candidate", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    result = ra.apply_autofill("c1", reading(), resume_id="r1")
    assert result["filled"] == {} and "could not be applied" in result["refused"]


# --- through the real upload route ----------------------------------------------------

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import candidates as candidate_routes  # noqa: E402


@pytest.fixture()
def api(store, tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "RESUMES_DIR", str(tmp_path / "resumes"))
    monkeypatch.setattr("core.dashboard_access.assert_candidate_row_access", lambda request, row: None)
    app = FastAPI()
    app.include_router(candidate_routes.router)
    return TestClient(app)


def upload(api, cid="c1", name="resume.pdf"):
    return api.post(f"/candidates/{cid}/resumes", files={"file": (name, b"%PDF-1.4 distinct " + name.encode(), "application/pdf")})


def read_as(monkeypatch, extraction):
    monkeypatch.setattr("features.ollama_resume_extract.extract_resume_with_ollama", lambda raw, mime: dict(extraction))


def test_uploading_a_resume_fills_the_gaps_and_reports_it(api, store, monkeypatch):
    add(store, "c1", technology="Java", resumes=[])
    add(store, "other", name="Someone Else", technology="ServiceNow", phone="9000000955", email="else@example.test", resumes=[])
    read_as(monkeypatch, reading())
    body = upload(api).json()
    assert body["status"] == "ok"
    assert set(body["autofill"]["filled"]) == {"email", "phone"}
    assert body["candidate"]["email"] == "asha.rao@example.test"


def test_uploading_a_resume_never_replaces_a_valid_value(api, store, monkeypatch):
    add(store, "c1", email="keep@example.test", phone="9000000902", technology="Java", resumes=[])
    read_as(monkeypatch, reading(email="different@example.test", phone="9000000999"))
    body = upload(api).json()
    assert body["autofill"]["filled"] == {}
    assert body["candidate"]["email"] == "keep@example.test"


def test_a_regex_guess_is_reported_but_not_applied(api, store, monkeypatch):
    add(store, "c1", resumes=[])
    read_as(monkeypatch, reading(extraction_source="regex_only"))
    body = upload(api).json()
    assert body["status"] == "ok" and body["autofill"]["filled"] == {}
    assert "not an AI model" in body["autofill"]["refused"]
    assert body["candidate"]["email"] == ""


def test_another_persons_resume_is_filed_but_fills_nothing(api, store, monkeypatch):
    add(store, "c1", resumes=[])
    read_as(monkeypatch, reading(candidate_name="Bhanu Prakash"))
    body = upload(api).json()
    assert body["status"] == "ok" and body["autofill"]["filled"] == {}
    assert body["candidate"]["email"] == ""


def test_a_failure_in_auto_fill_never_fails_the_upload(api, store, monkeypatch):
    add(store, "c1", resumes=[])
    read_as(monkeypatch, reading())
    monkeypatch.setattr(ra, "apply_autofill", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    body = upload(api).json()
    assert body["status"] == "ok" and "autofill" not in body
    assert body["candidate"]["id"] == "c1"


def test_a_file_that_is_not_a_resume_is_still_refused_and_fills_nothing(api, store, monkeypatch):
    add(store, "c1", resumes=[])
    read_as(monkeypatch, reading(is_resume=False))
    body = upload(api).json()
    assert body["status"] == "error"
    assert store.get_candidate("c1")["email"] == ""
