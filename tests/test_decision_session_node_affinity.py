"""One mail is read on one machine, or it is not read at all.

The model is not a random sampler. Measured against production on
byte-identical input, with temperature 0 and no seed:

  rtx4060, warm      INTERVIEW_UPDATE        9 runs out of 9, identical
  rtx4060, reloaded  SELECTED                4 runs out of 4, identical
  jagadeesh          INTERVIEW_SHORTLISTED   3 runs out of 3, identical

Each execution state answers the same way every time, and different states
answer differently. Across nine real mails the two nodes disagreed on three.

That is only a correctness problem because one analysis makes three calls.
`OLLAMA_REQUEST_TIMEOUT` is 300s and jagadeesh was measured at 300-420s under
load, so a call could time out and fail over: production logs show the
validator timing out on jagadeesh and being retried on rtx4060 within the same
mail. The disagreement check then compared two machines rather than two
readings, and a genuine Karat interview reminder -- relevance ESTABLISHED,
confidence 1.0, full schedule -- was refused as AI_REQUIRES_REVIEW.

A decision session pins the node for the whole analysis. Nothing else moves:
the relevance gate was checked on both nodes across six marketing samples and
two genuine ones and agreed every time, so the blocking path never depended on
this. MODEL_DISAGREEMENT is untouched -- this makes it mean what it says.
"""

from __future__ import annotations

import inspect

import pytest

from core import ollama_nodes
from services import recruitment_mail_agent as agent


@pytest.fixture(autouse=True)
def _clean_session():
    """No test may leak a pin into the next one."""
    yield
    ollama_nodes._decision.active = False
    ollama_nodes._decision.node_id = None


class TestTheSessionHoldsOneNode:
    def test_nothing_is_pinned_outside_a_session(self):
        assert ollama_nodes.decision_node() is None
        assert ollama_nodes.decision_session_active() is False

    def test_a_session_starts_unpinned_so_the_first_call_chooses_freely(self):
        with ollama_nodes.decision_session():
            assert ollama_nodes.decision_session_active() is True
            assert ollama_nodes.decision_node() is None

    def test_the_first_resolved_node_becomes_the_session_node(self):
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("jagadeesh")
            assert ollama_nodes.decision_node() == "jagadeesh"

    def test_later_calls_cannot_choose_a_different_node(self):
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("jagadeesh")
            ollama_nodes._remember_decision_node("rtx4060")
            assert ollama_nodes.decision_node() == "jagadeesh"

    def test_the_routing_order_collapses_to_that_node(self):
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("jagadeesh")
            assert ollama_nodes.candidate_order("qwen2.5:7b") == ["jagadeesh"]

    def test_which_is_what_stops_a_mid_analysis_failover(self):
        """An empty order after exclusion is what makes selection raise."""
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("jagadeesh")
            order = ollama_nodes.candidate_order("qwen2.5:7b")
            remaining = [n for n in order if n not in {"jagadeesh"}]
            assert remaining == []


class TestTheSessionCleansUp:
    def test_the_pin_is_released_on_exit(self):
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("rtx4060")
        assert ollama_nodes.decision_node() is None
        assert ollama_nodes.decision_session_active() is False

    def test_it_is_released_even_when_the_analysis_raises(self):
        with pytest.raises(RuntimeError):
            with ollama_nodes.decision_session():
                ollama_nodes._remember_decision_node("rtx4060")
                raise RuntimeError("gateway failed")
        assert ollama_nodes.decision_node() is None

    def test_a_nested_session_restores_the_outer_one(self):
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("jagadeesh")
            with ollama_nodes.decision_session():
                ollama_nodes._remember_decision_node("rtx4060")
                assert ollama_nodes.decision_node() == "rtx4060"
            assert ollama_nodes.decision_node() == "jagadeesh"

    def test_normal_routing_returns_afterwards(self):
        with ollama_nodes.decision_session():
            ollama_nodes._remember_decision_node("jagadeesh")
        order = ollama_nodes.candidate_order("qwen2.5:7b")
        assert len(order) > 1
        assert set(order) <= {n["id"] for n in ollama_nodes.configured_nodes()}


class TestAnalyzeOpensOne:
    def test_analyze_wraps_the_whole_decision(self):
        source = inspect.getsource(agent.analyze)
        assert "decision_session()" in source
        assert "_analyze_on_one_node" in source

    def test_every_model_call_is_inside_it(self):
        """Relevance, classifier and validator all live in the wrapped body."""
        body = inspect.getsource(agent._analyze_on_one_node)
        for workload in ("recruitment_mail_relevance", "recruitment_mail_primary",
                         "recruitment_mail_validator"):
            assert workload in body
        assert "decision_session" not in body

    def test_the_wrapper_adds_nothing_but_the_session(self):
        source = inspect.getsource(agent.analyze)
        head = source[source.index("with ollama_nodes.decision_session()"):]
        assert "return _analyze_on_one_node(message, attachment_texts)" in head


class TestNothingElseChanged:
    def test_the_model_pin_still_leads_when_no_session_is_open(self, monkeypatch):
        monkeypatch.setattr(
            ollama_nodes, "model_node_preference", lambda: {"qwen2.5:7b": "jagadeesh"})
        assert ollama_nodes.candidate_order("qwen2.5:7b")[0] == "jagadeesh"

    def test_a_session_still_honours_the_model_pin_on_its_first_call(self, monkeypatch):
        """The session does not choose the node; it only remembers it."""
        monkeypatch.setattr(
            ollama_nodes, "model_node_preference", lambda: {"qwen2.5:7b": "jagadeesh"})
        with ollama_nodes.decision_session():
            assert ollama_nodes.candidate_order("qwen2.5:7b")[0] == "jagadeesh"

    def test_model_disagreement_detection_is_untouched(self):
        """This change only stops feeding it answers from two machines.

        The disagreement branch still exists and still validates no
        transition. Where it sends the mail -- automatic retry rather than a
        person -- is settled in test_model_disagreement_resolves_itself.py.
        """
        source = inspect.getsource(agent._validate_result)
        assert 'if "MODEL_DISAGREEMENT" in' in source
        marker = source.index('if "MODEL_DISAGREEMENT" in')
        block = source[marker:marker + 700]
        assert "backend_transition_validated=False" in block
        assert 'backend_validation_reason="MODEL_DISAGREEMENT"' in block

    def test_no_seed_was_introduced(self):
        """Measured: a seed did not stabilise anything. On rtx4060 seed=42 gave
        two answers in three runs where no seed gave one in three.

        The gateway sends one only when a caller passes it, and the only caller
        that does is Prompt 12, which the owner tested with seed 12 and runs as
        tested (13 Nov 2026). The recruitment readers still send none."""
        from core import ai_gateway
        from services import prompt12_classifier, recruitment_mail_agent

        gateway = inspect.getsource(ai_gateway)
        assert gateway.count('["seed"]') == 1
        assert 'if seed is not None:\n        request_payload["options"]["seed"]' in gateway
        assert "seed=" not in inspect.getsource(recruitment_mail_agent)
        assert "seed=SEED" in inspect.getsource(prompt12_classifier)
