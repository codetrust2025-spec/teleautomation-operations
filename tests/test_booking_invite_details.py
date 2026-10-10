"""More of the invite kept with a booking: meeting platform, meeting link and time zone.

The reader fills the booking form only with values it can stand behind: a known platform, a complete link on
a meeting service, a time zone the invite actually writes. The booking keeps them on the booked slot beside
the company, refuses a malformed link, and a blank never clears a value already recorded.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from features import candidate_store as cs
from features.ollama_invite_extract import (
    INVITE_EXTRACTION_PROMPT,
    TEXT_CLEANUP_PROMPT,
    clean_meeting_link,
    clean_meeting_platform,
    clean_written_timezone,
    validate_invite_extraction,
)
from tests.test_public_slot_booking_flow import _booking, _client, _confirm, _profile_booking, _upload

AHEAD = (date.today() + timedelta(days=7)).isoformat()
TEAMS = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_test%40thread.v2/0?context=x"


class TestThePlatform:
    @pytest.mark.parametrize("seen, name", [
        ("Microsoft Teams", "Microsoft Teams"), ("teams", "Microsoft Teams"), ("MS Teams meeting", "Microsoft Teams"),
        ("Google Meet", "Google Meet"), ("Zoom Meeting", "Zoom"), ("HirePro video interview", "HirePro"), ("FloCareer", "FloCareer"),
        ("Webex", "Webex"), ("Telephonic round", "Phone call"), ("Face to face", "In person"),
    ])
    def test_a_known_platform_gets_its_own_name(self, seen, name):
        assert clean_meeting_platform(seen) == name

    @pytest.mark.parametrize("seen", ["", None, "Gmail", "Outlook", "online", "virtual", "https://x.example.com"])
    def test_anything_else_is_left_for_the_candidate(self, seen):
        assert clean_meeting_platform(seen) == ""


class TestTheMeetingLink:
    @pytest.mark.parametrize("link", [TEAMS, "https://us02web.zoom.us/j/81234567890?pwd=abc", "https://meet.google.com/abc-defg-hij",
                                      "https://company.webex.com/meet/someone", "https://app.flocareer.com/interview/123"])
    def test_a_complete_link_on_a_meeting_service_is_kept(self, link):
        assert clean_meeting_link(link) == link

    @pytest.mark.parametrize("link", [
        "https://teams.microsoft.com/l/meetup-join/19%3a...",        # cut off in the screenshot
        "https://teams.microsoft.com/l/meetup-join/19%3a…",
        "https://evil.example.com/j/123",                               # not a meeting service
        "teams.microsoft.com/l/meetup-join/x",                          # not a complete address
        "https://zoom.us/",                                             # no meeting in it
        "Click here to join the meeting", "https://meet.google.com/abc defg", "", None,
    ])
    def test_a_partial_unknown_or_malformed_link_is_not_filled(self, link):
        assert clean_meeting_link(link) == ""


class TestTheTimeZone:
    @pytest.mark.parametrize("seen, label", [("IST", "IST"), ("India Standard Time", "IST"), ("(GMT+05:30) India Standard Time", "IST"),
                                             ("Asia/Kolkata", "IST"), ("EST", "EST"), ("PST", "PST"), ("GMT", "GMT")])
    def test_a_time_zone_the_invite_writes(self, seen, label):
        assert clean_written_timezone(seen) == label

    @pytest.mark.parametrize("seen", ["", None, "Eastern Time", "local time", "morning"])
    def test_none_or_an_unclear_one_is_left_empty(self, seen):
        assert clean_written_timezone(seen) == ""

    def test_the_reader_is_asked_for_the_time_zone_as_written_without_disturbing_the_existing_field(self):
        for prompt in (INVITE_EXTRACTION_PROMPT, TEXT_CLEANUP_PROMPT):
            assert '"timezone_written": ""' in prompt
        # The existing `timezone` (with its default, which the OCR-mode agreement check relies on) is unchanged.
        assert '"timezone": "Asia/Kolkata"' in INVITE_EXTRACTION_PROMPT
        assert "Never assume one" in INVITE_EXTRACTION_PROMPT


class TestWhatAReadingConfirms:
    def read(self, **fields):
        return validate_invite_extraction({"interview_date": "2030-01-05", "start_time": "10:00 AM", "interview_round": "L1",
                                           "confidence_score": 90, **fields})

    def test_confirmed_values_come_only_from_values_that_pass_the_checks(self):
        out = self.read(meeting_platform="Teams", meeting_link=TEAMS, timezone_written="IST")
        assert (out["confirmed_platform"], out["confirmed_meeting_link"], out["confirmed_timezone"]) == ("Microsoft Teams", TEAMS, "IST")

    def test_the_readers_default_time_zone_is_never_a_confirmed_one(self):
        out = self.read(timezone="Asia/Kolkata")          # the schema's example, not something the invite said
        assert out["confirmed_timezone"] == ""

    def test_nothing_confirmed_means_empty_not_missing(self):
        out = self.read()
        assert (out["confirmed_platform"], out["confirmed_meeting_link"], out["confirmed_timezone"]) == ("", "", "")

    def test_the_manual_entry_fallback_carries_the_fields(self):
        from core.public_slot_api import _invite_extraction_fallback
        data = _invite_extraction_fallback("too slow")["data"]
        assert data["confirmed_platform"] == data["confirmed_meeting_link"] == data["confirmed_timezone"] == ""


def _booked():
    return [r for r in cs.list_candidates(stage="all", month="all") if r.get("slot_confirmed")]


class TestTheBookingKeepsThem:
    def test_platform_link_and_time_zone_are_kept_with_the_booked_slot(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        booking = _booking(_upload(client))
        booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}", "meeting_platform": " Microsoft  Teams ",
                        "meeting_link": TEAMS, "timezone": "IST"})
        assert _confirm(client, booking).status_code == 200
        row = _booked()[0]
        assert (row["interview_platform"], row["interview_meeting_link"], row["interview_timezone"]) == ("Microsoft Teams", TEAMS, "IST")
        assert row["interview_company"] == "Capgemini"

    def test_a_booking_without_them_records_none_and_changes_nothing_else(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        booking = _booking(_upload(client))
        booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}"})
        assert _confirm(client, booking).status_code == 200
        row = _booked()[0]
        assert not row.get("interview_platform") and not row.get("interview_meeting_link") and not row.get("interview_timezone")

    @pytest.mark.parametrize("link", ["teams meeting", "www.zoom.us/j/1", "https://bad link.com", "ftp://x.example.com/a"])
    def test_a_malformed_link_is_refused_and_books_nothing(self, monkeypatch, tmp_path, link):
        client = _client(monkeypatch, tmp_path)
        booking = _booking(_upload(client))
        booking.update({"date": AHEAD, "idempotency_key": f"raju-{AHEAD}", "meeting_link": link})
        refused = _confirm(client, booking)
        assert refused.status_code == 400
        assert refused.json()["message"].startswith("Meeting link must be a full web address")
        assert cs.list_candidates(stage="all", month="all") == []

    def test_a_blank_never_clears_a_detail_already_recorded(self, monkeypatch, tmp_path):
        client = _client(monkeypatch, tmp_path)
        booking = _profile_booking(key=f"abilash-{AHEAD}")
        booking.update({"date": AHEAD, "meeting_platform": "Zoom", "meeting_link": "https://us02web.zoom.us/j/1", "timezone": "IST"})
        assert _confirm(client, booking).status_code == 200
        for key in ("meeting_platform", "meeting_link", "timezone"):
            booking.pop(key)
        assert _confirm(client, booking).status_code == 200
        rows = _booked()
        assert len(rows) == 1
        assert (rows[0]["interview_platform"], rows[0]["interview_meeting_link"], rows[0]["interview_timezone"]) == ("Zoom", "https://us02web.zoom.us/j/1", "IST")


def test_the_store_helpers():
    assert cs.normalise_interview_detail("  Microsoft \n Teams ") == "Microsoft Teams"
    assert cs.normalise_meeting_link("") == ("", True)
    assert cs.normalise_meeting_link(TEAMS) == (TEAMS, True)
    assert cs.normalise_meeting_link("not a link") == ("", False)
