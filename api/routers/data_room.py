"""Operations Data Room routes."""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from core.operations_api_helpers import (
    is_data_room_admin as _data_room_admin_only,
    require_admin as _require_fleet_admin,
)

router = APIRouter()

@router.get("/data-room")
async def data_room_list(
    status: str | None = Query(default=None),
    opportunity_type: str | None = Query(default=None),
    query: str | None = Query(default=None),
):
    from features import data_room_store

    rows = data_room_store.list_opportunities(
        status=status,
        opportunity_type=opportunity_type,
        query=query,
    )
    return {
        "status": "ok",
        "opportunities": rows,
        "count": len(rows),
        "stats": data_room_store.stats_summary(),
        # The offer-letter folder is a live Drive location holding candidates'
        # signed offers. It was a literal in the dashboard source, which a
        # public repository then published to anyone who read the file. It is
        # configuration now, served to an authenticated dashboard session and to
        # nobody else; an unset value simply disables the folder link.
        "offer_folder_url": os.getenv("OFFER_LETTER_FOLDER_URL", ""),
    }
@router.get("/data-room/stats")
async def data_room_stats():
    from features import data_room_store

    return {"status": "ok", "stats": data_room_store.stats_summary()}
@router.get("/data-room/credentials")
async def data_room_credentials(request: Request):
    """Credentials section of the data room (admin only; not partner leads)."""
    from fastapi import HTTPException

    from features import data_room_credentials_store

    if not _data_room_admin_only(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    return {"status": "ok", "credentials": data_room_credentials_store.get_credentials()}


def _require_account_export_admin(request: Request) -> dict:
    """The admin, by role. The Data Room's own gate admits every signed-in
    operator (handlers too); taking every account out at once is the admin's."""
    from core import dashboard_auth_vps as auth
    from core.dashboard_access import operator_profile

    profile = operator_profile(request)
    if not auth.is_payroll_admin_profile(profile):
        raise HTTPException(status_code=403, detail="Only the admin can download all accounts")
    return profile


def _record_account_export(request: Request, profile: dict, *, fmt: str, count: int, with_passwords: bool) -> None:
    """Who took the accounts out, when, how many and whether with passwords -- never what was in them."""
    from core import recruitment_mail_store

    recruitment_mail_store.audit(
        actor=str(profile.get("username") or ""), role=str(profile.get("role") or ""),
        action="data_room_accounts_export_with_passwords" if with_passwords else "data_room_accounts_export",
        new={"format": fmt, "accounts": count, "with_passwords": with_passwords},
        source_ip=request.client.host if request.client else None,
    )


def _export_response(content: bytes, fmt: str, name: str):
    from fastapi.responses import Response
    from features import data_room_account_export as export

    return Response(
        content=content,
        media_type=export.MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


@router.get("/data-room/credentials/service-accounts/export")
async def data_room_accounts_export(request: Request, format: str = Query(default="xlsx")):
    """Every Data Room account -- name, type, username, status -- as .xlsx, .csv or .txt. No passwords."""
    import logging

    from features import data_room_account_export as export
    from features import data_room_credentials_store

    profile = _require_account_export_admin(request)
    fmt = (format or "").strip().lower()
    if fmt not in export.FORMATS:
        raise HTTPException(status_code=400, detail="format must be xlsx, csv or txt")
    rows = export.account_rows(data_room_credentials_store.get_credentials().get("service_accounts") or [])
    try:
        _record_account_export(request, profile, fmt=fmt, count=len(rows), with_passwords=False)
    except Exception:  # noqa: BLE001 - the record is wanted, not a condition of a password-free export
        logging.getLogger(__name__).exception("Could not record a Data Room accounts export")
    return _export_response(export.render(rows, fmt), fmt, export.filename(fmt))


@router.post("/data-room/credentials/service-accounts/export-with-passwords")
async def data_room_accounts_export_with_passwords(request: Request, body: dict | None = None):
    """Every account with its password, only as an encrypted Excel workbook, only after the admin signs in again.

    Body: {"admin_password": the admin's own sign-in password, "file_password": the password the workbook is
    encrypted with}. Neither is stored or logged. Five wrong sign-in passwords lock this export for 15 minutes.
    """
    from core import dashboard_auth_vps as auth
    from features import data_room_account_export as export
    from features import data_room_credentials_store

    profile = _require_account_export_admin(request)
    if not auth.auth_enabled():
        raise HTTPException(status_code=403, detail="Passwords can only be exported when dashboard sign-in is enabled")
    payload = body or {}
    admin_password = str(payload.get("admin_password") or "")
    file_password = str(payload.get("file_password") or "")
    key = str(profile.get("username") or "admin")
    if export.reauth_limiter.blocked(key):
        raise HTTPException(status_code=429, detail="Too many wrong passwords. Try again in 15 minutes.")
    if not admin_password or not auth.admin_password_matches(admin_password):
        export.reauth_limiter.failed(key)
        # 403, not 401: the dashboard treats any 401 as an ended session and signs the admin out.
        raise HTTPException(status_code=403, detail="Your sign-in password is not correct")
    export.reauth_limiter.succeeded(key)
    problem = export.file_password_problem(file_password, admin_password)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    rows = export.account_rows(data_room_credentials_store.get_credentials().get("service_accounts") or [], with_passwords=True)
    try:
        _record_account_export(request, profile, fmt="xlsx", count=len(rows), with_passwords=True)
    except Exception as exc:  # noqa: BLE001 - passwords never leave unrecorded
        raise HTTPException(status_code=503, detail="The export could not be recorded, so it was not made. Try again.") from exc
    content = export.encrypt_xlsx(export.render(rows, "xlsx", with_passwords=True), file_password)
    return _export_response(content, "xlsx", export.filename("xlsx", with_passwords=True))
@router.patch("/data-room/credentials/handlers/{username}")
async def data_room_update_handler(username: str, body: dict, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    updated, err = creds.update_handler_login(username, body or {})
    if err:
        return {"status": "error", "message": err}
    if not updated:
        return {"status": "error", "message": "Handler not found"}
    return {"status": "ok", "credentials": updated}
@router.post("/data-room/credentials/handlers")
async def data_room_create_handler(body: dict, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    updated, err = creds.create_handler_login(body or {})
    if err:
        return {"status": "error", "message": err}
    return {"status": "ok", "credentials": updated}
@router.delete("/data-room/credentials/handlers/{username}")
async def data_room_delete_handler(username: str, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    updated, err = creds.delete_handler_login(username)
    if err:
        return {"status": "error", "message": err}
    return {"status": "ok", "credentials": updated}
@router.patch("/data-room/credentials/vault/{section}/{item_id}")
async def data_room_update_vault_item(section: str, item_id: str, body: dict, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    updated = creds.update_vault_item(section, item_id, body or {})
    if not updated:
        return {"status": "error", "message": "Vault entry not found"}
    return {"status": "ok", "credentials": updated}
@router.post("/data-room/credentials/vault/{section}")
async def data_room_create_vault_item(section: str, body: dict, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    updated, err = creds.create_vault_item(section, body or {})
    if err:
        return {"status": "error", "message": err}
    return {"status": "ok", "credentials": updated}
@router.delete("/data-room/credentials/vault/{section}/{item_id}")
async def data_room_delete_vault_item(section: str, item_id: str, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    updated = creds.delete_vault_item(section, item_id)
    if not updated:
        return {"status": "error", "message": "Vault entry not found"}
    return {"status": "ok", "credentials": updated}
@router.post("/data-room/service-accounts/{item_id}/image")
async def data_room_service_account_image_upload(
    item_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """Store one service account's screenshot on the data volume.

    Only a reference is written to the vault row; the bytes never enter
    `credentials.json`, which is read whole on every Data Room request.
    """
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    try:
        raw = await file.read()
        row = creds.save_service_account_image(item_id, raw, file.filename or "")
    except FileNotFoundError as exc:
        return {"status": "error", "message": str(exc)}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "service_account": row}


@router.get("/data-room/service-accounts/{item_id}/image")
async def data_room_service_account_image(item_id: str, request: Request):
    from fastapi import HTTPException

    from features import data_room_credentials_store as creds

    if not _data_room_admin_only(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        path, media_type, row = creds.resolve_service_account_image(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    name = str(row.get("image_filename") or f"{item_id}.png").replace('"', "")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@router.delete("/data-room/service-accounts/{item_id}/image")
async def data_room_service_account_image_delete(item_id: str, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    row = creds.delete_service_account_image(item_id)
    if row is None:
        return {"status": "error", "message": "Service account not found"}
    return {"status": "ok", "service_account": row}


@router.get("/data-room/offer-letters/{item_id}/preview")
async def data_room_offer_letter_preview(item_id: str, request: Request):
    from fastapi import HTTPException

    from features import data_room_credentials_store as creds

    if not _data_room_admin_only(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        path, row = creds.resolve_offer_letter_pdf(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    name = (row.get("filename") or row.get("id") or "offer-letter.pdf").replace('"', "")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=name,
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )
@router.get("/data-room/offer-letters/{item_id}/download")
async def data_room_offer_letter_download(item_id: str, request: Request):
    from fastapi import HTTPException

    from features import data_room_credentials_store as creds

    if not _data_room_admin_only(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        path, row = creds.resolve_offer_letter_pdf(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    name = (row.get("filename") or row.get("id") or "offer-letter.pdf").replace('"', "")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=name,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
@router.post("/data-room/offer-letters/{item_id}/upload")
async def data_room_offer_letter_upload(
    item_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    try:
        raw = await file.read()
        row = creds.save_offer_letter_pdf(item_id, raw)
    except FileNotFoundError as exc:
        return {"status": "error", "message": str(exc)}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "offer_letter": row}
@router.post("/data-room/offer-letters/upload-analyze")
async def data_room_offer_letter_upload_analyze(
    request: Request,
    file: UploadFile = File(...),
):
    """Save a new offer-letter PDF and return editable extracted metadata."""
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    try:
        raw = await file.read()
        row = creds.create_offer_letter_from_pdf(file.filename or "offer-letter.pdf", raw)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "status": "ok",
        "offer_letter": row,
        "message": "PDF saved and fields auto-filled. Review the values, then save.",
    }
@router.get("/data-room/{oid}")
async def data_room_get(oid: str):
    from features import data_room_store

    row = data_room_store.get_opportunity(oid)
    if not row or not data_room_store._is_partner_opportunity(row):
        return {"status": "error", "message": "Opportunity not found"}
    return {"status": "ok", "opportunity": row}
@router.post("/data-room", dependencies=[Depends(_require_fleet_admin)])
async def data_room_create(body: dict):
    from features import data_room_store

    summary = (body.get("summary") or body.get("name") or "").strip()
    if not summary and not (body.get("name") or "").strip():
        return {"status": "error", "message": "Name or summary is required"}
    row = data_room_store.create_opportunity(body or {})
    return {"status": "ok", "opportunity": row}
@router.patch("/data-room/{oid}", dependencies=[Depends(_require_fleet_admin)])
async def data_room_update(oid: str, body: dict):
    from features import data_room_store

    row = data_room_store.update_opportunity(oid, body or {})
    if not row:
        return {"status": "error", "message": "Opportunity not found"}
    return {"status": "ok", "opportunity": row}
@router.delete("/data-room/{oid}", dependencies=[Depends(_require_fleet_admin)])
async def data_room_delete(oid: str):
    from features import data_room_store

    ok = data_room_store.delete_opportunity(oid)
    if not ok:
        return {"status": "error", "message": "Opportunity not found"}
    return {"status": "ok"}
