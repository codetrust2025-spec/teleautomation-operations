"""Data room — dashboard credentials section (admin-only, separate from partner leads)."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from core.config import DATA_DIR

_FILE = os.path.join(DATA_DIR, "data_room", "credentials.json")
_lock = Lock()


def _default_site_url() -> str:
    return (os.environ.get("OPERATIONS_PUBLIC_URL") or "").strip().rstrip("/")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty() -> dict:
    return {
        "site_url": _default_site_url(),
        "vps_host": "",
        "admin": None,
        "handlers": [],
        "service_accounts": [],
        "prompts": [],
        "resources": [],
        "offer_letters": [],
        "interview_data": [],
        "updated_at": None,
    }


def _load() -> dict:
    with _lock:
        if not os.path.isfile(_FILE):
            return _empty()
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _empty()
            data.setdefault("handlers", [])
            data.setdefault("service_accounts", [])
            data.setdefault("prompts", [])
            data.setdefault("resources", [])
            data.setdefault("offer_letters", [])
            data.setdefault("interview_data", [])
            return data
        except (OSError, json.JSONDecodeError):
            return _empty()


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    data = dict(data)
    data["updated_at"] = _now_iso()
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


def _clean_list(rows: list | None) -> list[dict]:
    out: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append(dict(row))
    return out


_NON_COMPANY_MARKERS = (
    "whatsapp",
    "shared from",
    "shared in",
    "received via",
    "employment agreement",
    "named offer letter",
    "generic filename",
    "batch offer",
    "encrypted pdf",
    "verify candidate",
    "interview slots",
    "upload pdf",
    "handler ",
    "placed candidate",
    "promotions group",
    "wa doc",
    "latest one",
    "template or signed",
    "document from",
)


def _company_name_is_valid(name: str, notes: str = "") -> bool:
    value = str(name or "").strip()
    if not value:
        return False
    if value == str(notes or "").strip():
        return False
    if len(value) > 40:
        return False
    if any(sep in value for sep in (" / ", " — ", " · ", "...")):
        return False
    low = value.lower()
    return not any(marker in low for marker in _NON_COMPANY_MARKERS)


def _infer_offer_company(row: dict) -> str:
    blob = f"{row.get('filename') or ''} {row.get('id') or ''}"
    if re.search(r"luxoft\s+india", blob, re.I):
        return "Luxoft India"
    if re.search(r"luxoft", blob, re.I):
        return "Luxoft"
    filename = str(row.get("filename") or "")
    match = re.search(
        r"\b([A-Z][A-Za-z0-9&]+(?:\s+[A-Z][A-Za-z0-9&]+){0,2})\s+(?:India\s+)?(?:Offer(?:Letter)?|OfferLetters)\b",
        filename,
    )
    if match:
        candidate = match.group(1).strip()
        if _company_name_is_valid(candidate):
            return candidate
    return ""


def _infer_offer_source(notes: str, filename: str, candidate: str) -> str:
    blob = f"{notes} {filename} {candidate}".lower()
    if "whatsapp" in blob or "wa doc" in blob or "(wa)" in blob or "wa0062" in blob:
        return "WhatsApp"
    if "promotions" in blob:
        return "Promotions"
    if "interview slots" in blob:
        return "Interview slots"
    if "shared from" in blob or "document from" in blob:
        return "Shared"
    if "employment agreement" in blob or "template" in blob:
        return "Template"
    if "batch offer" in blob or "offerletters" in blob.replace("_", "").lower():
        return "Drive"
    if "drive" in blob:
        return "Drive"
    return ""


def _infer_offer_handler(notes: str, filename: str) -> str:
    match = re.search(r"handler\s+([A-Za-z][A-Za-z0-9._-]+)", notes, re.I)
    if match:
        return match.group(1)
    match = re.search(r"shared from\s+([A-Za-z][A-Za-z0-9._-]+)", notes, re.I)
    if match:
        return match.group(1)
    match = re.search(r"document from\s+([A-Za-z][A-Za-z0-9._-]+)", filename, re.I)
    if match:
        return match.group(1)
    return ""


def _migrate_offer_letter_rows(data: dict) -> bool:
    """One-time seed for company/source/handler; does not overwrite manual edits later."""
    if data.get("offer_letters_rows_migrated_v1"):
        return False
    changed = False
    rows = list(data.get("offer_letters") or [])
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        merged = dict(row)
        notes = str(merged.get("notes") or "").strip()
        current = str(merged.get("company_name") or "").strip()
        if current and not _company_name_is_valid(current, notes):
            merged["company_name"] = ""
            current = ""
            changed = True
        if not current:
            inferred = _infer_offer_company(merged)
            if inferred:
                merged["company_name"] = inferred
                changed = True
        if not str(merged.get("source") or "").strip():
            source = _infer_offer_source(
                notes,
                str(merged.get("filename") or ""),
                str(merged.get("candidate") or ""),
            )
            if source:
                merged["source"] = source
                changed = True
        if not str(merged.get("handler") or "").strip():
            handler = _infer_offer_handler(notes, str(merged.get("filename") or ""))
            if handler:
                merged["handler"] = handler
                changed = True
        rows[i] = merged
    data["offer_letters"] = rows
    data["offer_letters_rows_migrated_v1"] = True
    return True


def get_credentials() -> dict:
    data = _load()
    if _migrate_offer_letter_rows(data):
        _save(data)
    admin = data.get("admin")
    handlers = list(data.get("handlers") or [])
    service_accounts = _clean_list(data.get("service_accounts"))
    prompts = _clean_list(data.get("prompts"))
    resources = _clean_list(data.get("resources"))
    offer_letters = _clean_list(data.get("offer_letters"))
    interview_data = _clean_list(data.get("interview_data"))
    return {
        "site_url": (data.get("site_url") or "").strip(),
        "vps_host": (data.get("vps_host") or "").strip(),
        "admin": dict(admin) if isinstance(admin, dict) else None,
        # The stored password is a hash and of no use to a caller; withholding
        # it keeps the secret off the wire and out of anything that logs a
        # response body.
        "handlers": [
            {key: value for key, value in h.items() if key != "password"}
            for h in handlers if isinstance(h, dict)
        ],
        "service_accounts": service_accounts,
        "prompts": prompts,
        "resources": resources,
        "offer_letters": offer_letters,
        "interview_data": interview_data,
        "updated_at": data.get("updated_at"),
        "count": (1 if admin else 0) + len(handlers),
    }




def save_credentials(
    *,
    site_url: str,
    admin_username: str,
    admin_password: str,
    handlers: list[dict] | None = None,
    vps_host: str = "",
) -> dict:
    rows: list[dict] = []
    for h in handlers or []:
        if not isinstance(h, dict):
            continue
        user = str(h.get("username") or "").strip()
        pwd = str(h.get("password") or "").strip()
        ref = str(h.get("reference") or user).strip()
        if user and pwd:
            rows.append({"username": user, "password": pwd, "reference": ref, "role": "handler"})
    data = {
        "site_url": (site_url or _default_site_url()).strip(),
        "vps_host": (vps_host or "").strip(),
        "admin": {
            "username": (admin_username or "admin").strip(),
            "password": (admin_password or "").strip(),
            "role": "admin",
            "reference": "Full dashboard",
        },
        "handlers": rows,
    }
    _save(data)
    return get_credentials()


def merge_vault_entries(
    *,
    service_accounts: list[dict] | None = None,
    prompts: list[dict] | None = None,
    resources: list[dict] | None = None,
    offer_letters: list[dict] | None = None,
    vps_host: str = "",
) -> dict:
    """Upsert vault rows by stable `id` field."""
    data = _load()
    if vps_host:
        data["vps_host"] = vps_host.strip()

    for key, incoming in (
        ("service_accounts", service_accounts),
        ("prompts", prompts),
        ("resources", resources),
        ("offer_letters", offer_letters),
    ):
        if not incoming:
            continue
        existing = {str(r.get("id") or ""): r for r in (data.get(key) or []) if r.get("id")}
        for row in incoming:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "").strip()
            if not rid:
                continue
            merged = dict(existing.get(rid) or {})
            merged.update(row)
            merged["id"] = rid
            existing[rid] = merged
        data[key] = sorted(existing.values(), key=_section_sort_key(key))

    _save(data)
    return get_credentials()


# Interview Data holds dated operational history (interviews, payments noted in
# chats, process notes) with the source each record came from. It is admin-only
# like the rest of this file, and it is a record of what a source said -- it
# never writes to the candidate, slot or payment stores.
_VAULT_SECTIONS = frozenset({"service_accounts", "prompts", "resources", "offer_letters", "interview_data"})


def _section_sort_key(section: str):
    if section == "offer_letters":
        return lambda r: str(r.get("filename") or r.get("id"))
    if section == "interview_data":
        return lambda r: (str(r.get("event_date") or ""), str(r.get("id") or ""))
    return lambda r: str(r.get("label") or r.get("title") or r.get("id"))


def update_vault_item(section: str, item_id: str, updates: dict) -> dict | None:
    """Update one vault row by stable id. Returns full credentials or None if missing."""
    sec = str(section or "").strip()
    rid = str(item_id or "").strip()
    if sec not in _VAULT_SECTIONS or not rid or not isinstance(updates, dict):
        return None
    data = _load()
    rows = list(data.get(sec) or [])
    idx = next(
        (i for i, r in enumerate(rows) if isinstance(r, dict) and str(r.get("id") or "") == rid),
        None,
    )
    if idx is None:
        return None
    merged = dict(rows[idx])
    for key, value in updates.items():
        if key == "id":
            continue
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    merged["id"] = rid
    rows[idx] = merged
    data[sec] = rows
    _save(data)
    return get_credentials()


def create_vault_item(section: str, row: dict) -> tuple[dict | None, str | None]:
    """Insert a new vault row. Returns (credentials, error)."""
    sec = str(section or "").strip()
    if sec not in _VAULT_SECTIONS or not isinstance(row, dict):
        return None, "Invalid section"
    rid = str(row.get("id") or "").strip()
    if not rid:
        return None, "id is required"
    data = _load()
    rows = list(data.get(sec) or [])
    if any(isinstance(r, dict) and str(r.get("id") or "") == rid for r in rows):
        return None, f"Entry already exists: {rid}"
    entry = dict(row)
    entry["id"] = rid
    rows.append(entry)
    data[sec] = sorted(rows, key=_section_sort_key(sec))
    _save(data)
    return get_credentials(), None


def delete_vault_item(section: str, item_id: str) -> dict | None:
    """Remove one vault row by id."""
    sec = str(section or "").strip()
    rid = str(item_id or "").strip()
    if sec not in _VAULT_SECTIONS or not rid:
        return None
    data = _load()
    rows = list(data.get(sec) or [])
    filtered = [r for r in rows if isinstance(r, dict) and str(r.get("id") or "") != rid]
    if len(filtered) == len(rows):
        return None
    data[sec] = filtered
    _save(data)
    if sec == "offer_letters":
        try:
            os.remove(_offer_letter_cache_path(rid))
        except FileNotFoundError:
            pass
    return get_credentials()


def handler_login_rows() -> list[dict]:
    """The handler rows mirrored here, for recovering the auth store.

    `credentials.json` lives on the data volume and the auth YAML historically
    did not, so after a deployment this copy is all that remains of a handler's
    login. Returned as plain rows; the caller decides what to do with them.
    """
    data = _load()
    return [row for row in (data.get("handlers") or []) if isinstance(row, dict)]


def create_handler_login(row: dict) -> tuple[dict | None, str | None]:
    """Add handler to credentials.json and dashboard_handlers.yaml."""
    if not isinstance(row, dict):
        return None, "Invalid payload"
    user = str(row.get("username") or "").strip()
    pwd = str(row.get("password") or "").strip()
    ref = str(row.get("reference") or user).strip()
    if not user or not pwd:
        return None, "Username and password are required"
    from core import dashboard_auth_vps as auth

    err = auth.admin_add_handler(user, ref, pwd)
    if err:
        return None, err
    data = _load()
    handlers = list(data.get("handlers") or [])
    entry: dict = {
        "username": user,
        "password": auth.stored_handler_password(user) or "",
        "account_id": auth.stored_handler_account_id(user),
        "reference": ref,
        "role": "handler",
    }
    if row.get("notes"):
        entry["notes"] = str(row.get("notes") or "").strip()
    handlers.append(entry)
    data["handlers"] = sorted(handlers, key=lambda r: str(r.get("reference") or r.get("username")))
    _save(data)
    return get_credentials(), None


def rename_handler_login(username: str, new_username: str) -> tuple[dict | None, str | None]:
    """Rename a handler in both durable stores while preserving account identity."""
    current = str(username or "").strip()
    replacement = str(new_username or "").strip()
    if not current or not replacement:
        return None, "Current and new usernames are required"
    from core import dashboard_auth_vps as auth

    err = auth.admin_rename_handler(current, replacement)
    if err:
        return None, err
    data = _load()
    handlers = list(data.get("handlers") or [])
    found = False
    for index, row in enumerate(handlers):
        if not isinstance(row, dict):
            continue
        if str(row.get("username") or "").strip().casefold() != current.casefold():
            continue
        handlers[index] = {
            **row,
            "username": replacement,
            "account_id": auth.stored_handler_account_id(replacement),
        }
        found = True
        break
    if not found:
        # Keep the authoritative store and its mirror aligned if the second
        # half of the rename cannot be completed.
        auth.admin_rename_handler(replacement, current)
        return None, "Handler mirror not found"
    data["handlers"] = handlers
    _save(data)
    return get_credentials(), None


def delete_handler_login(username: str) -> tuple[dict | None, str | None]:
    """Remove handler from credentials.json and dashboard_handlers.yaml."""
    user = str(username or "").strip()
    if not user:
        return None, "Username required"
    from core import dashboard_auth_vps as auth

    err = auth.admin_remove_handler(user)
    if err:
        return None, err
    data = _load()
    handlers = [
        h
        for h in (data.get("handlers") or [])
        if isinstance(h, dict) and str(h.get("username") or "").strip().lower() != user.lower()
    ]
    data["handlers"] = handlers
    _save(data)
    return get_credentials(), None


def sync_admin_login_copy(updates: dict) -> tuple[dict | None, str | None]:
    """Synchronize the display-only admin row after an authenticated auth change."""
    if not isinstance(updates, dict):
        return None, "Invalid payload"
    data = _load()
    admin = dict(data.get("admin") or {})
    if not admin:
        admin = {"username": "admin", "password": "", "role": "admin", "reference": "Full dashboard"}
    if "username" in updates:
        admin["username"] = str(updates.get("username") or admin.get("username") or "admin").strip()
    if "password" in updates and str(updates.get("password") or "").strip():
        admin["password"] = str(updates.get("password") or "").strip()
    if "reference" in updates:
        admin["reference"] = str(updates.get("reference") or "Full dashboard").strip()
    data["admin"] = admin
    _save(data)
    return get_credentials(), None
def set_handler_password_mirror(username: str, stored_password: str) -> bool:
    """Record an already-stored password value in the mirror, and nothing else.

    Separate from `update_handler_login` on purpose. That function routes a new
    password through `admin_set_handler_password`, which hashes what it is
    given -- so feeding a hash back through it would hash the hash and lock the
    handler out. This writes the mirror only.
    """
    user = str(username or "").strip()
    stored = str(stored_password or "").strip()
    if not user or not stored:
        return False
    data = _load()
    handlers = list(data.get("handlers") or [])
    for index, row in enumerate(handlers):
        if not isinstance(row, dict):
            continue
        if str(row.get("username") or "").strip().lower() != user.lower():
            continue
        if str(row.get("password") or "") == stored:
            return False
        handlers[index] = {**row, "password": stored}
        data["handlers"] = handlers
        _save(data)
        return True
    return False


def update_handler_login(username: str, updates: dict) -> tuple[dict | None, str | None]:
    """Update handler row in credentials.json; syncs password to auth YAML when changed."""
    user = str(username or "").strip()
    if not user or not isinstance(updates, dict):
        return None, "Username required"
    data = _load()
    handlers = list(data.get("handlers") or [])
    idx = next(
        (
            i
            for i, h in enumerate(handlers)
            if isinstance(h, dict) and str(h.get("username") or "").strip().lower() == user.lower()
        ),
        None,
    )
    if idx is None:
        return None, "Handler not found"
    merged = dict(handlers[idx])
    if "reference" in updates:
        merged["reference"] = str(updates.get("reference") or merged.get("reference") or user).strip()
    if "notes" in updates:
        notes = str(updates.get("notes") or "").strip()
        if notes:
            merged["notes"] = notes
        else:
            merged.pop("notes", None)
    new_pwd = str(updates.get("password") or "").strip()
    if new_pwd and new_pwd != str(merged.get("password") or ""):
        from core import dashboard_auth_vps as auth

        err = auth.admin_set_handler_password(user, new_pwd)
        if err:
            return None, err
        # What went in was typed by a person; what is recorded here is the hash
        # the auth store now holds.
        merged["password"] = auth.stored_handler_password(user) or ""
    handlers[idx] = merged
    data["handlers"] = handlers
    _save(data)
    return get_credentials(), None


def merge_handlers(handlers: list[dict] | None = None) -> dict:
    """Upsert dashboard handler logins by username."""
    if not handlers:
        return get_credentials()
    data = _load()
    existing = {
        str(h.get("username") or "").strip(): dict(h)
        for h in (data.get("handlers") or [])
        if isinstance(h, dict) and str(h.get("username") or "").strip()
    }
    for row in handlers:
        if not isinstance(row, dict):
            continue
        user = str(row.get("username") or "").strip()
        pwd = str(row.get("password") or "").strip()
        if not user or not pwd:
            continue
        ref = str(row.get("reference") or user).strip()
        merged = dict(existing.get(user) or {})
        merged.update({
            "username": user,
            "password": pwd,
            "reference": ref,
            "role": "handler",
        })
        if row.get("notes"):
            merged["notes"] = str(row.get("notes") or "").strip()
        existing[user] = merged
    data["handlers"] = sorted(existing.values(), key=lambda r: str(r.get("reference") or r.get("username")))
    _save(data)
    return get_credentials()


def migrate_credentials_from_opportunities() -> int:
    """Move legacy dashboard_login rows out of opportunities.json."""
    from features import data_room_store

    return data_room_store.purge_legacy_credential_opportunities()


# ── Offer letter PDF cache (Data room vault) ─────────────────────────────────

_OFFER_CACHE_DIR = os.path.join(DATA_DIR, "data_room", "offer_letters_cache")
_DRIVE_FILE_RE = re.compile(r"drive\.google\.com/file/d/([^/?#]+)", re.I)
_MAX_OFFER_PDF_BYTES = 25 * 1024 * 1024


def _fetch_url_bytes(url: str, *, timeout: int = 90) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if len(data) > _MAX_OFFER_PDF_BYTES:
        raise ValueError("PDF too large to cache")
    return data


def find_offer_letter(item_id: str) -> dict | None:
    rid = str(item_id or "").strip()
    if not rid:
        return None
    for row in get_credentials().get("offer_letters") or []:
        if str(row.get("id") or "") == rid:
            return dict(row)
    return None


def _offer_letter_cache_path(item_id: str) -> str:
    return os.path.join(_OFFER_CACHE_DIR, f"{item_id}.pdf")


def _drive_file_id_from_row(row: dict) -> str:
    fid = str(row.get("drive_file_id") or "").strip()
    if fid:
        return fid
    for key in ("file_url", "view_url", "url"):
        m = _DRIVE_FILE_RE.search(str(row.get(key) or ""))
        if m:
            return m.group(1)
    return ""


def resolve_offer_letter_pdf(item_id: str) -> tuple[str, dict]:
    """Return cached PDF path + catalog row. Downloads from Drive on first view."""
    row = find_offer_letter(item_id)
    if not row:
        raise FileNotFoundError("Offer letter not found")
    cache = _offer_letter_cache_path(item_id)
    if os.path.isfile(cache) and os.path.getsize(cache) > 400:
        return cache, row
    file_id = _drive_file_id_from_row(row)
    if not file_id:
        raise FileNotFoundError(
            "No PDF on server — upload via View or add a Drive file ID in Edit"
        )
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    data = _fetch_url_bytes(url)
    head = data[:800].lower()
    if head.startswith(b"<!doctype") or b"<html" in head:
        raise ValueError(
            "Could not download from Drive (permissions or confirm page). "
            "Upload the PDF in the dashboard or open in Drive."
        )
    if not data.startswith(b"%PDF"):
        raise ValueError("Downloaded file is not a PDF — upload directly or fix Drive link")
    os.makedirs(_OFFER_CACHE_DIR, exist_ok=True)
    tmp = cache + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, cache)
    return cache, row


def save_offer_letter_pdf(item_id: str, data: bytes) -> dict:
    """Store/replace cached PDF for a vault offer letter row."""
    if not data:
        raise ValueError("Empty upload")
    if len(data) > _MAX_OFFER_PDF_BYTES:
        raise ValueError(f"File too large (max {_MAX_OFFER_PDF_BYTES // (1024 * 1024)} MB)")
    if not data.startswith(b"%PDF"):
        raise ValueError("Only PDF files are supported for offer letter preview")
    row = find_offer_letter(item_id)
    if not row:
        raise FileNotFoundError("Offer letter not found")
    os.makedirs(_OFFER_CACHE_DIR, exist_ok=True)
    cache = _offer_letter_cache_path(item_id)
    tmp = cache + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, cache)
    updated = update_vault_item(
        "offer_letters",
        item_id,
        {
            "has_pdf": True,
            "uploaded_at": _now_iso(),
            "size_kb": max(1, (len(data) + 1023) // 1024),
        },
    )
    return find_offer_letter(item_id) if updated else row


def _offer_slug(filename: str) -> str:
    stem = Path(filename or "offer-letter").stem.casefold()
    slug = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")[:32] or "offer_letter"
    existing = {
        str(row.get("id") or "")
        for row in get_credentials().get("offer_letters") or []
        if isinstance(row, dict)
    }
    if slug not in existing:
        return slug
    while True:
        candidate = f"{slug[:23]}_{uuid.uuid4().hex[:8]}"
        if candidate not in existing:
            return candidate


def create_offer_letter_from_pdf(filename: str, pdf_data: bytes) -> dict:
    """Persist a new PDF and create its editable, auto-filled catalog row."""
    if not pdf_data:
        raise ValueError("Empty upload")
    if len(pdf_data) > _MAX_OFFER_PDF_BYTES:
        raise ValueError(f"File too large (max {_MAX_OFFER_PDF_BYTES // (1024 * 1024)} MB)")
    if not pdf_data.startswith(b"%PDF"):
        raise ValueError("Only PDF offer letters are supported")

    from features.offer_letter_extract import extract_offer_letter_fields

    fields = extract_offer_letter_fields(pdf_data, filename)
    item_id = _offer_slug(filename)
    row = {
        "id": item_id,
        "filename": fields["filename"],
        "candidate": fields["candidate"],
        "company_name": fields["company_name"],
        "date_modified": fields["date_modified"],
        "size_kb": fields["size_kb"],
        "drive_file_id": "",
        "notes": fields["notes"],
        "has_pdf": True,
        "uploaded_at": _now_iso(),
        "analysis_method": fields["analysis_method"],
        "analysis_confidence": fields["analysis_confidence"],
    }

    os.makedirs(_OFFER_CACHE_DIR, exist_ok=True)
    cache = _offer_letter_cache_path(item_id)
    tmp = cache + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(pdf_data)
    os.replace(tmp, cache)
    _, error = create_vault_item("offer_letters", row)
    if error:
        try:
            os.remove(cache)
        except OSError:
            pass
        raise ValueError(error)
    return find_offer_letter(item_id) or row


# ── service account screenshots ──────────────────────────────────────────────
#
# Stored the way offer-letter PDFs are: the bytes live on the data volume and
# the vault row keeps only a reference. Putting a screenshot in the row itself
# would inline base64 into `credentials.json`, which is read whole on every
# Data Room request and rewritten whole on every edit -- a few screenshots
# would make every unrelated save slower and the file unreadable.

_ACCOUNT_IMAGE_DIR = os.path.join(DATA_DIR, "data_room", "service_account_images")
_MAX_ACCOUNT_IMAGE_BYTES = 5 * 1024 * 1024

# Sniffed from the bytes, not from the filename or the browser's content-type,
# both of which the uploader controls.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)


def _account_image_kind(data: bytes) -> tuple[str, str] | None:
    """Return (extension, media type) for a supported image, else None."""
    for signature, extension, media_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return extension, media_type
    # WebP is RIFF....WEBP: a four-byte length sits between the two markers.
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def find_service_account(item_id: str) -> dict | None:
    rid = str(item_id or "").strip()
    if not rid:
        return None
    for row in get_credentials().get("service_accounts") or []:
        if str(row.get("id") or "") == rid:
            return dict(row)
    return None


def _account_image_path(item_id: str, extension: str) -> str:
    return os.path.join(_ACCOUNT_IMAGE_DIR, f"{item_id}.{extension}")


def _existing_account_image(item_id: str) -> tuple[str, str] | None:
    for _, extension, media_type in _IMAGE_SIGNATURES + ((b"", "webp", "image/webp"),):
        path = _account_image_path(item_id, extension)
        if os.path.isfile(path):
            return path, media_type
    return None


def save_service_account_image(item_id: str, data: bytes, filename: str = "") -> dict:
    """Store or replace one service account's screenshot."""
    if not data:
        raise ValueError("Empty upload")
    if len(data) > _MAX_ACCOUNT_IMAGE_BYTES:
        raise ValueError(
            f"Image too large (max {_MAX_ACCOUNT_IMAGE_BYTES // (1024 * 1024)} MB)"
        )
    kind = _account_image_kind(data)
    if kind is None:
        raise ValueError("Only PNG, JPG, JPEG and WebP images are supported")
    extension, media_type = kind
    if not find_service_account(item_id):
        raise FileNotFoundError("Service account not found")

    # A replacement can arrive in a different format, so any previous file is
    # removed rather than left orphaned beside the new one.
    delete_service_account_image(item_id, forget=False)

    os.makedirs(_ACCOUNT_IMAGE_DIR, exist_ok=True)
    path = _account_image_path(item_id, extension)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)

    update_vault_item("service_accounts", item_id, {
        "has_image": True,
        "image_filename": str(filename or f"{item_id}.{extension}")[:200],
        "image_type": media_type,
        "image_size_kb": max(1, (len(data) + 1023) // 1024),
        "image_uploaded_at": _now_iso(),
    })
    return find_service_account(item_id) or {}


def resolve_service_account_image(item_id: str) -> tuple[str, str, dict]:
    """Return (path, media type, row) for a stored screenshot."""
    row = find_service_account(item_id)
    if not row:
        raise FileNotFoundError("Service account not found")
    found = _existing_account_image(item_id)
    if not found:
        raise FileNotFoundError("No image stored for this account")
    path, media_type = found
    return path, str(row.get("image_type") or media_type), row


def delete_service_account_image(item_id: str, *, forget: bool = True) -> dict | None:
    """Remove a stored screenshot. `forget` also clears the row's reference."""
    rid = str(item_id or "").strip()
    if not rid:
        return None
    for extension in ("png", "jpg", "webp"):
        path = _account_image_path(rid, extension)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    if not forget:
        return None
    update_vault_item("service_accounts", rid, {
        "has_image": False,
        "image_filename": None,
        "image_type": None,
        "image_size_kb": None,
        "image_uploaded_at": None,
    })
    return find_service_account(rid)
