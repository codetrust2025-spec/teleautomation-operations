"""No keyword path may decide that a mail is about the recipient's hiring.

Pure Ollama mode was supposed to mean the model reads the whole mail and
decides its intent. One path around it survived: when the deterministic layer
asserted an interview, `_deterministic_relevance_result` returned
ESTABLISHED / RECIPIENT_HIRING_PROCESS at confidence 100 and the relevance
model was never called at all.

That layer matches vocabulary, not meaning. A handwriting-therapy mailing list
wrote "Join our FREE Interview with Imran Baig", gave a date, a time and a Zoom
link, and satisfied `_is_assertive_interview_invitation` -- its webinar and
workshop exclusion reads only the subject, and this subject was about a child's
behaviour. In production 128 mails reached the classifier with intent already
asserted this way, among them nine from that list, a Naukri newsletter and an
Uber account notice.

Running all six known marketing samples through the live pipeline separated the
two behaviours cleanly: the five that reached Ollama were rejected with an
accurate kind (PUBLIC_EVENT, NEWSLETTER, MARKETING_OR_TRAINING), and the only
one wrongly called a hiring process was the one that never reached it.

`calendar_invite_intent` removed the shortcut when the same thing happened to
three webinars. `analyze()` is the other entry point, and this covers it.

Nothing here loosens the guards downstream. The classifier and the backend
entailment check are what contained these mails while the shortcut was live,
and they stay exactly as they were.
"""

from __future__ import annotations

import inspect
import json

import pytest

from services import recruitment_mail_agent as agent
from services import recruitment_semantics as semantics

# The real mail, with the recipient's name removed. The details that matter are
# the ones the regex layer keys on: the word "Interview" after "Join", a date, a
# time, and a Zoom link -- with nothing in the subject to mark it as a webinar.
WEBINAR_SUBJECT = "Her son's behaviour transformed through GraphoTherapy | Read how"
WEBINAR_BODY = (
    "Dear friend, As a mother she was struggling. Fear. Anxiety. Sleepless nights. "
    "Then she started practicing GraphoTherapy consistently, and slowly everything "
    "began to change. Her son's behaviour improved. Their home became calmer. "
    "Join our FREE Interview with Imran Baig, India's most trusted handwriting "
    "analysis coach. 27th August 2026 Thursday, 3:30 PM IST. "
    "Zoom Link: https://us06web.zoom.us/meeting/register/test "
    "Meeting ID: 000 0000 0001 Passcode: TestPass0. "
    "With love and warmth, Indu and Mitesh. Unsubscribe."
)

# Same deterministic assertion, genuinely about the recipient. The fix must not
# turn this one away.
INTERVIEW_SUBJECT = "Your interview with Altimetrik is scheduled"
INTERVIEW_BODY = (
    "Dear Candidate, Your technical interview has been scheduled for "
    "27th August 2026 at 3:30 PM IST. Please join the interview using the link "
    "below. Zoom Link: https://us06web.zoom.us/j/123 Meeting ID: 000 0000 0001."
)


def _message(subject: str, body: str, sender: str):
    return {
        "subject": subject, "body": body, "sender_name": "Sender",
        "sender_email": sender, "recipient_email": "candidate@example.com",
        "sent_at": "2026-08-25T07:00:00+05:30",
    }


WEBINAR = _message(WEBINAR_SUBJECT, WEBINAR_BODY, "contact@miteshkhatri.com")
INTERVIEW = _message(INTERVIEW_SUBJECT, INTERVIEW_BODY, "support@karat.io")


class _Response:
    def __init__(self, value, model="primary-model"):
        self.content = json.dumps(value)
        self.model = model
        self.duration_ms = 3


def _relevance(decision, kind, quote, reason):
    return {
        "decision": decision, "message_kind": kind, "confidence": 97,
        "evidence": [{"source": "EMAIL_BODY", "text": quote}], "reason": reason,
    }


@pytest.fixture
def calls(monkeypatch):
    """Record every model call `analyze()` makes, in order."""
    recorded: list[dict] = []
    monkeypatch.setattr(agent, "configured_models", lambda: {
        "primary": "primary-model", "validator": "validator-model",
        "fallback": "fallback-model",
    })
    return recorded


def _answer_with(monkeypatch, recorded, outputs):
    stream = iter(outputs)

    def respond(**kwargs):
        recorded.append(kwargs)
        return _Response(next(stream), kwargs["model"])

    monkeypatch.setattr(agent, "chat_structured", respond)


def _relevance_calls(recorded):
    return [c for c in recorded if c.get("schema") is agent.RELEVANCE_SCHEMA]


class TestThePreconditionThisTestRestsOn:
    """Without these the rest could pass while proving nothing."""

    def test_the_regex_layer_really_does_assert_an_interview_here(self):
        assert semantics._is_assertive_interview_invitation(WEBINAR_SUBJECT, WEBINAR_BODY)

    def test_and_therefore_calls_the_webinar_a_hiring_process(self):
        context = semantics.classify_context(
            WEBINAR_SUBJECT, WEBINAR_BODY,
            sender_email="contact@miteshkhatri.com", sent_at="2026-08-25T07:00:00+05:30",
        )
        assert context["recruitment_relevance"] == "ESTABLISHED"
        assert context["interview_event"] == "INTERVIEW_CONFIRMED"
        # Not caught as promotional either -- nothing else stops it.
        assert context["is_promotional_or_job_ad"] is False


class TestOllamaDecidesIntent:
    def test_the_model_is_asked_even_when_the_regex_layer_has_decided(self, monkeypatch, calls):
        """The exact bypass: relevance settled without a single model call."""
        _answer_with(monkeypatch, calls, [
            _relevance("NOT_ESTABLISHED", "PUBLIC_EVENT", "Join our FREE Interview with Imran Baig",
                       "A public handwriting-analysis session, not this recipient's hiring process."),
        ])

        agent.analyze(WEBINAR, [])

        assert len(_relevance_calls(calls)) == 1

    def test_the_webinar_stops_at_the_gate(self, monkeypatch, calls):
        _answer_with(monkeypatch, calls, [
            _relevance("NOT_ESTABLISHED", "PUBLIC_EVENT", "Join our FREE Interview with Imran Baig",
                       "A public handwriting-analysis session, not this recipient's hiring process."),
        ])

        result, _, _ = agent.analyze(WEBINAR, [])

        assert result["primary_status"] == "IGNORED_NOT_OFFER_RELATED"
        assert result["classification"] == "not_relevant"
        assert result["should_create_review_record"] is False
        # The classifier is never reached, so no schedule is ever proposed.
        assert result["_decision_trace"]["primary_model_result"] is None

    def test_the_answer_is_the_model_s_and_says_so(self, monkeypatch, calls):
        """An audit must be able to see which judgement allowed a mail through."""
        _answer_with(monkeypatch, calls, [
            _relevance("NOT_ESTABLISHED", "MARKETING_OR_TRAINING", "Join our FREE Interview with Imran Baig",
                       "Promotional content for a paid coaching programme."),
        ])

        result, _, _ = agent.analyze(WEBINAR, [])

        assert result["recruitment_relevance_result"]["source"] == "RELEVANCE_MODEL"
        assert result["recruitment_relevance_result"]["model"] == "primary-model"

    def test_a_real_interview_with_the_same_wording_still_gets_through(self, monkeypatch, calls):
        """The regex layer asserts an interview for this one too. The difference
        must come from the model reading it, not from the assertion.

        Passing the gate is the whole claim here, so the run stops the moment
        the classifier is reached rather than restating what it does.
        """
        assert semantics._is_assertive_interview_invitation(INTERVIEW_SUBJECT, INTERVIEW_BODY)

        class ClassifierReached(Exception):
            pass

        answer = _relevance(
            "ESTABLISHED", "RECIPIENT_HIRING_PROCESS",
            "Your technical interview has been scheduled for 27th August 2026 at 3:30 PM IST.",
            "The mail schedules this recipient's own interview.",
        )

        def respond(**kwargs):
            calls.append(kwargs)
            if kwargs.get("schema") is agent.RELEVANCE_SCHEMA:
                return _Response(answer, kwargs["model"])
            raise ClassifierReached

        monkeypatch.setattr(agent, "chat_structured", respond)

        # analyze() wraps anything unexpected, so the sentinel arrives as the cause.
        with pytest.raises(agent.AIGatewayError) as raised:
            agent.analyze(INTERVIEW, [])

        assert isinstance(raised.value.__cause__, ClassifierReached)
        assert len(_relevance_calls(calls)) == 1
        assert len(calls) > 1


class TestTheShortcutIsGone:
    def test_the_function_no_longer_exists(self):
        assert not hasattr(agent, "_deterministic_relevance_result")

    def test_analyze_has_no_branch_that_skips_the_relevance_call(self):
        # analyze() is now a thin wrapper opening a decision session; the
        # decision itself lives in _analyze_on_one_node.
        source = inspect.getsource(agent._analyze_on_one_node)
        head = source[:source.index("classifier_input")]
        assert "RELEVANCE_PROMPT" in head
        assert "if relevance is None" not in head

    def test_only_the_model_s_answer_can_carry_a_relevance_source(self):
        """`_validate_relevance_result` stamps RELEVANCE_MODEL, and after the
        shortcut's removal nothing else writes that field."""
        source = inspect.getsource(agent)
        assert source.count('"source": "DETERMINISTIC_ASSERTIVE_CONTEXT"') == 0
        assert source.count('value["source"] = "RELEVANCE_MODEL"') == 1

    def test_the_calendar_path_still_has_no_shortcut_either(self):
        assert "_deterministic_relevance_result" not in inspect.getsource(
            agent.calendar_invite_intent,
        )


class TestAMislabelledQuoteIsNotAnInventedOne:
    """Removing the shortcut exposed a second defect it had been masking.

    Production check on a genuine "Rescheduling interview for Application
    Security Engineer": the model answered ESTABLISHED /
    RECIPIENT_HIRING_PROCESS and quoted the mail's opening line word for word
    -- then labelled the quote ATTACHMENT, on a mail with no attachment.
    `_evidence_supported` searches only the declared category, so the quote
    failed, the evidence list emptied, and the answer was downgraded to
    NOT_ESTABLISHED / UNKNOWN. A real interview would have been dropped.

    The classifier has always corrected this with
    `_canonicalise_evidence_source`. The relevance gate did not.
    """

    BODY = "Reschedule Interview Notification. Your interview has been re-scheduled to 28/08/2026 03:30 PM IST."
    PAYLOAD = {"subject": "Rescheduling interview for Application Security Engineer", "body": BODY}

    def _validated(self, evidence):
        value = {
            "decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
            "confidence": 96, "evidence": evidence,
            "reason": "The mail reschedules this recipient's own interview.",
        }
        agent._validate_relevance_result(value, self.PAYLOAD)
        return value

    def test_a_verbatim_body_quote_labelled_attachment_is_kept(self):
        value = self._validated([
            {"source": "ATTACHMENT", "text": "Reschedule Interview Notification"},
        ])
        assert value["decision"] == "ESTABLISHED"
        assert value["message_kind"] == "RECIPIENT_HIRING_PROCESS"

    def test_and_the_label_is_corrected_to_where_it_was_found(self):
        value = self._validated([
            {"source": "ATTACHMENT", "text": "Reschedule Interview Notification"},
        ])
        assert value["evidence"][0]["source"] == "EMAIL_BODY"
        assert value["evidence"][0]["evidence_source_corrected_from"] == "ATTACHMENT"

    def test_the_quote_itself_is_never_corrected(self):
        """Only the label moves. Invented text has nowhere to match."""
        value = self._validated([
            {"source": "ATTACHMENT", "text": "Your offer letter is attached"},
        ])
        assert value["decision"] == "NOT_ESTABLISHED"
        assert value["message_kind"] == "UNKNOWN"
        assert value["evidence"] == []

    def test_a_quote_in_two_sources_still_fails_closed(self):
        """Ambiguous provenance proves nothing, so it is not rescued."""
        payload = {"subject": "Interview rescheduled", "body": "Interview rescheduled to 3:30 PM."}
        value = {
            "decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
            "confidence": 96,
            "evidence": [{"source": "ATTACHMENT", "text": "Interview rescheduled"}],
            "reason": "Ambiguous provenance.",
        }
        agent._validate_relevance_result(value, payload)
        assert value["decision"] == "NOT_ESTABLISHED"

    def test_a_correctly_labelled_quote_is_untouched(self):
        value = self._validated([
            {"source": "EMAIL_BODY", "text": "Reschedule Interview Notification"},
        ])
        assert value["decision"] == "ESTABLISHED"
        assert value["evidence"][0]["source"] == "EMAIL_BODY"
        assert "evidence_source_corrected_from" not in value["evidence"][0]

    def test_the_gate_and_the_classifier_now_judge_evidence_the_same_way(self):
        relevance_gate = inspect.getsource(agent._validate_relevance_result)
        classifier = inspect.getsource(agent._validate_result)
        for source in (relevance_gate, classifier):
            assert "_canonicalise_evidence_source" in source
            assert "_evidence_supported" in source


class TestNothingDownstreamWasLoosened:
    """These guards contained the 128 mails while the shortcut was live."""

    def test_unquotable_evidence_still_drops_a_relevance_claim(self):
        value = {
            "decision": "ESTABLISHED", "message_kind": "RECIPIENT_HIRING_PROCESS",
            "confidence": 95,
            "evidence": [{"source": "EMAIL_BODY", "text": "your offer has been released"}],
            "reason": "Invented.",
        }
        agent._validate_relevance_result(value, {"subject": "Hello", "body": "Nothing like it."})
        assert value["decision"] == "NOT_ESTABLISHED"
        assert value["message_kind"] == "UNKNOWN"

    def test_the_backend_entailment_check_is_untouched(self):
        """The webinar's own context, had the classifier proposed a booking."""
        context = semantics.classify_context(
            WEBINAR_SUBJECT, WEBINAR_BODY,
            sender_email="contact@miteshkhatri.com", sent_at="2026-08-25T07:00:00+05:30",
        )
        assert semantics.validate_interview_event("INTERVIEW_CONFIRMED", {}) == (
            "NONE", "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"
        )
        # And with support present it still passes, so this guard is unchanged
        # rather than merely strict.
        assert semantics.validate_interview_event("INTERVIEW_CONFIRMED", context) == (
            "INTERVIEW_CONFIRMED", None
        )

    def test_the_deterministic_context_is_still_recorded_for_every_mail(self, monkeypatch, calls):
        """It stops being a decision. It does not stop being evidence."""
        _answer_with(monkeypatch, calls, [
            _relevance("NOT_ESTABLISHED", "PUBLIC_EVENT", "Join our FREE Interview with Imran Baig",
                       "A public session."),
        ])

        result, _, _ = agent.analyze(WEBINAR, [])

        context = result["_decision_trace"]["deterministic_context"]["semantic_context"]
        assert context["recruitment_relevance"] == "ESTABLISHED"
        assert context["interview_event"] == "INTERVIEW_CONFIRMED"
