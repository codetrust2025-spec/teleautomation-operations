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


@router.get("/data-room/credentials/service-accounts/{item_id}/password")
async def data_room_service_account_password(item_id: str, request: Request):
    """One account's password, for Show, Copy and Edit. The account list never carries passwords; each one is read
    here, on purpose, by the operators who can open the Data Room, and the read is recorded."""
    import logging

    from fastapi.responses import JSONResponse

    from core.dashboard_access import operator_profile
    from features import data_room_credentials_store as creds

    if not _data_room_admin_only(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        password = creds.service_account_password(item_id)
    except Exception as exc:  # noqa: BLE001 - a token the key cannot open
        raise HTTPException(status_code=409, detail="This password cannot be read with the current key") from exc
    if password is None:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        from core import recruitment_mail_store

        profile = operator_profile(request)
        recruitment_mail_store.audit(
            actor=str(profile.get("username") or ""), role=str(profile.get("role") or ""),
            action="data_room_password_viewed", source_id=str(item_id),
            source_ip=request.client.host if request.client else None,
        )
    except Exception:  # noqa: BLE001 - the record is wanted, not a condition of using the Data Room
        logging.getLogger(__name__).exception("Could not record a Data Room password read")
    return JSONResponse({"status": "ok", "password": password}, headers={"Cache-Control": "no-store"})


def _require_account_export_admin(request: Request) -> dict:
    """A signed-in admin, by role. The Data Room's own gate admits every
    signed-in operator (handlers too); taking every account out at once,
    passwords included, is the admin's."""
    from core import dashboard_auth_vps as auth
    from core.dashboard_access import operator_profile

    profile = operator_profile(request)
    if not auth.is_payroll_admin_profile(profile):
        raise HTTPException(status_code=403, detail="Only the admin can download all accounts")
    return profile


def _record_account_export(request: Request, profile: dict, *, fmt: str, rows: list[dict]) -> None:
    """Who took the credentials out, when, in which format and how many -- never what was in them."""
    from core import recruitment_mail_store
    from features import data_room_account_export as export

    logins = sum(1 for row in rows if row.get("password") == export.HASHED)
    recruitment_mail_store.audit(
        actor=str(profile.get("username") or ""), role=str(profile.get("role") or ""),
        action="data_room_accounts_export",
        new={"format": fmt, "credentials": len(rows), "dashboard_logins": logins,
             "service_accounts": len(rows) - logins, "with_passwords": True},
        source_ip=request.client.host if request.client else None,
    )


@router.get("/data-room/credentials/service-accounts/export")
async def data_room_accounts_export(request: Request, format: str = Query(default="xlsx")):
    """Every Data Room credential -- Credential Name, Type, Username, Password -- as .xlsx, .csv or .txt: the
    dashboard logins (their passwords "Not available (hashed)") and the service accounts (passwords exactly as
    stored). The admin's only; every download is recorded, and one that cannot be recorded is not made. The file is
    built in memory and sent: nothing is written to the server, and nothing may cache it."""
    from fastapi.responses import Response

    from features import data_room_account_export as export
    from features import data_room_credentials_store

    profile = _require_account_export_admin(request)
    fmt = (format or "").strip().lower()
    if fmt not in export.FORMATS:
        raise HTTPException(status_code=400, detail="format must be xlsx, csv or txt")
    # The logins as the table has them: get_credentials strips every password field from the admin and the handlers.
    listed = data_room_credentials_store.get_credentials()
    rows = export.credential_rows(listed.get("admin"), listed.get("handlers"),
                                  data_room_credentials_store.service_accounts_with_passwords())
    try:
        _record_account_export(request, profile, fmt=fmt, rows=rows)
    except Exception as exc:  # noqa: BLE001 - passwords never leave unrecorded
        raise HTTPException(status_code=503, detail="The download could not be recorded, so it was not made. Try again.") from exc
    return Response(
        content=export.render(rows, fmt),
        media_type=export.MEDIA_TYPES[fmt],
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename(fmt)}"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
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
    if section == "service_accounts":
        # Checked fields only: the service must be one of the three, and a password is encrypted, never stored as typed.
        updated, err = creds.update_service_account(item_id, body or {})
        if err:
            return {"status": "error", "message": "Vault entry not found" if err == "Not found" else err}
        return {"status": "ok", "credentials": updated}
    updated = creds.update_vault_item(section, item_id, body or {})
    if not updated:
        return {"status": "error", "message": "Vault entry not found"}
    return {"status": "ok", "credentials": updated}
@router.post("/data-room/credentials/vault/{section}")
async def data_room_create_vault_item(section: str, body: dict, request: Request):
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    if section == "service_accounts":
        updated, err = creds.create_service_account(body or {})
    else:
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


def _offer_letter_file_response(item_id: str, request: Request, disposition: str):
    """The offer letter's file exactly as uploaded -- PDF or image, typed from its bytes -- for the preview (inline)
    or the download (attachment). Never cached: these are candidates' documents."""
    from urllib.parse import quote

    from fastapi import HTTPException

    from features import data_room_credentials_store as creds

    if not _data_room_admin_only(request):
        raise HTTPException(status_code=403, detail="Admin access required")
    try:
        path, media_type, row = creds.resolve_offer_letter_file(item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    name = str(row.get("filename") or row.get("id") or "offer-letter").replace('"', "").replace("\\", "")
    extension = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[media_type]
    if not name.lower().endswith((extension, ".jpeg") if extension == ".jpg" else extension):
        name = f"{os.path.splitext(name)[0] or 'offer-letter'}{extension}"
    # A header is Latin-1: an ASCII name for every browser, the real one for those that read filename*.
    plain = name.encode("ascii", "ignore").decode("ascii").strip() or f"offer-letter{extension}"
    # What the file holds, read from it, so the preview can say "1 of 4" and notice if it shows fewer.
    facts = row.get("file") or creds.offer_file_facts(row)
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Offer-Page-Count": "" if facts.get("pages") is None else str(facts.get("pages")),
            "X-Offer-Password-Protected": "1" if facts.get("password_protected") else "0",
            "Access-Control-Expose-Headers": "X-Offer-Page-Count, X-Offer-Password-Protected",
        },
    )


@router.get("/data-room/offer-letters/{item_id}/preview")
async def data_room_offer_letter_preview(item_id: str, request: Request):
    return _offer_letter_file_response(item_id, request, "inline")


@router.get("/data-room/offer-letters/{item_id}/download")
async def data_room_offer_letter_download(item_id: str, request: Request):
    return _offer_letter_file_response(item_id, request, "attachment")



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
        row = creds.save_offer_letter_file(item_id, raw, file.filename or "")
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
    """Save a new offer letter (PDF or image) and return editable extracted metadata."""
    from core.dashboard_access import require_fleet_admin
    from features import data_room_credentials_store as creds

    require_fleet_admin(request)
    try:
        raw = await file.read()
        row = creds.create_offer_letter_from_file(file.filename or "offer-letter.pdf", raw)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}
    return {
        "status": "ok",
        "offer_letter": row,
        "message": "File saved and fields auto-filled. Review the values, then save.",
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
