"""API-root discovery must see routes added with include_router (11 Oct).

FastAPI 0.142+ keeps an included router as one wrapper entry in `app.routes`
instead of copying its routes in. register_api_roots kept only APIRoute
entries, so on the new FastAPI it would have discovered the roots declared on
`app` itself and none from the included routers -- /referrers, /attendance,
/internal ... would have fallen back to "unknown first segment", which the
auth middleware treats as a client-side page. This pinned FastAPI at 0.115 and
Starlette at 0.38.6, which carry published advisories.
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as auth


def test_a_root_only_an_included_router_declares_is_discovered():
    router = APIRouter(prefix="/zz-included-only")

    @router.get("/items")
    def items():  # pragma: no cover - never called
        return []

    app = FastAPI()

    @app.get("/zz-declared-on-app")
    def declared():  # pragma: no cover - never called
        return {}

    app.include_router(router)
    roots = auth.register_api_roots(app)
    assert {"zz-included-only", "zz-declared-on-app"} <= roots
    assert "/zz-included-only/items" in auth._api_route_paths(app)


def test_the_real_app_discovers_the_roots_that_exist_only_on_included_routers():
    import main

    roots = auth.register_api_roots(main.app)
    # Not in the static list: only discovery protects these.
    assert {"attendance", "company-expenses", "internal", "referrer-payment-accounts", "referrers"} <= roots
    assert {"candidates", "data-room", "handler-expenses"} <= roots


def test_the_startup_hook_still_runs_on_the_upgraded_framework(monkeypatch):
    import main

    asked = []
    real_getenv = main.os.getenv

    def getenv(key, default=None):
        if key == "TELEAUTOMATION_SAFE_UI_MODE":
            asked.append(key)
            return "true"  # return before any loop or database is started
        return real_getenv(key, default)

    monkeypatch.setattr(main.os, "getenv", getenv)
    with TestClient(main.app):
        pass
    assert asked, "the app's startup handler did not run"
