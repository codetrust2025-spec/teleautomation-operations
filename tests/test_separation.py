import importlib

import main


def route_paths():
    from core.dashboard_auth_vps import _api_route_paths

    return set(_api_route_paths(main.app))


def test_business_owns_core_routes():
    paths = route_paths()
    assert "/candidates" in paths
    assert "/data-room" in paths
    assert "/api/ai-recruitment/config" in paths
    assert "/internal/v1/opportunities" in paths


def test_messaging_implementations_are_not_present():
    for module in ("core.telegram_client", "services.whatsapp_bsp", "messaging.queue_backend"):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"Messaging module leaked into Business: {module}")


def test_handler_salary_implementation_exists():
    from features import handler_salaries
    assert callable(handler_salaries.total_salary_owed)


def test_internal_contract_uses_service_auth_with_dashboard_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from features import data_room_store, service_inbox

    monkeypatch.setenv("DASHBOARD_PASSWORD", "dashboard-fixture")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "service-fixture")
    monkeypatch.setattr(service_inbox, "_PATH", str(tmp_path / "inbox.json"))
    monkeypatch.setattr(data_room_store, "_FILE", str(tmp_path / "opportunities.json"))
    client = TestClient(main.app)
    assert client.post("/internal/v1/opportunities", json={}).status_code == 401
    response = client.post(
        "/internal/v1/opportunities",
        json={"slot": "account1", "user_id": 1, "name": "Fixture", "summary": "sanitized"},
        headers={"X-Internal-Service-Token": "service-fixture", "X-Idempotency-Key": "auth-test"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
