"""Dashboard auth HTTP routes + middleware (TeleAutomation login & admin gate)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
import os

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core import dashboard_auth as auth


logger = logging.getLogger(__name__)


async def _record_auth_activity(profile: dict[str, Any], activity_type: str) -> None:
    try:
        from features.attendance_eligibility import record_auth_activity

        await asyncio.to_thread(record_auth_activity, profile, activity_type)
    except Exception:
        # Authentication remains available if the audit database is temporarily
        # unavailable. The failure is visible without exposing credential data.
        logger.exception("operations auth activity audit failed type=%s", activity_type)


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


def _set_session_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=os.getenv("DASHBOARD_COOKIE_SECURE", "true").lower() not in {"0", "false", "no"},
        samesite="lax",
        max_age=auth.SESSION_TTL_SEC,
        path="/",
    )


def install_dashboard_auth(app: FastAPI) -> None:
    """Register /auth/* routes and optional session middleware."""

    @app.get("/auth/status")
    async def auth_status(request: Request):
        if not auth.auth_enabled():
            return {
                "enabled": False,
                "authenticated": True,
                "username": "dev",
                "role": "admin",
                "reference": None,
                "display_name": "Dev",
                "account_id": "admin:dev",
            }
        profile = auth.operator_profile_from_cookies(dict(request.cookies))
        username = profile.get("username")
        return {
            "enabled": True,
            "authenticated": bool(username),
            "username": username,
            "role": profile.get("role") or "admin",
            "reference": profile.get("reference"),
            "display_name": profile.get("display_name"),
            "account_id": profile.get("account_id"),
        }

    @app.post("/auth/login")
    async def auth_login(request: Request, background_tasks: BackgroundTasks, body: dict | None = None):
        payload = body or {}
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        if not auth.auth_enabled():
            return {"username": "dev", "role": "admin", "reference": None}

        profile = auth.resolve_operator_login(username, password)
        if not profile:
            return _json({"detail": "Invalid username or password"}, status=401)

        token = auth.create_session_token(
            profile["username"],
            role=profile.get("role") or "admin",
            reference=profile.get("reference"),
            display_name=profile.get("display_name"),
            account_id=profile.get("account_id"),
        )
        session_profile = auth.parse_session_token(token) or profile
        # Auth responses must never queue behind an optional attendance-audit
        # database write. Starlette runs this task after the response is sent.
        background_tasks.add_task(_record_auth_activity, session_profile, "LOGIN")
        resp = _json(
            {
                "status": "ok",
                "username": profile["username"],
                "role": profile.get("role") or "admin",
                "reference": profile.get("reference"),
                "display_name": profile.get("display_name"),
                "account_id": profile.get("account_id"),
            }
        )
        _set_session_cookie(resp, token)
        return resp

    @app.post("/auth/logout")
    async def auth_logout(request: Request, background_tasks: BackgroundTasks):
        profile = auth.operator_profile_from_cookies(dict(request.cookies))
        if profile.get("username"):
            background_tasks.add_task(_record_auth_activity, profile, "LOGOUT")
        resp = _json({"status": "ok"})
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    @app.post("/auth/verify-admin")
    async def auth_verify_admin(body: dict | None = None):
        """Re-check admin password for sensitive UI (handler payouts, etc.)."""
        payload = body or {}
        password = str(payload.get("password") or "")
        if not auth.auth_enabled():
            return {"status": "ok", "verified": True}

        expected_user, expected_pass = auth.get_credentials()
        if not expected_pass:
            return {"status": "ok", "verified": True}
        if not password:
            return _json({"detail": "Password required"}, status=400)
        import secrets

        if secrets.compare_digest(password, expected_pass):
            return {"status": "ok", "verified": True}
        return _json({"detail": "Incorrect password"}, status=401)

    @app.post("/auth/reset-password")
    async def auth_reset_password(body: dict | None = None):
        """Handler self-service password reset (username + referrer name)."""
        if not auth.auth_enabled():
            return _json({"detail": "Auth is disabled"}, status=400)
        payload = body or {}
        username = str(payload.get("username") or "").strip()
        reference = str(payload.get("reference") or "").strip()
        new_password = str(payload.get("new_password") or "")
        err = auth.handler_self_reset_password(username, reference, new_password)
        if err:
            return _json({"detail": err}, status=400)
        return {"status": "ok"}

    @app.post("/auth/change-password")
    async def auth_change_password(request: Request, body: dict | None = None):
        """Logged-in operator changes their own password."""
        if not auth.auth_enabled():
            return {"status": "ok"}
        profile = auth.operator_profile_from_cookies(dict(request.cookies))
        username = profile.get("username")
        if not username:
            return _json({"detail": "Authentication required"}, status=401)
        payload = body or {}
        current_password = str(payload.get("current_password") or "")
        new_password = str(payload.get("new_password") or "")
        err = auth.change_operator_password(username, current_password, new_password)
        if err:
            return _json({"detail": err}, status=400)
        return {"status": "ok"}

    class DashboardAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            from core.dashboard_access import is_internal_service_authorized, is_ops_request_authorized

            # Authorise on the ASGI scope path -- the exact path the router
            # dispatches on -- not request.url.path. Starlette rebuilds
            # request.url from the Host header, so a header such as
            # "Host: ops.example?" or "...#" moves the path boundary during
            # re-parsing: request.url.path then reads "/" (public) while the
            # router still runs the real protected route (CVE-2026-48710). The
            # scope path and the dispatch can never disagree, so this closes
            # that bypass regardless of the Starlette version in play. For a
            # well-formed request the two are identical, so nothing else moves.
            path = request.scope.get("path") or ""
            if not auth.auth_enabled():
                return await call_next(request)
            if auth.is_public_path(path):
                return await call_next(request)
            if path.startswith("/internal/") and is_internal_service_authorized(request):
                return await call_next(request)
            if is_ops_request_authorized(request) and path.startswith("/ai/smart-reply/"):
                return await call_next(request)
            if (
                is_ops_request_authorized(request)
                and request.method.upper() == "POST"
                and path.startswith("/inbox/")
                and "/sync/" in path
            ):
                return await call_next(request)
            if auth.is_spa_shell_request(request.method, path):
                return await call_next(request)
            if auth.username_from_request_cookies(dict(request.cookies)):
                return await call_next(request)
            return _json({"detail": "Authentication required"}, status=401)

    app.add_middleware(DashboardAuthMiddleware)
