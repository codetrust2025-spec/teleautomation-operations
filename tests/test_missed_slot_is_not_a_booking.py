"""A missed interview slot books nothing, and a reminder books nothing twice.

Lavanya Venkata Chundru was booked for 2026-09-11, 02:30-04:00 IST, twice, for
an interview she had already missed.

The times are not the fault. The Persistent Systems mail says the slot was
"02:00 PM - 03:30 PM PDT" and the Google invitation carries
TZID America/Los_Angeles, so 02:30-04:00 IST is that slot correctly converted.
The reschedule deadline -- "completed before 10 Sep 2026, 11:00 PM" -- was
never read as an interview time; the extracted time was 02:00 PM, never 11:00 PM.

Two things were actually wrong.

**The missed-slot mail did nothing.** It arrived at 22:40 saying the slot had
been missed, and the confirmed booking stayed exactly as it was. Nothing in the
deterministic vocabulary knew what a missed slot was: the mail says "reschedule
your interview" while the reschedule pattern requires "rescheduled", so the
model's INTERVIEW_RESCHEDULED reading had nothing to corroborate it, and the
mail was parked and then ignored. The same happened on 19 August and 27 August.

**A reminder booked the interview it was reminding about.** "Your AI interview
starts in 30 minutes" was read as INTERVIEW_CONFIRMED and auto-booked a second
time. It carries no calendar UID and opens its own thread, so every identity
test in `_same_lifecycle_slot` missed the row the Google invitation had already
written -- two slots for one interview, at the identical minutes.
"""

from __future__ import annotations

import pytest

from services import recruitment_semantics as sem
from services.interview_auto_booking import _same_lifecycle_slot


SUBJECT = ("Interview Slot Missed for V1 AI Java React Kafka Mongo "
           "Microservices Role at Persistent Systems")
BODY = (
    "Interview slot missed for V1 AI Java React Kafka Mongo Microservices Role "
    "Hi Lavanya Venkata, It looks like you missed your interview slot. You can "
    "reschedule your interview by clicking the button below. Please ensure it is "
    "completed before 10 Sep 2026, 11:00 PM. Sep 10 Thursday, 10th September 2026 "
    "Missed Slot 02:00 PM - 03:30 PM PDT 60 mins Persistent Systems Reschedule "
    "Interview You can attempt the interview until 10 Sep 2026, 11:00 PM. Kindly "
    "note that you can only reschedule once."
)
SENDER = "persistent.interview@zeko.ai"


@pytest.fixture
def missed():
    return sem.classify_context(SUBJECT, BODY, sender_email=SENDER)


class TestTheMissedSlotBooksNothing:
    def test_the_source_reads_it_as_the_booking_being_spent(self, missed):
        assert missed["interview_event"] == "INTERVIEW_CANCELLED"
        assert set(missed["asserted_transitions"]) == {"INTERVIEW_CANCELLED"}

    @pytest.mark.parametrize("proposed", ["INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED"])
    def test_no_model_reading_can_turn_it_into_a_booking(self, missed, proposed):
        """The rule, stated once: a missed slot cannot schedule anything.

        Whatever the model proposes, the source refuses to support a
        confirmation or a reschedule, so nothing downstream can book.
        """
        status, reason = sem.validate_interview_event(proposed, missed)
        assert status == "NONE"
        assert reason == "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"

    def test_releasing_the_missed_booking_is_supported(self, missed):
        assert sem.validate_interview_event("INTERVIEW_CANCELLED", missed) == (
            "INTERVIEW_CANCELLED", None)

    def test_it_is_no_longer_read_as_a_questionnaire(self, missed):
        """It was `RECRUITER_QUESTIONNAIRE`, which is why nothing acted on it."""
        assert missed["is_questionnaire"] is False
        assert missed["email_intent"] == "INTERVIEW_CANCELLATION"

    @pytest.mark.parametrize("phrase", [
        "It looks like you missed your interview slot.",
        "You missed your interview for the Java role.",
        "Interview Slot Missed for Full Stack Developer",
        "You left your interview mid-way and can resume once.",
        "You did not attend your interview.",
    ])
    def test_the_shapes_this_recruiter_actually_sends(self, phrase):
        context = sem.classify_context("Interview update", phrase, sender_email=SENDER)
        assert context["interview_event"] == "INTERVIEW_CANCELLED"


class TestTheDeadlineIsNeverAnInterviewTime:
    def test_the_reschedule_deadline_does_not_schedule_anything(self, missed):
        """"complete before 10 Sep 2026, 11:00 PM" is a deadline, not a slot."""
        assert "11:00" not in str(missed.get("interview_event") or "")
        for proposed in ("INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED"):
            assert sem.validate_interview_event(proposed, missed)[0] == "NONE"

    @pytest.mark.parametrize("body", [
        "Please complete your interview before 10 Sep 2026, 11:00 PM.",
        "You can attempt the interview until 10 Sep 2026, 11:00 PM.",
        "Kindly respond before 12 Sep 2026, 6:00 PM to keep your candidature.",
    ])
    def test_a_deadline_alone_never_asserts_a_confirmed_interview(self, body):
        context = sem.classify_context("Action required", body, sender_email=SENDER)
        assert sem.validate_interview_event("INTERVIEW_CONFIRMED", context)[0] == "NONE"


class TestWhatMustNotChange:
    def test_a_reminder_still_confirms_its_interview(self):
        """The reminder is a real confirmation; it just must not book twice."""
        body = ("Interview reminder for V1 AI Java React Kafka Mongo Microservices Role. "
                "This is a quick reminder that your AI interview starts in 30 minutes. "
                "Sep 10 Thursday, 10th September 2026 02:00 PM - 03:30 PM PDT 60 mins")
        context = sem.classify_context(
            "Interview Reminder for V1 AI Java React Kafka Mongo Microservices Role",
            body, sender_email=SENDER)
        assert context["interview_event"] == "INTERVIEW_CONFIRMED"

    def test_dont_miss_your_interview_is_a_reminder_not_a_miss(self):
        context = sem.classify_context(
            "Reminder: don't miss your interview tomorrow",
            "Please don't miss your interview tomorrow at 10:00 AM IST with Acme.",
            sender_email="hr@acme.com")
        assert context["interview_event"] == "INTERVIEW_CONFIRMED"

    def test_a_missed_webinar_is_not_this_candidates_interview(self):
        context = sem.classify_context(
            "You missed our webinar", "Sorry you missed our webinar on system design.",
            sender_email="news@example.com")
        assert context["interview_event"] != "INTERVIEW_CANCELLED"

    def test_a_real_cancellation_still_cancels(self):
        context = sem.classify_context(
            "Interview cancelled", "Your interview has been cancelled by the panel.",
            sender_email="hr@acme.com")
        assert context["interview_event"] == "INTERVIEW_CANCELLED"


def row(**kw):
    base = {
        "date": "2026-09-11", "time": "02:30", "time_end": "04:00",
        "interview_calendar_uid": "", "interview_source_message_id": "",
        "interview_source_thread_id": "",
    }
    base.update(kw)
    return base


SCHEDULE = {"date": "2026-09-11", "time": "02:30", "time_end": "04:00"}


class TestOneInterviewIsOneSlot:
    def test_time_equality_without_source_identity_is_not_duplicate_proof(self):
        """The minimal schedule-only fixture cannot prove a reminder's identity.

        A real sibling requires source evidence, not an assumption that two
        interviews occupying identical minutes must be the same interview.
        """
        booked = row(interview_calendar_uid="syntheticuid000000000002cd@google.com",
                     interview_source_thread_id="thread-google")
        reminder = {"provider_message_id": "1a08d03543262239",
                    "provider_thread_id": "thread-zeko"}
        assert _same_lifecycle_slot(
            booked, result={}, message=reminder, schedule=SCHEDULE) is False

    def test_a_different_time_is_still_a_different_interview(self):
        booked = row(time="09:00", time_end="10:00")
        assert _same_lifecycle_slot(
            booked, result={}, message={"provider_thread_id": "x"}, schedule=SCHEDULE) is False

    def test_a_different_day_is_still_a_different_interview(self):
        booked = row(date="2026-09-12")
        assert _same_lifecycle_slot(
            booked, result={}, message={"provider_thread_id": "x"}, schedule=SCHEDULE) is False

    def test_a_calendar_uid_still_decides_when_the_invite_has_one(self):
        """Two UIDs that disagree are different meetings, whatever the clock says."""
        booked = row(interview_calendar_uid="uid-a")
        result = {"calendar": {"uid": "uid-b"}}
        assert _same_lifecycle_slot(
            booked, result=result, message={}, schedule=SCHEDULE) is False

    def test_the_same_uid_is_the_same_interview(self):
        booked = row(interview_calendar_uid="uid-a", time="09:00", time_end="10:00")
        result = {"calendar": {"uid": "uid-a"}}
        assert _same_lifecycle_slot(
            booked, result=result, message={}, schedule=SCHEDULE) is True


class TestSourceEvidenceMayReleaseABookingNotCreateOne:
    """The asymmetry that let Lavanya's missed slot stand.

    Both readings agreed the interview was off; the model paraphrased "you
    missed your interview slot" rather than quoting it, so
    `SOURCE_ASSERTS_TRANSITION_UNQUOTED` parked the mail and the confirmed
    booking stayed. Releasing a booking commits the candidate to nothing and a
    later invitation simply books again, so a missing quote is not a reason to
    keep a dead slot standing. Creating or moving one still needs the quote.
    """

    def _branch(self):
        import inspect

        from services import recruitment_mail_agent as agent

        source = inspect.getsource(agent._validate_result)
        start = source.index("if asserted_by_source and safe_status ==")
        return source[start:source.index("value.update(\n            status=\"IGNORED_NOT_OFFER_RELATED\"")]

    def test_a_cancellation_is_released_without_a_quoted_sentence(self):
        branch = self._branch()
        head = branch[:branch.index("if asserted_by_source:")]
        assert 'classification="interview_cancelled"' in head
        assert "backend_transition_validated=True" in head
        assert "SOURCE_ASSERTS_CANCELLATION_UNQUOTED" in head

    def test_only_a_cancellation_qualifies(self):
        branch = self._branch()
        assert 'safe_status == "INTERVIEW_CANCELLED"' in branch

    def test_a_confirmation_without_a_quote_still_books_nothing(self):
        """The anti-hallucination guard, unchanged for anything that commits."""
        branch = self._branch()
        tail = branch[branch.index("if asserted_by_source:"):]
        assert "AI_RETRY_PENDING" in tail
        assert "backend_transition_validated=False" in tail
        assert 'classification="interview_confirmed"' not in tail

    def test_no_unquoted_transition_asks_a_person(self):
        branch = self._branch()
        assert "MANUAL_REVIEW_REQUIRED" not in branch
        assert "requires_manual_review=True" not in branch
