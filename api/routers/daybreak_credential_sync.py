"""Routes for delivering Data Room Naukri logins to the computer that runs Daybreak's browser.

Two audiences. The computer (Praveen's companion today) calls /api/integrations/daybreak/executor/* with Daybreak's
bearer token and its own Ed25519 signature; it has no dashboard session. The admin calls /data-room/daybreak/* from the
Data Room, which sees statuses and reasons -- never a password, sealed or not.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from api.routers.daybreak_integration import _authorized

router = APIRouter()

_MAX_BODY = 256 * 1024


def _no_store(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _signed_headers(request: Request) -> dict:
    return {
        "executor": request.headers.get("x-daybreak-executor"),
        "timestamp": request.headers.get("x-daybreak-timestamp"),
        "nonce": request.headers.get("x-daybreak-nonce"),
        "signature": request.headers.get("x-daybreak-signature"),
    }


async def _executor_call(request: Request, authorization: str | None, handler):
    from features import daybreak_credential_sync as sync

    if not _authorized(authorization):
        raise HTTPException(status_code=401, detail="Daybreak authorization required")
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise HTTPException(status_code=413, detail="Request too large")
    try:
        result = handler(body, _signed_headers(request), method=request.method, path=request.scope.get("path") or "")
    except sync.SyncError as exc:
        return _no_store({"detail": exc.detail}, exc.status)
    return _no_store(result)


@router.post("/api/integrations/daybreak/executor/register")
async def executor_register(request: Request, authorization: str | None = Header(default=None)):
    from features import daybreak_credential_sync as sync

    return await _executor_call(request, authorization, sync.register_executor)


@router.post("/api/integrations/daybreak/executor/sync")
async def executor_sync(request: Request, authorization: str | None = Header(default=None)):
    from features import daybreak_credential_sync as sync

    return await _executor_call(request, authorization, sync.executor_sync)


def _require_admin(request: Request) -> dict:
    """The admin, by role: handlers can open the Data Room, but only the admin moves logins to another computer."""
    from core import dashboard_auth_vps as auth
    from core.dashboard_access import operator_profile

    profile = operator_profile(request)
    if not auth.is_payroll_admin_profile(profile):
        raise HTTPException(status_code=403, detail="Only the admin can manage Daybreak delivery")
    return profile


def _admin_call(fn):
    from features import daybreak_credential_sync as sync

    try:
        return _no_store({"status": "ok", **fn()})
    except sync.SyncError as exc:
        return _no_store({"detail": exc.detail}, exc.status)


@router.get("/data-room/daybreak")
async def daybreak_status(request: Request):
    from features import daybreak_credential_sync as sync

    _require_admin(request)
    return _admin_call(sync.status_view)


@router.post("/data-room/daybreak/executors/{executor_id}/approve")
async def daybreak_approve_executor(executor_id: str, request: Request, body: dict | None = None):
    from features import daybreak_credential_sync as sync

    profile = _require_admin(request)
    payload = body or {}
    return _admin_call(lambda: {"executor": sync.approve_executor(
        executor_id, typed_fingerprint=str(payload.get("fingerprint") or ""), actor=str(profile.get("username") or ""),
        cutover_confirmed=payload.get("cutover_confirmed") is True)})


@router.post("/data-room/daybreak/executors/{executor_id}/revoke")
async def daybreak_revoke_executor(executor_id: str, request: Request):
    from features import daybreak_credential_sync as sync

    profile = _require_admin(request)
    return _admin_call(lambda: {"executor": sync.revoke_executor(executor_id, actor=str(profile.get("username") or ""))})


def _ids(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 200 or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in value):
        raise HTTPException(status_code=400, detail="Account ids must be a list")
    return value


@router.post("/data-room/daybreak/deliveries")
async def daybreak_request_deliveries(request: Request, body: dict | None = None):
    from features import daybreak_credential_sync as sync

    profile = _require_admin(request)
    payload = body or {}
    account_ids, replace_ids, create_ids = _ids(payload.get("account_ids")), _ids(payload.get("replace_ids")), _ids(payload.get("create_ids"))
    verify_ids = _ids(payload.get("verify_ids"))
    if not (account_ids or replace_ids or create_ids or verify_ids):
        raise HTTPException(status_code=400, detail="Choose at least one account")
    return _admin_call(lambda: sync.request_deliveries(
        account_ids, actor=str(profile.get("username") or ""), replace_ids=replace_ids, create_ids=create_ids,
        consent_confirmed=payload.get("consent_confirmed") is True, verify_ids=verify_ids))
