"""The invite reader is asked for the company, and what it answers is checked.

The booking form fills the company in from the invite when the invite names it,
and the candidate types it when it does not. For that to be trustworthy the
reader has to be asked for it (it was not), and what comes back has to be a
company: a placeholder, the meeting platform, the candidate's own name, an
address or a sentence must come out as "", because "" is what sends the
candidate to type it. A wrong name nobody notices is worse than an empty box
that is asked for.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import features.ollama_invite_extract as invite_extract
from features.ollama_invite_extract import (
    INVITE_EXTRACTION_PROMPT,
    TEXT_CLEANUP_PROMPT,
    clean_company_name,
    validate_invite_extraction,
)

UPCOMING = (date.today() + timedelta(days=14)).isoformat()


class TestWhatTheReaderIsAsked:
    def test_both_prompts_ask_for_the_company_in_the_schema(self):
        assert '"company": ""' in INVITE_EXTRACTION_PROMPT
        assert '"company": ""' in TEXT_CLEANUP_PROMPT

    def test_the_vision_prompt_says_what_a_company_is_and_is_not(self):
        text = INVITE_EXTRACTION_PROMPT
        assert "company is the organisation the interview is for" in text
        assert "Do NOT put the meeting platform" in text
        assert "keep company empty" in text
        # The vision prompt is filled with str.format, so its braces are doubled and a
        # stray one in the new rules would break every reading.
        assert invite_extract._get_invite_prompt().count('"company": ""') == 1

    def test_the_text_prompt_says_the_same(self):
        assert "company is the organisation the interview is for" in TEXT_CLEANUP_PROMPT
        assert "keep company empty" in TEXT_CLEANUP_PROMPT


class TestWhatCountsAsACompany:
    @pytest.mark.parametrize("given, kept", [
        ("Capgemini", "Capgemini"),
        ("  Capgemini   Technology  Services ", "Capgemini Technology Services"),
        ({"name": "Infosys"}, "Infosys"),
        ({"company": "Wipro"}, "Wipro"),
        ("Wipro Ltd.", "Wipro Ltd."),
        ("'TCS'", "TCS"),
        # A real employer that shares a word with a platform is still an employer.
        ("Microsoft", "Microsoft"),
        ("Google", "Google"),
    ])
    def test_a_plausible_name_is_kept_tidy(self, given, kept):
        assert clean_company_name(given) == kept

    @pytest.mark.parametrize("given", [
        None, "", "   ", 12, [], {}, {"name": ""},
        "N/A", "n/a", "NA", "None", "null", "Unknown", "Not visible", "Not specified", "not mentioned",
        "Company", "Company name", "Client", "Organisation", "TBD", "-", "--",
    ])
    def test_a_placeholder_is_not_a_company(self, given):
        assert clean_company_name(given) == ""

    @pytest.mark.parametrize("given", [
        # What a model writes when the invite names no company, instead of leaving the field empty. "Unknown company"
        # was the live case: the slim prompt returned it for every invite without a company and it was accepted.
        "Unknown company", "Unknown Company Name", "unknown organisation", "Unspecified", "Unnamed organisation", "Undefined",
        "Not specified in the invite", "Not mentioned", "No company mentioned", "No name", "Company not mentioned", "Name not visible",
        "Not Applicable", "None specified", "Hiring company", "Employer", "The company", "Client name", "Organisation name",
    ])
    def test_a_phrase_that_says_there_is_no_company_is_not_a_company(self, given):
        assert clean_company_name(given) == ""

    @pytest.mark.parametrize("given", [
        "Nilkamal Ltd", "Notion Labs", "Nokia", "Norton Lifelock", "Nova Tech", "NoBroker", "Nucleus Software",
        "Unisys", "UNISYS", "Unilever", "Uniphore", "Nagarro", "EY", "Insight Global", "Arrise Solutions India Private",
    ])
    def test_names_that_merely_start_like_a_placeholder_are_kept(self, given):
        assert clean_company_name(given) == given

    @pytest.mark.parametrize("given", [
        "HirePro", "FloCareer", "Zoom", "Microsoft Teams", "Google Meet", "BarRaiser", "Gmail", "WhatsApp", "Skype",
    ])
    def test_the_platform_the_invite_came_through_is_not_a_company(self, given):
        assert clean_company_name(given) == ""

    @pytest.mark.parametrize("given", [
        "recruiter@capgemini.com", "https://capgemini.com/careers", "www.capgemini.com", "1234567890", "!!!",
        "Interview with the hiring manager for the senior java developer role at some company in town",
        "x" * 121,
    ])
    def test_an_address_a_number_or_a_sentence_is_not_a_company(self, given):
        assert clean_company_name(given) == ""

    def test_the_candidates_own_name_is_not_a_company(self):
        assert clean_company_name("Asha Rao", people=("asha rao",)) == ""
        assert clean_company_name("ASHA  RAO", people=("", "Asha Rao")) == ""
        assert clean_company_name("Capgemini", people=("Asha Rao",)) == "Capgemini"


class TestWhatComesBackFromAReading:
    def _read(self, **fields):
        return validate_invite_extraction({
            "interview_date": UPCOMING, "start_time": "10:30 AM", "interview_round": "L1",
            "confidence_score": 90, **fields,
        })

    def test_the_company_the_invite_names_is_returned(self):
        assert self._read(company="Capgemini")["company"] == "Capgemini"

    def test_a_reading_without_one_returns_an_empty_company_not_a_missing_key(self):
        assert self._read()["company"] == ""

    def test_a_placeholder_a_platform_or_the_candidate_comes_back_empty(self):
        assert self._read(company="Not visible")["company"] == ""
        assert self._read(company="HirePro")["company"] == ""
        assert self._read(company="Asha Rao", candidate_name="Asha Rao")["company"] == ""
        assert self._read(company="Asha Rao", client_name="Asha Rao")["company"] == ""

    def test_a_company_is_not_a_required_booking_field_of_the_reading(self):
        """Date, start time and round decide whether the reading is usable; the company is asked of the person."""
        result = self._read()
        assert "company" not in result["missing_fields"]
        assert result["manual_fields_required"] is False

    def test_an_empty_reading_and_the_manual_entry_fallback_carry_the_field(self):
        assert invite_extract._empty_extraction()["company"] == ""
        from core.public_slot_api import _invite_extraction_fallback
        assert _invite_extraction_fallback("too slow")["data"]["company"] == ""


class TestThroughTheWholeReader:
    """The AI-only path production runs in (OCR is switched off there)."""

    def _vision(self, monkeypatch, **fields):
        monkeypatch.delenv("INVITE_EXTRACTION_MODE", raising=False)
        monkeypatch.setenv("OCR_ENABLED", "false")
        monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)
        reply = {
            "interview_date": UPCOMING, "start_time": "10:30 AM", "end_time": "11:15 AM",
            "interview_round": "L1", "confidence_score": 91, "missing_fields": [], "warnings": [],
            "looks_like_interview_invite": True, "is_payment_screenshot": False, **fields,
        }
        monkeypatch.setattr(invite_extract, "call_ollama_vision_model", lambda *_a, **_k: json.dumps(reply))
        return invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")

    def test_the_company_the_model_read_reaches_the_response(self, monkeypatch):
        result = self._vision(monkeypatch, company="Capgemini")
        assert result["extraction_method"] == "ai_only"
        assert result["company"] == "Capgemini"

    def test_a_model_that_found_none_gives_an_empty_company(self, monkeypatch):
        assert self._vision(monkeypatch, company="")["company"] == ""
        assert self._vision(monkeypatch)["company"] == ""

    def test_a_model_that_answers_with_a_placeholder_or_the_platform_gives_an_empty_company(self, monkeypatch):
        assert self._vision(monkeypatch, company="N/A")["company"] == ""
        assert self._vision(monkeypatch, company="Microsoft Teams", meeting_platform="Teams")["company"] == ""

    def test_the_company_never_blocks_an_otherwise_good_reading(self, monkeypatch):
        result = self._vision(monkeypatch, company="")
        assert result["auto_booking_safe"] is True
        assert result["manual_fields_required"] is False

    def test_the_unavailable_and_unreadable_results_carry_the_field(self, monkeypatch):
        monkeypatch.setenv("OCR_ENABLED", "false")
        monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: False)
        assert invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")["company"] == ""
        monkeypatch.setattr(invite_extract, "_is_ollama_available", lambda: True)
        monkeypatch.setattr(invite_extract, "call_ollama_vision_model", lambda *_a, **_k: None)
        assert invite_extract.extract_interview_invite_with_ollama(b"image", "image/jpeg")["company"] == ""
