"""The three-laptop inference pool: membership, failover, and recovery.

The pool is reached over reverse SSH tunnels from laptops, so nodes disappear
routinely — a closed lid is an outage. Routing has to survive that without
either hammering a dead node or moving production's configured primary every
time a probe times out.
"""

import json

import pytest

from core import ollama_nodes

MODEL = "qwen3-vl:8b-instruct"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_NODE_STATE_FILE", str(tmp_path / "state.json"))
    for name in (
        "OLLAMA_PRIMARY_NODE",
        "OLLAMA_BASE_URL",
        "OLLAMA_NODE_RTX4060_URL",
        "OLLAMA_NODE_JAGADEESH_URL",
        "OLLAMA_NODE_OUR_MACHINE_URL",
        "OLLAMA_ENABLE_VPS_LOCAL",
        "OLLAMA_NODE_FAILURE_THRESHOLD",
        "OLLAMA_NODE_COOLDOWN_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    ollama_nodes.reset_breakers()
    yield
    ollama_nodes.reset_breakers()


def _stub_health(monkeypatch, healthy: set[str]):
    """Every node reachable with the model, except those left out of `healthy`."""

    def fake(node_id, *, model, timeout=5, deep=True):
        ok = node_id in healthy
        return {
            "id": node_id,
            "label": node_id,
            "primary": node_id == ollama_nodes.primary_node_id(),
            "status": "online" if ok else "offline",
            "endpoint_reachable": ok,
            "model": model,
            "model_available": ok,
            "model_loaded": False,
            "response_time_ms": 5,
            "error": None if ok else "ConnectionRefusedError",
            "breaker": ollama_nodes.breaker_state(node_id),
            "available": ok,
        }

    monkeypatch.setattr(ollama_nodes, "node_health", fake)


# ── pool membership ─────────────────────────────────────────────────────────


def test_the_three_laptops_are_present_in_preference_order():
    ids = [n["id"] for n in ollama_nodes.configured_nodes()]
    assert ids == ["rtx4060", "jagadeesh", "our_machine"]


def test_endpoints_match_the_agreed_tunnel_ports():
    urls = {n["id"]: n["base_url"] for n in ollama_nodes.configured_nodes()}
    assert urls["rtx4060"] == "http://127.0.0.1:11437"
    assert urls["jagadeesh"] == "http://127.0.0.1:11435"
    assert urls["our_machine"] == "http://127.0.0.1:11436"


def test_praveens_node_keeps_its_persisted_id_while_showing_a_clearer_label():
    """Renaming the id would orphan the persisted primary and the admin API."""
    node = ollama_nodes.node("our_machine")
    assert node["id"] == "our_machine"
    assert node["label"] == "Slim 3 — Backup AI 2"


def test_the_vps_node_is_absent_unless_explicitly_enabled(monkeypatch):
    assert "vps_local" not in [n["id"] for n in ollama_nodes.configured_nodes()]
    monkeypatch.setenv("OLLAMA_ENABLE_VPS_LOCAL", "true")
    nodes = [n["id"] for n in ollama_nodes.configured_nodes()]
    assert nodes == ["rtx4060", "jagadeesh", "our_machine", "vps_local"]
    assert ollama_nodes.base_url_for("vps_local") == "http://127.0.0.1:11434"


def test_a_node_is_addressed_by_its_own_variable_not_a_generic_one(monkeypatch):
    """OLLAMA_BASE_URL names "some Ollama"; it must not name a specific node.

    This replaces an earlier test that asserted the opposite. The old contract
    let OLLAMA_BASE_URL stand in for the Jagadeesh node, and production drifted
    exactly there: a stale value of 11434 pointed `jagadeesh` at the VPS's own
    CPU Ollama for as long as nobody looked, because 11434 also has
    qwen2.5:7b so every model check passed and no request ever failed.
    """
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:19999")
    assert ollama_nodes.base_url_for("jagadeesh") == "http://127.0.0.1:11435"
    assert ollama_nodes.base_url_for("rtx4060") == "http://127.0.0.1:11437"
    assert ollama_nodes.base_url_for("our_machine") == "http://127.0.0.1:11436"


def test_the_exact_production_regression_cannot_recur(monkeypatch):
    """A stale OLLAMA_BASE_URL of 11434 must not silently capture text routing.

    11434 is the VPS's own Ollama — no GPU, four cores shared with the web app.
    It is reachable and it does have the text model, so nothing about this
    failure is loud: the only symptom is that inference is slow and the GPU
    laptop is idle. Pinning the behaviour here so the silence cannot return.
    """
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    assert ollama_nodes.base_url_for("jagadeesh") == "http://127.0.0.1:11435"
    urls = {n["id"]: n["base_url"] for n in ollama_nodes.configured_nodes()}
    assert "http://127.0.0.1:11434" not in urls.values()


def test_the_jagadeesh_node_still_honours_its_own_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_NODE_JAGADEESH_URL", "http://127.0.0.1:19999")
    assert ollama_nodes.base_url_for("jagadeesh") == "http://127.0.0.1:19999"
    # ...and must not leak into its neighbours.
    assert ollama_nodes.base_url_for("rtx4060") == "http://127.0.0.1:11437"
    assert ollama_nodes.base_url_for("our_machine") == "http://127.0.0.1:11436"


def test_the_vps_node_is_the_only_way_to_reach_11434(monkeypatch):
    """11434 stays reachable, but only by asking for it deliberately."""
    monkeypatch.setenv("OLLAMA_ENABLE_VPS_LOCAL", "true")
    assert ollama_nodes.base_url_for("vps_local") == "http://127.0.0.1:11434"
    assert ollama_nodes.base_url_for("jagadeesh") == "http://127.0.0.1:11435"


# ── persistence ─────────────────────────────────────────────────────────────


def test_a_chosen_primary_survives_a_restart(monkeypatch, tmp_path):
    ollama_nodes.set_primary_node("our_machine", force=True)
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["primary_node"] == "our_machine"
    # A new process reads the file rather than the code default.
    assert ollama_nodes.primary_node_id() == "our_machine"


def test_an_unknown_persisted_primary_falls_back_instead_of_crashing(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps({"primary_node": "a-node-that-was-removed"}), encoding="utf-8"
    )
    assert ollama_nodes.primary_node_id() == "rtx4060"


def test_selecting_an_unknown_node_is_refused():
    with pytest.raises(ValueError):
        ollama_nodes.set_primary_node("nonsense", force=True)


# ── failover ────────────────────────────────────────────────────────────────


def test_requests_go_to_the_primary_while_it_is_healthy(monkeypatch):
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    _stub_health(monkeypatch, {"jagadeesh", "our_machine"})
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "jagadeesh"


def test_an_unhealthy_primary_fails_over_to_the_next_node(monkeypatch):
    ollama_nodes.set_primary_node("rtx4060", force=True)
    _stub_health(monkeypatch, {"jagadeesh", "our_machine"})
    chosen = ollama_nodes.select_available_node(model=MODEL)
    assert chosen["node_id"] == "jagadeesh"
    assert chosen["was_primary"] is False


def test_a_second_failure_falls_through_to_the_third_node(monkeypatch):
    ollama_nodes.set_primary_node("rtx4060", force=True)
    _stub_health(monkeypatch, {"our_machine"})
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "our_machine"


def test_a_node_missing_the_model_is_not_selected(monkeypatch):
    """An open port is not health. A node can accept TCP and still be useless."""
    ollama_nodes.set_primary_node("jagadeesh", force=True)

    def fake(node_id, *, model, timeout=5, deep=True):
        return {
            "id": node_id, "label": node_id, "primary": False, "status": "degraded",
            "endpoint_reachable": True,
            "model_available": node_id == "our_machine",
            "model_loaded": False, "response_time_ms": 5, "error": None,
            "breaker": ollama_nodes.breaker_state(node_id), "available": False,
        }

    monkeypatch.setattr(ollama_nodes, "node_health", fake)
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "our_machine"


def test_a_total_outage_raises_rather_than_returning_a_dead_node(monkeypatch):
    _stub_health(monkeypatch, set())
    with pytest.raises(RuntimeError, match="No Ollama node"):
        ollama_nodes.select_available_node(model=MODEL)


# ── circuit breaker: no flapping, real recovery ─────────────────────────────


def test_one_transient_failure_does_not_take_a_node_out_of_service(monkeypatch):
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "3")
    ollama_nodes.record_failure("jagadeesh", "timeout")
    assert ollama_nodes.in_cooldown("jagadeesh") is False
    assert ollama_nodes.breaker_state("jagadeesh")["consecutive_failures"] == 1


def test_repeated_failures_open_the_breaker(monkeypatch):
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("OLLAMA_NODE_COOLDOWN_SECONDS", "120")
    for _ in range(3):
        ollama_nodes.record_failure("jagadeesh", "timeout")
    assert ollama_nodes.in_cooldown("jagadeesh") is True
    assert ollama_nodes.breaker_state("jagadeesh")["cooldown_remaining_s"] > 0


def test_a_cooling_node_is_skipped_even_when_it_is_the_primary(monkeypatch):
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "2")
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    _stub_health(monkeypatch, {"jagadeesh", "our_machine"})
    for _ in range(2):
        ollama_nodes.record_failure("jagadeesh", "timeout")
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "our_machine"


def test_a_recovered_node_rejoins_once_it_succeeds(monkeypatch):
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "2")
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    _stub_health(monkeypatch, {"jagadeesh", "our_machine"})
    for _ in range(2):
        ollama_nodes.record_failure("jagadeesh", "timeout")
    assert ollama_nodes.in_cooldown("jagadeesh") is True

    ollama_nodes.record_success("jagadeesh")

    assert ollama_nodes.in_cooldown("jagadeesh") is False
    assert ollama_nodes.breaker_state("jagadeesh")["consecutive_failures"] == 0
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "jagadeesh"


def test_when_everything_is_cooling_the_pool_still_probes(monkeypatch):
    """A blanket outage must not become permanent because every breaker is open."""
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "1")
    _stub_health(monkeypatch, {"our_machine"})
    for node_id in ("rtx4060", "jagadeesh", "our_machine"):
        ollama_nodes.record_failure(node_id, "timeout")
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "our_machine"


def test_failover_never_rewrites_the_configured_primary(monkeypatch):
    """The anti-flap rule, and it still holds: routing around a sick node never
    edits the stored choice, so the node reclaims primary the moment it
    recovers and nobody has to set it again.

    What changed is what `primary_node_id()` reports. It used to name the
    stored node whether or not it was up, which meant an operator watching the
    dashboard saw work attributed to a machine that was answering nothing. It
    now names whoever is actually serving -- while leaving the stored choice
    exactly where it was."""
    ollama_nodes.set_primary_node("rtx4060", force=True)
    _stub_health(monkeypatch, {"our_machine"})

    for _ in range(5):
        assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "our_machine"

    stored = ollama_nodes.primary_override()
    assert stored["node_id"] == "rtx4060", "the stored choice must survive failover"
    assert ollama_nodes.primary_node_id() == "our_machine"

    # And it comes straight back, with no second admin action.
    ollama_nodes.record_success("rtx4060")
    assert ollama_nodes.primary_node_id() == "rtx4060"


# ── inference verification ──────────────────────────────────────────────────


def test_verify_inference_requires_a_real_completion(monkeypatch):
    calls = []

    def fake_request(node_id, path, *, method="GET", payload=None, timeout=5):
        calls.append((node_id, path, payload))
        return {"response": "ok", "eval_count": 4, "eval_duration": 1_000_000_000}

    monkeypatch.setattr(ollama_nodes, "_request", fake_request)
    result = ollama_nodes.verify_inference("jagadeesh", model=MODEL)

    assert result["ok"] is True
    assert result["tokens_per_second"] == 4.0
    assert calls[0][1] == "/api/generate"
    assert ollama_nodes.breaker_state("jagadeesh")["consecutive_failures"] == 0


def test_an_empty_completion_counts_as_a_failure(monkeypatch):
    monkeypatch.setattr(
        ollama_nodes, "_request", lambda *a, **k: {"response": "   "}
    )
    result = ollama_nodes.verify_inference("jagadeesh", model=MODEL)
    assert result["ok"] is False
    assert ollama_nodes.breaker_state("jagadeesh")["consecutive_failures"] == 1


def test_a_generation_error_counts_as_a_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(ollama_nodes, "_request", boom)
    assert ollama_nodes.verify_inference("jagadeesh", model=MODEL)["ok"] is False
    assert ollama_nodes.breaker_state("jagadeesh")["last_error"] == "OSError"


# ── promotion must verify every required model, not just one ────────────────


def _stub_installed(monkeypatch, per_node):
    """node_health reporting a specific installed-model list per node."""

    def fake(node_id, *, model, timeout=5, deep=True):
        installed = per_node.get(node_id)
        reachable = installed is not None
        return {
            "id": node_id, "label": node_id, "primary": False,
            "status": "online" if reachable else "offline",
            "endpoint_reachable": reachable,
            "model": model,
            "model_available": bool(installed and model in installed),
            "model_loaded": False, "response_time_ms": 5, "error": None,
            "installed_models": installed or [],
            "breaker": ollama_nodes.breaker_state(node_id),
            "available": reachable,
        }

    monkeypatch.setattr(ollama_nodes, "node_health", fake)
    monkeypatch.setattr(
        ollama_nodes, "required_models", lambda: ["qwen2.5:7b", MODEL]
    )


def test_a_node_missing_the_text_model_cannot_become_primary(monkeypatch):
    """The regression this guard exists for. rtx4060 was promoted on the
    strength of the vision model alone, while lacking the text model that
    invite extraction actually calls, and booking broke until it was reverted.
    Fast on vision is not the same as able to serve."""
    _stub_installed(monkeypatch, {
        "rtx4060": [MODEL],                    # vision only
        "jagadeesh": ["qwen2.5:7b", MODEL],
        "our_machine": ["qwen2.5:7b", MODEL],
    })
    ollama_nodes.set_primary_node("jagadeesh", force=True)

    with pytest.raises(ValueError, match="missing required model"):
        ollama_nodes.set_primary_node("rtx4060")

    assert ollama_nodes.primary_node_id() == "jagadeesh", "primary must not move"


def test_the_error_names_the_model_that_is_missing(monkeypatch):
    _stub_installed(monkeypatch, {"rtx4060": [MODEL], "jagadeesh": ["qwen2.5:7b", MODEL]})
    with pytest.raises(ValueError, match="qwen2.5:7b"):
        ollama_nodes.set_primary_node("rtx4060")


def test_a_fully_stocked_node_promotes_normally(monkeypatch):
    _stub_installed(monkeypatch, {
        "rtx4060": ["qwen2.5:7b", MODEL],
        "jagadeesh": ["qwen2.5:7b", MODEL],
    })
    assert ollama_nodes.set_primary_node("rtx4060") == "rtx4060"
    assert ollama_nodes.primary_node_id() == "rtx4060"


def test_an_unreachable_node_cannot_become_primary(monkeypatch):
    _stub_installed(monkeypatch, {"jagadeesh": ["qwen2.5:7b", MODEL]})
    with pytest.raises(RuntimeError, match="not reachable"):
        ollama_nodes.set_primary_node("rtx4060")


def test_force_still_allows_a_deliberate_override(monkeypatch):
    """An admin who confirms the warning can still select a degraded node."""
    _stub_installed(monkeypatch, {"rtx4060": [MODEL], "jagadeesh": ["qwen2.5:7b", MODEL]})
    assert ollama_nodes.set_primary_node("rtx4060", force=True) == "rtx4060"


def test_missing_models_reports_every_gap(monkeypatch):
    _stub_installed(monkeypatch, {"rtx4060": []})
    assert ollama_nodes.missing_models("rtx4060") == ["qwen2.5:7b", MODEL]


def test_a_missing_model_does_not_cool_a_node_down(monkeypatch):
    """The pool runs mixed inventories: rtx4060 carries the vision model but
    not the text one. The breaker is per node while a missing model is per
    model, so counting text misses as failures would cool rtx4060 down and then
    drop it from vision work too — losing the fast node for the exact job it is
    best at."""
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "2")
    ollama_nodes.set_primary_node("rtx4060", force=True)

    def fake(node_id, *, model, timeout=5, deep=True):
        has = {"rtx4060": {MODEL}, "jagadeesh": {MODEL, "qwen2.5:7b"}}.get(node_id, set())
        return {
            "id": node_id, "label": node_id, "primary": False, "status": "online",
            "endpoint_reachable": True, "model_available": model in has,
            "model_loaded": False, "response_time_ms": 5, "error": None,
            "breaker": ollama_nodes.breaker_state(node_id), "available": True,
        }

    monkeypatch.setattr(ollama_nodes, "node_health", fake)

    # Several text requests, each of which rtx4060 cannot serve.
    for _ in range(5):
        assert (
            ollama_nodes.select_available_node(model="qwen2.5:7b")["node_id"]
            == "jagadeesh"
        )

    assert ollama_nodes.in_cooldown("rtx4060") is False
    assert ollama_nodes.breaker_state("rtx4060")["consecutive_failures"] == 0

    # ...and vision still goes to the fast node afterwards.
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "rtx4060"


def test_an_unreachable_node_is_still_penalised(monkeypatch):
    """The relaxation above must not blunt the breaker for real outages."""
    monkeypatch.setenv("OLLAMA_NODE_FAILURE_THRESHOLD", "2")
    # rtx4060 must be first in line, or selection returns jagadeesh without
    # ever probing it and the breaker legitimately stays closed.
    ollama_nodes.set_primary_node("rtx4060", force=True)
    _stub_health(monkeypatch, {"jagadeesh"})
    for _ in range(2):
        ollama_nodes.select_available_node(model=MODEL)
    assert ollama_nodes.in_cooldown("rtx4060") is True


# ── pinning a model to a node ───────────────────────────────────────────────


def test_a_pinned_model_goes_to_its_node_ahead_of_the_primary(monkeypatch):
    """An 8 GB card cannot hold the text and vision models at once — measured
    on the RTX 4060, alternating reloaded on every request and took vision from
    2.4s to 11.5s. Promoting it therefore loses, because primary attracts both
    routes. Pinning vision to it keeps one model resident on each machine."""
    _stub_health(monkeypatch, {"rtx4060", "jagadeesh", "our_machine"})
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    ollama_nodes.set_model_node(MODEL, "rtx4060", force=True)

    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "rtx4060"
    # An unpinned model still follows the primary.
    assert (
        ollama_nodes.select_available_node(model="qwen2.5:7b")["node_id"] == "jagadeesh"
    )


def test_a_pin_is_a_preference_not_a_requirement(monkeypatch):
    """If the pinned node is down the work still runs; a pin must not become a
    single point of failure."""
    _stub_health(monkeypatch, {"jagadeesh", "our_machine"})
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    ollama_nodes.set_model_node(MODEL, "rtx4060", force=True)

    chosen = ollama_nodes.select_available_node(model=MODEL)
    assert chosen["node_id"] == "jagadeesh"
    assert any(a["node"] == "rtx4060" for a in chosen["attempts"])


def test_a_pin_survives_a_primary_change(monkeypatch, tmp_path):
    """Both live in the same state file; writing one must not drop the other."""
    ollama_nodes.set_model_node(MODEL, "rtx4060", force=True)
    ollama_nodes.set_primary_node("our_machine", force=True)

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["primary_node"] == "our_machine"
    assert saved["model_nodes"] == {MODEL: "rtx4060"}
    assert ollama_nodes.model_node_preference() == {MODEL: "rtx4060"}


def test_a_pin_can_be_removed(monkeypatch):
    _stub_health(monkeypatch, {"rtx4060", "jagadeesh"})
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    ollama_nodes.set_model_node(MODEL, "rtx4060", force=True)
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "rtx4060"

    ollama_nodes.set_model_node(MODEL, None)

    assert ollama_nodes.model_node_preference() == {}
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "jagadeesh"


def test_pinning_to_a_node_without_the_model_is_refused(monkeypatch):
    def fake(node_id, *, model, timeout=5, deep=True):
        return {
            "id": node_id, "label": node_id, "primary": False, "status": "online",
            "endpoint_reachable": True, "model_available": False,
            "model_loaded": False, "response_time_ms": 5, "error": None,
            "breaker": ollama_nodes.breaker_state(node_id), "available": True,
        }

    monkeypatch.setattr(ollama_nodes, "node_health", fake)
    with pytest.raises(ValueError, match="does not have"):
        ollama_nodes.set_model_node(MODEL, "rtx4060")


def test_pinning_to_an_unknown_node_is_refused():
    with pytest.raises(ValueError, match="Unknown Ollama node"):
        ollama_nodes.set_model_node(MODEL, "nonsense", force=True)


def test_an_env_default_can_seed_the_pin(monkeypatch):
    monkeypatch.setenv(
        "OLLAMA_MODEL_NODE_PREFERENCE", json.dumps({MODEL: "rtx4060"})
    )
    assert ollama_nodes.model_node_preference() == {MODEL: "rtx4060"}
    # Saved state wins over the environment default.
    ollama_nodes.set_model_node(MODEL, "jagadeesh", force=True)
    assert ollama_nodes.model_node_preference() == {MODEL: "jagadeesh"}


def test_a_pin_to_a_node_that_no_longer_exists_is_ignored(monkeypatch):
    monkeypatch.setenv(
        "OLLAMA_MODEL_NODE_PREFERENCE", json.dumps({MODEL: "a-retired-node"})
    )
    assert ollama_nodes.model_node_preference() == {}
    _stub_health(monkeypatch, {"jagadeesh"})
    ollama_nodes.set_primary_node("jagadeesh", force=True)
    assert ollama_nodes.select_available_node(model=MODEL)["node_id"] == "jagadeesh"
