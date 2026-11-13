"""Shared test setup.

Prompt 12 (services/prompt12_classifier.py) runs on every mail that reaches
classification. Tests written before it exercise the readers and rules beneath
it and stub those models, not Prompt 12. For them Prompt 12 answers as the
readers concluded -- SCHEDULED for a confirmed or rescheduled interview,
CANCELED for a cancellation, OTHER for anything else -- so they keep testing
what they were written for, and a reading Prompt 12 would now drop (a
shortlist, a final round, an unproven "Selected" refiled as an interview) is
dropped here too.

A test that needs Prompt 12 itself -- its prompt, its request, a verdict that
disagrees with the readers -- uses the `prompt12_verdict` fixture to set the
answer, or lives in test_prompt12_*.py, which this stub leaves alone.
"""
import pytest

from services import prompt12_classifier

_AGREE = "__TEST_AGREES_WITH_READERS__"
_FROM_STATUS = {
    "INTERVIEW_CONFIRMED": "SCHEDULED",
    "INTERVIEW_RESCHEDULED": "SCHEDULED",
    "INTERVIEW_CANCELLED": "CANCELED",
}


@pytest.fixture(autouse=True)
def _prompt12_answers_like_the_readers(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1].startswith("test_prompt12_"):
        yield None
        return
    state = {"verdict": _AGREE}
    real_apply = prompt12_classifier.apply_verdict

    def classify(subject, body, **_):
        return {"verdict": state["verdict"], "unparsed": False, "raw": "", "model": "qwen2.5:7b",
                "node": "", "prompt_name": prompt12_classifier.PROMPT_NAME,
                "prompt_version": prompt12_classifier.PROMPT_VERSION,
                "prompt_sha256": prompt12_classifier.PROMPT12_SHA256, "duration_ms": 0}

    def apply_verdict(result, record, **kwargs):
        if record.get("verdict") == _AGREE:
            status = str(result.get("primary_status") or result.get("status") or "").upper()
            record = {**record, "verdict": _FROM_STATUS.get(status, "OTHER")}
        return real_apply(result, record, **kwargs)

    monkeypatch.setattr(prompt12_classifier, "classify", classify)
    monkeypatch.setattr(prompt12_classifier, "apply_verdict", apply_verdict)
    yield state


@pytest.fixture
def prompt12_verdict(_prompt12_answers_like_the_readers):
    """Set what Prompt 12 answers in this test: prompt12_verdict("OTHER")."""
    def set_verdict(verdict):
        _prompt12_answers_like_the_readers["verdict"] = verdict
    return set_verdict
