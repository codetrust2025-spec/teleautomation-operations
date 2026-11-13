"""qwen2.5:7b prefers the RTX 4060 while it fits there, with the RTX 3050 as fallback.

The 4060 is an 8 GB card that also serves the vision model (qwen3-vl). Text is
sent to it only while qwen2.5:7b is resident there or fits beside what is
resident, so a text request never evicts the vision model or overfills the
card; otherwise the 3050 takes it and the 4060 stays a fallback.
"""
import json

import pytest

from core import ollama_nodes as nodes

GB = 1_000_000_000
TEXT, VISION = "qwen2.5:7b", "qwen3-vl:8b-instruct"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "state.json"))
    for name in ("OLLAMA_MODEL_NODE_PREFERENCE", "OLLAMA_NODE_VRAM_BUDGET_GB", "OLLAMA_MODEL_VRAM_GB"):
        monkeypatch.delenv(name, raising=False)
    nodes._resident_cache.clear()
    # The owner's routing: text 4060 then 3050; vision on the 4060 alone.
    state = {"model_nodes": {TEXT: ["rtx4060", "jagadeesh"], VISION: "rtx4060"}, "primary_node": "rtx4060"}
    (tmp_path / "state.json").write_text(json.dumps(state))
    yield


def resident(monkeypatch, models_by_node):
    def fake_request(node_id, path, **_):
        assert path == "/api/ps"
        value = models_by_node.get(node_id)
        if isinstance(value, Exception):
            raise value
        return {"models": value or []}
    monkeypatch.setattr(nodes, "_request", fake_request)


def test_an_idle_4060_takes_text_first(monkeypatch):
    resident(monkeypatch, {"rtx4060": []})
    assert nodes.candidate_order(TEXT)[:2] == ["rtx4060", "jagadeesh"]


def test_text_already_resident_on_the_4060_stays_there(monkeypatch):
    resident(monkeypatch, {"rtx4060": [{"name": TEXT, "size_vram": int(4.99 * GB)}]})
    assert nodes.candidate_order(TEXT)[0] == "rtx4060"


def test_the_vision_model_on_the_4060_sends_text_to_the_3050(monkeypatch):
    # Measured 13 Nov: vision holds 5.56 GB; + 5.2 GB text exceeds the 7.5 GB budget.
    resident(monkeypatch, {"rtx4060": [{"name": VISION, "size_vram": int(5.56 * GB)}]})
    order = nodes.candidate_order(TEXT)
    assert order[:2] == ["jagadeesh", "rtx4060"]  # the 4060 stays a fallback
    assert order.count("rtx4060") == 1


def test_an_unknown_4060_is_not_preferred_but_still_a_fallback(monkeypatch):
    resident(monkeypatch, {"rtx4060": OSError("tunnel down")})
    assert nodes.candidate_order(TEXT)[:2] == ["jagadeesh", "rtx4060"]


def test_vision_is_never_capacity_checked_away_from_its_only_node(monkeypatch):
    resident(monkeypatch, {"rtx4060": [{"name": TEXT, "size_vram": int(4.99 * GB)}]})
    assert nodes.candidate_order(VISION)[0] == "rtx4060"


def test_budgets_and_sizes_can_be_set_without_a_release(monkeypatch):
    resident(monkeypatch, {"rtx4060": [{"name": "small:1b", "size_vram": int(1.0 * GB)}]})
    assert nodes.candidate_order(TEXT)[0] == "rtx4060"  # 1.0 + 5.2 <= 7.5
    monkeypatch.setenv("OLLAMA_NODE_VRAM_BUDGET_GB", json.dumps({"rtx4060": 6.0}))
    nodes._resident_cache.clear()
    assert nodes.candidate_order(TEXT)[0] == "jagadeesh"  # 1.0 + 5.2 > 6.0


def test_residency_is_read_at_most_every_few_seconds(monkeypatch):
    calls = []
    monkeypatch.setattr(nodes, "_request", lambda node_id, path, **_: calls.append(node_id) or {"models": []})
    for _ in range(5):
        nodes.candidate_order(TEXT)
    assert calls == ["rtx4060"]


def test_a_single_node_pin_behaves_as_before(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"model_nodes": {TEXT: "jagadeesh"}, "primary_node": "rtx4060"}))
    resident(monkeypatch, {"rtx4060": []})
    assert nodes.candidate_order(TEXT)[:2] == ["jagadeesh", "rtx4060"]
    assert nodes.model_node_preference()[TEXT] == "jagadeesh"


def test_an_ordered_preference_is_saved_and_read_back(monkeypatch, tmp_path):
    nodes.set_model_node(TEXT, ["rtx4060", "jagadeesh"], force=True)
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["model_nodes"][TEXT] == ["rtx4060", "jagadeesh"]
    assert nodes.model_node_orders()[TEXT] == ["rtx4060", "jagadeesh"]
    assert nodes.model_node_preference()[TEXT] == "rtx4060"
    with pytest.raises(ValueError):
        nodes.set_model_node(TEXT, ["rtx4060", "rtx4060"], force=True)
    with pytest.raises(ValueError):
        nodes.set_model_node(TEXT, ["rtx4060", "nowhere"], force=True)
