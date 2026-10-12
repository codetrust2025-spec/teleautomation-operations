from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import daybreak_integration as integration
from core.ai_gateway import AIGatewayError


def client(monkeypatch):
    monkeypatch.setenv("DAYBREAK_HEADLINE_TOKEN", "d" * 48)
    app = FastAPI()
    app.include_router(integration.router)
    return TestClient(app)


def payload():
    return {
        "candidate_ref": "candidate-a",
        "facts": {
            "target_role": "React developer",
            "experience": "6 years",
            "skills": ["JavaScript", "React.js"],
            "profile_facts": "",
        },
        "max_length": 250,
    }


def test_daybreak_headline_requires_dedicated_bearer_token(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/api/integrations/daybreak/headline", json=payload()).status_code == 401
    assert c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer wrong"}, json=payload()).status_code == 401


def test_daybreak_headline_uses_shared_gateway_and_returns_selected_node(monkeypatch):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content='{"headline":"React developer with 6 years using JavaScript and React.js"}',
            node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=47,
        )

    c = client(monkeypatch)
    monkeypatch.setattr("core.ai_gateway.chat_structured", fake_chat)
    response = c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "headline": "React developer with 6 years using JavaScript and React.js",
        "node_id": "rtx4060", "node_label": "RTX 4060", "model": "qwen2.5:7b", "duration_ms": 47,
    }
    assert calls[0]["workload"] == "daybreak_headline"
    assert calls[0]["temperature"] == 0
    assert "candidate-a" not in calls[0]["messages"][1]["content"]


def test_daybreak_headline_fails_closed_for_node_or_model_failures(monkeypatch):
    c = client(monkeypatch)
    headers = {"Authorization": "Bearer " + "d" * 48}
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: (_ for _ in ()).throw(AIGatewayError("secret node detail", code="OLLAMA_CONNECTION_FAILED")))
    response = c.post("/api/integrations/daybreak/headline", headers=headers, json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "AI generation unavailable"}
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: SimpleNamespace(content='{"headline":"x"}', node_id="", node_label="", model="", duration_ms=0))
    response = c.post("/api/integrations/daybreak/headline", headers=headers, json=payload())
    assert response.status_code == 502
    assert response.json() == {"detail": "AI generation returned an invalid result"}


def test_daybreak_headline_rejects_extra_or_oversized_candidate_input(monkeypatch):
    c = client(monkeypatch)
    headers = {"Authorization": "Bearer " + "d" * 48}
    assert c.post("/api/integrations/daybreak/headline", headers=headers, json=payload() | {"unexpected": "no"}).status_code == 422
    bad = payload()
    bad["facts"]["skills"] = ["x" * 61]
    assert c.post("/api/integrations/daybreak/headline", headers=headers, json=bad).status_code == 422


def test_daybreak_machine_route_bypasses_dashboard_cookie_gate_only():
    """The route authenticates with its own bearer token, never a dashboard cookie."""
    from core import dashboard_auth_vps as dashboard_auth

    assert dashboard_auth.is_public_path("/api/integrations/daybreak/headline") is True
    assert dashboard_auth.is_public_path("/api/integrations/daybreak/other") is False

def test_daybreak_prompt_requires_grounded_terms():
    text = integration._prompt(integration.DaybreakHeadlineRequest.model_validate(payload()))
    assert "Every non-neutral word must appear verbatim in the approved facts" in text
    assert "experienced, strong, expertise" in text