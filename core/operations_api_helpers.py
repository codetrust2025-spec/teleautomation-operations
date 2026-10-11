"""Small Operations API helpers shared by route modules."""

from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse


def require_admin(request: Request) -> None:
    from core.dashboard_access import require_fleet_admin

    require_fleet_admin(request)


def require_payroll_admin(request: Request) -> None:
    """Strict administrator role for salary-changing operations only."""
    from core import dashboard_auth_vps as auth

    profile = auth.operator_profile_from_cookies(dict(request.cookies))
    if not auth.is_payroll_admin_profile(profile):
        raise HTTPException(status_code=403, detail="Payroll administrator access required")


def viewer_reference(request: Request) -> str | None:
    from core import dashboard_auth_vps as auth
    from core.dashboard_access import operator_profile

    return auth.scoped_reference(operator_profile(request))


def operations_actor(request: Request) -> str:
    from core.dashboard_access import operator_profile

    profile = operator_profile(request)
    return (profile.get("reference") or profile.get("username") or "dashboard").strip()[:120]


def is_data_room_admin(request: Request) -> bool:
    from core import dashboard_auth_vps as auth

    return auth.is_admin_profile(auth.operator_profile_from_cookies(dict(request.cookies)))


_EXTENSION_FOR_MIME = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
_HAS_EXTENSION = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def download_filename(entry: dict, fallback: str) -> str:
    """The name a stored file is saved under: the name it was uploaded with,
    without any folder part or control characters, and with an extension that
    matches its type when the uploaded name has none (so it opens as what it is)."""
    name = str(entry.get("original_name") or entry.get("filename") or "")
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip().strip(".")
    name = name or fallback
    if not _HAS_EXTENSION.search(name):
        stored = os.path.splitext(str(entry.get("filename") or ""))[1]
        extension = _EXTENSION_FOR_MIME.get(str(entry.get("mime_type") or "").lower().strip())
        if not extension and _HAS_EXTENSION.search(stored):
            extension = stored
        name += extension or ""
    return name


def stored_file_response(
    path: str, entry: dict, *, fallback: str, inline: bool = False
) -> FileResponse:
    """A stored resume or proof, saved (attachment) or shown in the browser
    (inline) under its own name. Starlette writes the header, so a name with
    non-ASCII characters or quotes is encoded rather than breaking it."""
    return FileResponse(
        path,
        media_type=entry.get("mime_type") or "application/octet-stream",
        filename=download_filename(entry, fallback),
        content_disposition_type="inline" if inline else "attachment",
    )


def resume_file_response(path: str, entry: dict, *, inline: bool = False) -> FileResponse:
    return stored_file_response(path, entry, fallback="resume", inline=inline)


def resolve_resume_hit(candidate_id: str, resume_id: str):
    from features import candidate_store

    hit = candidate_store.get_resume(candidate_id, resume_id)
    if hit is not None:
        return hit
    try:
        folders = [
            os.path.join(candidate_store.RESUMES_DIR, str(row.get("id")))
            for row in candidate_store._resume_owner_rows(candidate_id)
        ]
    except Exception:
        folders = []
    folders.append(os.path.join(candidate_store.RESUMES_DIR, candidate_id))

    matches = []
    for folder in dict.fromkeys(folders):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if name.startswith(resume_id) and os.path.isfile(full):
                matches.append(full)
    if len(matches) != 1:
        return None
    path = matches[0]
    filename = os.path.basename(path)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = {
        "pdf": "application/pdf",
        "doc": "application/msword",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "txt": "text/plain",
    }.get(extension, "application/octet-stream")
    return path, {
        "id": resume_id,
        "filename": filename,
        "original_name": filename,
        "mime_type": mime,
    }
