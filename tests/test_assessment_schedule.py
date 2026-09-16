"""An assessment window becomes one slot, by rule, or becomes nothing.

The Glider body is the real production shape -- one flattened line of labelled
fields -- with the candidate's name replaced. Its window is thirteen hours wide
for a one-hour test, which is exactly the case the roster could not express and
the model tried to express as a 7:05 PM interview.
"""
from datetime import datetime, timedelta

import pytest

from services import assessment_schedule as schedule

IST = schedule.LOCAL_ZONE

GLIDER_BODY = (
    "Accept Invite: Yes No Hi Test Candidate, This is reminder from Intelliswift to complete "
    "the pending assessment. The details are below - Assessment Name: Automation "
    "Number of Question(s): 16 Start time: Tuesday, 15-Sep-2026 07:05 PM (IST) "
    "End time: Wednesday, 16-Sep-2026 08:30 AM (IST) Time limit: 60 min(s) "
    "If you are unable to attempt the assessment within the due date, you can request an "
    "extension before you start the assessment. Once you are ready to take the assessment, "
    "please click on the \"Start Assessment\" link below. Start Assessment Powered by GLIDER"
)
BEFORE_THE_WINDOW = datetime(2026, 9, 15, 14, 0, tzinfo=IST)


def slot_of(window, *, existing=None, now=BEFORE_THE_WINDOW):
    return schedule.resolve_slot(window, existing=existing or [], now=now)


class TestReadingTheWindow:
    def test_the_production_mail_yields_its_window_and_length(self):
        window = schedule.parse_window("Reminder - Intelliswift invitation for assessment", GLIDER_BODY)
        assert window.start == datetime(2026, 9, 15, 19, 5, tzinfo=IST)
        assert window.deadline == datetime(2026, 9, 16, 8, 30, tzinfo=IST)
        assert window.duration_minutes == 60
        assert window.name == "Automation"

    def test_a_window_is_not_an_appointment(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        assert window.exact is False

    def test_the_start_label_is_a_heading_not_the_word_start(self):
        # "before you start the assessment" and the "Start Assessment" link both
        # appear after the real fields and must not be read as an opening time.
        body = ("Complete by: 20-Sep-2026 06:00 PM Time limit: 30 mins. "
                "Please start the assessment on 25-Sep-2026 only if asked.")
        window = schedule.parse_window("assessment", body)
        assert window.start is None
        assert window.deadline == datetime(2026, 9, 20, 18, 0, tzinfo=IST)

    def test_a_stated_time_with_no_window_is_an_appointment(self):
        window = schedule.parse_window(
            "Assessment scheduled", "Your assessment is scheduled on 18-Sep-2026 at 3:00 PM. Duration: 45 minutes")
        assert window.exact is True
        assert window.start == datetime(2026, 9, 18, 15, 0, tzinfo=IST)
        assert window.duration_minutes == 45

    def test_a_window_no_longer_than_the_test_is_an_appointment(self):
        window = schedule.parse_window(
            "assessment", "Start time: 18-Sep-2026 10:00 AM End time: 18-Sep-2026 11:00 AM Time limit: 60 min(s)")
        assert window.exact is True

    def test_a_missing_length_is_an_hour(self):
        window = schedule.parse_window("assessment", "Start time: 18-Sep-2026 07:00 PM End time: 19-Sep-2026 09:00 AM")
        assert window.duration_minutes == schedule.DEFAULT_DURATION_MINUTES

    @pytest.mark.parametrize("text,expected", [
        ("Start time: 2026-09-18 19:30 End time: 2026-09-19 08:00", datetime(2026, 9, 18, 19, 30, tzinfo=IST)),
        ("Start time: 18/09/2026 07:30 PM End time: 19/09/2026 08:00 AM", datetime(2026, 9, 18, 19, 30, tzinfo=IST)),
        ("Start time: 18 September 2026, 7:30 PM End time: 19 September 2026, 8:00 AM",
         datetime(2026, 9, 18, 19, 30, tzinfo=IST)),
    ])
    def test_the_date_shapes_platforms_send(self, text, expected):
        assert schedule.parse_window("assessment", text).start == expected


class TestChoosingTheSlot:
    def test_the_production_window_books_the_hour_it_opens(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window)
        assert (decision.date, decision.time, decision.time_end) == ("2026-09-15", "19:05", "20:05")

    def test_a_stated_time_is_booked_exactly_for_its_duration(self):
        window = schedule.parse_window(
            "assessment", "Your assessment is scheduled on 18-Sep-2026 at 3:00 PM. Duration: 45 minutes")
        decision = slot_of(window)
        assert (decision.date, decision.time, decision.time_end) == ("2026-09-18", "15:00", "15:45")

    def test_a_date_without_a_time_starts_at_six_in_the_evening(self):
        window = schedule.parse_window(
            "assessment", "Complete by: 20-Sep-2026 11:00 PM Time limit: 60 min(s)")
        decision = slot_of(window, now=datetime(2026, 9, 20, 9, 0, tzinfo=IST))
        assert (decision.date, decision.time, decision.time_end) == ("2026-09-20", "18:00", "19:00")

    def test_the_slot_never_starts_before_the_window_opens(self):
        window = schedule.parse_window(
            "assessment", "Start time: 20-Sep-2026 08:30 PM End time: 21-Sep-2026 08:00 AM Time limit: 60 min(s)")
        decision = slot_of(window, now=datetime(2026, 9, 20, 9, 0, tzinfo=IST))
        assert decision.time == "20:30"

    def test_a_taken_slot_moves_on_rather_than_double_booking(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window, existing=[
            {"date": "2026-09-15", "time": "19:00", "time_end": "20:00"}])
        assert (decision.time, decision.time_end) == ("20:05", "21:05")

    def test_it_keeps_moving_until_it_finds_room(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window, existing=[
            {"date": "2026-09-15", "time": "19:00", "time_end": "20:00"},
            {"date": "2026-09-15", "time": "20:00", "time_end": "21:30"}])
        assert decision.time == "21:35"

    def test_a_booking_on_another_day_is_not_in_the_way(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window, existing=[
            {"date": "2026-09-14", "time": "19:00", "time_end": "21:00"}])
        assert decision.time == "19:05"

    def test_the_slot_finishes_before_the_deadline(self):
        window = schedule.parse_window(
            "assessment", "Start time: 20-Sep-2026 11:00 PM End time: 20-Sep-2026 11:40 PM Time limit: 60 min(s)")
        decision = slot_of(window, now=datetime(2026, 9, 20, 9, 0, tzinfo=IST))
        assert not decision.bookable
        assert decision.reason_code == "ASSESSMENT_WINDOW_TOO_SHORT"

    def test_a_window_already_closed_books_nothing(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window, now=datetime(2026, 9, 16, 12, 0, tzinfo=IST))
        assert not decision.bookable
        assert decision.reason_code == "ASSESSMENT_WINDOW_CLOSED"

    def test_a_stated_time_already_past_books_nothing(self):
        window = schedule.parse_window(
            "assessment", "Your assessment is scheduled on 18-Sep-2026 at 3:00 PM. Duration: 45 minutes")
        decision = slot_of(window, now=datetime(2026, 9, 18, 16, 0, tzinfo=IST))
        assert decision.reason_code == "ASSESSMENT_TIME_PASSED"

    def test_a_mail_with_no_date_books_nothing(self):
        window = schedule.parse_window("assessment", "Please complete your pending assessment. Time limit: 60 min(s)")
        decision = slot_of(window)
        assert not decision.bookable
        assert decision.reason_code == "ASSESSMENT_WITHOUT_DATE"

    def test_a_fully_booked_window_books_nothing(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        taken = ([{"date": "2026-09-15", "time": f"{hour:02d}:00", "time_end": f"{hour + 1:02d}:00"}
                  for hour in range(18, 23)]
                 + [{"date": "2026-09-15", "time": "23:00", "time_end": "23:59"}]
                 + [{"date": "2026-09-16", "time": f"{hour:02d}:00", "time_end": f"{hour + 1:02d}:00"}
                    for hour in range(0, 9)])
        decision = slot_of(window, existing=taken)
        assert not decision.bookable
        assert decision.reason_code == "ASSESSMENT_NO_FREE_SLOT"

    def test_the_evening_is_preferred_over_the_hours_after_midnight(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        assert slot_of(window).date == "2026-09-15"

    def test_a_slot_is_never_chosen_in_the_past(self):
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window, now=datetime(2026, 9, 15, 20, 40, tzinfo=IST))
        assert decision.time == "20:45"
        assert decision.time_end == "21:45"

    def test_the_last_hour_before_the_deadline_still_books(self):
        # The reminder is read the next morning with an hour of window left:
        # the evening it was written for has gone, the window has not.
        window = schedule.parse_window("assessment", GLIDER_BODY)
        decision = slot_of(window, now=datetime(2026, 9, 16, 7, 20, tzinfo=IST))
        assert (decision.date, decision.time, decision.time_end) == ("2026-09-16", "07:30", "08:30")
