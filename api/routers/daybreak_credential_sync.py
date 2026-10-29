"""Data Room routes for delivering Naukri logins to Daybreak's dedicated workers through Daybreak's own queue.

Admin only, except the public half of Operations' intake key. They show statuses, reasons and worker fingerprints --
never a password, sealed or not. Operations itself calls Daybreak, signed with its intake key.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _no_store(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


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


@router.get("/api/integrations/daybreak/intake-key")
async def daybreak_intake_key():
    """The PUBLIC half of Operations' intake key, which a Daybreak release pins so it accepts Operations' signed
    requests. Public on purpose: it proves nothing by itself and is needed to build that release."""
    from features import daybreak_credential_sync as sync

    return _admin_call(lambda: {"public_key": sync.intake_public_key(), "algorithm": "Ed25519"})


@router.post("/data-room/daybreak/workers/{key_id}/approve")
async def daybreak_approve_worker(key_id: str, request: Request, body: dict | None = None):
    from features import daybreak_credential_sync as sync

    profile = _require_admin(request)
    return _admin_call(lambda: {"worker": sync.approve_worker(
        key_id, typed_fingerprint=str((body or {}).get("fingerprint") or ""), actor=str(profile.get("username") or ""))})


@router.post("/data-room/daybreak/workers/{key_id}/revoke")
async def daybreak_revoke_worker(key_id: str, request: Request):
    from features import daybreak_credential_sync as sync

    profile = _require_admin(request)
    return _admin_call(lambda: {"worker": sync.revoke_worker(key_id, actor=str(profile.get("username") or ""))})


def _ids(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 50 or not all(isinstance(v, str) and 0 < len(v) <= 200 for v in value):
        raise HTTPException(status_code=400, detail="Account ids must be a list")
    return value


@router.post("/data-room/daybreak/deliveries")
async def daybreak_deliver(request: Request, body: dict | None = None):
    from features import daybreak_credential_sync as sync

    profile = _require_admin(request)
    payload = body or {}
    account_ids, create_ids = _ids(payload.get("account_ids")), _ids(payload.get("create_ids"))
    if not (account_ids or create_ids):
        raise HTTPException(status_code=400, detail="Choose at least one account")
    return _admin_call(lambda: sync.deliver(account_ids, actor=str(profile.get("username") or ""), create_ids=create_ids,
                                            consent_confirmed=payload.get("consent_confirmed") is True))
