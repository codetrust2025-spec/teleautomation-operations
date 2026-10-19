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
            return data
        except (OSError, json.JSONDecodeError):
            return _empty()


# ---------------------------------------------------------------------------------------------
# Service accounts: their service, and their passwords encrypted at rest
# ---------------------------------------------------------------------------------------------

SERVICE_TYPES = ("Naukri", "Gmail", "Telegram")
DEFAULT_SERVICE = "Naukri"
_SERVICE_WORDS = {name.lower(): name for name in SERVICE_TYPES}
_KEY_INFO = b"teleautomation data room service account passwords"


def normalise_service(value) -> str:
    """One of SERVICE_TYPES, whatever its case, or "" when it is none of them."""
    return _SERVICE_WORDS.get(str(value or "").strip().lower(), "")


def classify_service(row: dict) -> str:
    """The service an account is for, from what the account itself says, or "" when it does not say.

    A service already recorded wins. Otherwise the account's name, then its notes, must name exactly one service
    ("Bhavana Naukri", "Telegram login number"). The username is never read: a Naukri login is very often a Gmail
    address, and an email domain says where the mail goes, not what the account is for.
    """
    recorded = normalise_service(row.get("service"))
    if recorded:
        return recorded
    for field in ("label", "notes"):
        words = set(re.findall(r"[a-z]+", str(row.get(field) or "").lower()))
        named = [name for word, name in _SERVICE_WORDS.items() if word in words]
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            return ""
    return ""


def _password_cipher():
    """A key of its own for these passwords, derived from the deployment's credential key (the one the mailbox
    credentials use) so no new secret has to be managed; derivation keeps the two uses apart."""
    import base64

    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    root = (os.getenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY") or "").strip().encode()
    if not root:
        raise RuntimeError("MAILBOX_CREDENTIAL_ENCRYPTION_KEY is not configured")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_KEY_INFO).derive(root)
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt_password(value: str) -> str:
    return _password_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_password(token: str) -> str:
    return _password_cipher().decrypt(token.encode("ascii")).decode("utf-8")


def _seal_service_account_passwords(data: dict) -> int:
    """Every plain `password` on a service account becomes `password_enc`; returns how many. Runs on every save, so
    nothing writes one in the clear whichever path it takes. Without a configured key (a developer machine) the
    rows are left as they are."""
    sealed = 0
    rows = data.get("service_accounts") or []
    if not any(isinstance(r, dict) and "password" in r for r in rows):
        return 0
    try:
        _password_cipher()
    except RuntimeError:
        return 0
    for row in rows:
        if isinstance(row, dict) and "password" in row:
            plain = row.pop("password")
            if plain:
                row["password_enc"] = _encrypt_password(str(plain))
            else:
                row.pop("password_enc", None)
            sealed += 1
    return sealed


def _stored_password(row: dict) -> str:
    if row.get("password_enc"):
        return _decrypt_password(str(row["password_enc"]))
    return str(row.get("password") or "")


def _public_service_account(row: dict) -> dict:
    """What a routine response carries: everything but the password, plus whether there is one."""
    public = {key: value for key, value in row.items() if key not in ("password", "password_enc")}
    public["service"] = normalise_service(row.get("service")) or classify_service(row)
    public["has_password"] = bool(row.get("password_enc") or row.get("password"))
    return public


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    data = dict(data)
    _seal_service_account_passwords(data)
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


def _hash_admin_password_copy(data: dict) -> bool:
    """Replace a password kept as typed on the admin row with its hash.

    The row is a copy for this page; login never reads it. It held the admin
    password as typed -- on the data volume and in every backup of it -- while
    handler rows already held hashes (11 Oct audit). Returns True if changed."""
    from core.password_hashing import hash_password, is_hashed

    admin = data.get("admin")
    if not isinstance(admin, dict):
        return False
    stored = str(admin.get("password") or "")
    if not stored or is_hashed(stored):
        return False
    data["admin"] = {**admin, "password": hash_password(stored)}
    return True


def hash_stored_admin_password() -> bool:
    """Run once at startup so the copy does not wait for someone to open the page."""
    data = _load()
    if not _hash_admin_password_copy(data):
        return False
    _save(data)
    return True


def get_credentials() -> dict:
    data = _load()
    changed = _migrate_offer_letter_rows(data)
    changed = _hash_admin_password_copy(data) or changed
    if changed:
        _save(data)
    admin = data.get("admin")
    handlers = list(data.get("handlers") or [])
    service_accounts = _clean_list(data.get("service_accounts"))
    prompts = _clean_list(data.get("prompts"))
    resources = _clean_list(data.get("resources"))
    offer_letters = _clean_list(data.get("offer_letters"))
    return {
        "site_url": (data.get("site_url") or "").strip(),
        "vps_host": (data.get("vps_host") or "").strip(),
        # Like the handlers': a hash, of no use to the page, so not sent.
        "admin": (
            {key: value for key, value in admin.items() if key != "password"}
            if isinstance(admin, dict) else None
        ),
        # The stored password is a hash and of no use to a caller; withholding
        # it keeps the secret off the wire and out of anything that logs a
        # response body.
        "handlers": [
            {key: value for key, value in h.items() if key != "password"}
            for h in handlers if isinstance(h, dict)
        ],
        # Passwords stay in the store, encrypted: one is read only on purpose
        # (service_account_password), never with the list.
        "service_accounts": [_public_service_account(row) for row in service_accounts],
        "prompts": prompts,
        "resources": resources,
        "offer_letters": offer_letters,
        "updated_at": data.get("updated_at"),
        "count": (1 if admin else 0) + len(handlers),
    }




def _hashed_or_empty(password: str | None) -> str:
    from core.password_hashing import hash_password, is_hashed

    value = (password or "").strip()
    return value if not value or is_hashed(value) else hash_password(value)


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
            "password": _hashed_or_empty(admin_password),
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


# The sections the vault routes may create, edit and delete rows in.
_VAULT_SECTIONS = frozenset({"service_accounts", "prompts", "resources", "offer_letters"})


def _section_sort_key(section: str):
    if section == "offer_letters":
        return lambda r: str(r.get("filename") or r.get("id"))
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
        _remove_offer_letter_files(rid)
    return get_credentials()


def migrate_service_accounts() -> dict:
    """Give every service account its service and encrypt every password still stored in the clear.

    A service already recorded is kept (only its case is tidied); one the account does not name stays empty for a
    person to choose. Rows, ids, notes, images and provenance are untouched, nothing is added or removed, and running
    it again changes nothing.
    """
    data = _load()
    rows = data.get("service_accounts") or []
    classified = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        service = classify_service(row)
        if service and row.get("service") != service:
            row["service"] = service
            classified += 1
    plain = sum(1 for row in rows if isinstance(row, dict) and "password" in row)
    if classified or plain:
        _save(data)
    after = [r for r in (_load().get("service_accounts") or []) if isinstance(r, dict)]
    by_service = {name: 0 for name in SERVICE_TYPES + ("",)}
    for row in after:
        by_service[normalise_service(row.get("service"))] += 1
    return {
        "accounts": len(after),
        "classified": classified,
        "encrypted_now": plain - sum(1 for r in after if "password" in r),
        "still_plain": sum(1 for r in after if "password" in r),
        "by_service": {name or "not_set": count for name, count in by_service.items()},
    }


def _find_service_row(data: dict, item_id: str) -> dict | None:
    rid = str(item_id or "").strip()
    return next((r for r in data.get("service_accounts") or [] if isinstance(r, dict) and str(r.get("id") or "") == rid), None)


def service_account_password(item_id: str) -> str | None:
    """One account's password, decrypted, or None when there is no such account."""
    row = _find_service_row(_load(), item_id)
    return None if row is None else _stored_password(row)


def service_accounts_with_passwords() -> list[dict]:
    """Every service account with its password, for the admin's export; nothing else reads them all."""
    out = []
    for row in _load().get("service_accounts") or []:
        if isinstance(row, dict):
            entry = _public_service_account(row)
            entry["password"] = _stored_password(row)
            out.append(entry)
    return sorted(out, key=_section_sort_key("service_accounts"))


def _service_account_fields(body: dict, *, creating: bool) -> tuple[dict, str | None]:
    """The fields an operator may set on a service account, checked."""
    fields: dict = {}
    for key in ("label", "username", "notes"):
        if key in body and body[key] is not None:
            fields[key] = str(body[key])
    if "service" in body or creating:
        service = normalise_service(body.get("service")) if body.get("service") not in (None, "") else (DEFAULT_SERVICE if creating else "")
        if not service:
            return {}, f"Service must be one of {', '.join(SERVICE_TYPES)}"
        fields["service"] = service
    password = body.get("password")
    # An edit sends a password only when it is changed; a blank one keeps what is stored.
    if isinstance(password, str) and password != "":
        fields["password"] = password
    return fields, None


def create_service_account(body: dict) -> tuple[dict | None, str | None]:
    fields, error = _service_account_fields(body or {}, creating=True)
    if error:
        return None, error
    return create_vault_item("service_accounts", {"id": str((body or {}).get("id") or "").strip(), **fields})


def update_service_account(item_id: str, body: dict) -> tuple[dict | None, str | None]:
    fields, error = _service_account_fields(body or {}, creating=False)
    if error:
        return None, error
    if "password" in fields:
        # replaces the stored one; _save encrypts it
        fields["password_enc"] = None
    updated = update_vault_item("service_accounts", item_id, fields)
    return (updated, None) if updated is not None else (None, "Not found")


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
        admin["password"] = _hashed_or_empty(str(updates.get("password") or ""))
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


def _offer_letter_cache_path(item_id: str, extension: str = "pdf") -> str:
    return os.path.join(_OFFER_CACHE_DIR, f"{item_id}.{extension}")


# An offer letter is kept as the file that was uploaded -- a PDF, or a photo or
# scan of the letter -- byte for byte. What it is comes from its first bytes,
# never from a name or a browser's claim.
_OFFER_FILE_KINDS = (
    ("pdf", "application/pdf"),
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("webp", "image/webp"),
)


def offer_file_kind(data: bytes) -> tuple[str, str] | None:
    """(extension, media type) of a PDF, PNG, JPEG or WebP file, from its bytes; None for anything else."""
    head = bytes(data[:16])
    if head.startswith(b"%PDF"):
        return _OFFER_FILE_KINDS[0]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return _OFFER_FILE_KINDS[1]
    if head.startswith(b"\xff\xd8\xff"):
        return _OFFER_FILE_KINDS[2]
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return _OFFER_FILE_KINDS[3]
    return None


def _stored_offer_file(item_id: str) -> tuple[str, str] | None:
    """(path, media type) of the file stored for an offer letter, if there is one."""
    for extension, _ in _OFFER_FILE_KINDS:
        path = _offer_letter_cache_path(item_id, extension)
        if not os.path.isfile(path):
            continue
        # A PDF of a few hundred bytes is a failed Drive download, not a letter.
        if extension == "pdf" and os.path.getsize(path) <= 400:
            continue
        with open(path, "rb") as handle:
            kind = offer_file_kind(handle.read(16))
        if kind:
            return path, kind[1]
    return None


def _remove_offer_letter_files(item_id: str) -> None:
    for extension, _ in _OFFER_FILE_KINDS:
        try:
            os.remove(_offer_letter_cache_path(item_id, extension))
        except FileNotFoundError:
            pass


def _write_offer_letter_file(item_id: str, data: bytes, extension: str) -> None:
    """Store the file as uploaded, replacing whatever was stored before, of any kind."""
    os.makedirs(_OFFER_CACHE_DIR, exist_ok=True)
    path = _offer_letter_cache_path(item_id, extension)
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
    for other, _ in _OFFER_FILE_KINDS:
        if other != extension:
            try:
                os.remove(_offer_letter_cache_path(item_id, other))
            except FileNotFoundError:
                pass
    os.replace(tmp, path)


def _checked_offer_file(data: bytes) -> tuple[str, str]:
    if not data:
        raise ValueError("Empty upload")
    if len(data) > _MAX_OFFER_PDF_BYTES:
        raise ValueError(f"File too large (max {_MAX_OFFER_PDF_BYTES // (1024 * 1024)} MB)")
    kind = offer_file_kind(data)
    if kind is None:
        raise ValueError("Only PDF, PNG, JPG or WebP offer letters are supported")
    return kind


def _drive_file_id_from_row(row: dict) -> str:
    fid = str(row.get("drive_file_id") or "").strip()
    if fid:
        return fid
    for key in ("file_url", "view_url", "url"):
        m = _DRIVE_FILE_RE.search(str(row.get(key) or ""))
        if m:
            return m.group(1)
    return ""


def resolve_offer_letter_file(item_id: str) -> tuple[str, str, dict]:
    """(path, media type, row) of the offer letter's file, exactly as it was uploaded. A letter known only by its Drive
    id is fetched from Drive, as a PDF, the first time it is opened."""
    row = find_offer_letter(item_id)
    if not row:
        raise FileNotFoundError("Offer letter not found")
    stored = _stored_offer_file(item_id)
    if stored:
        return stored[0], stored[1], row
    path, row = resolve_offer_letter_pdf(item_id)
    return path, "application/pdf", row


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
            "No file on the server yet. Upload the PDF or image with the ↑ button on this offer."
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


def save_offer_letter_file(item_id: str, data: bytes) -> dict:
    """Store or replace the file of an existing offer letter: a PDF or an image, kept as uploaded.

    `has_pdf` keeps its name for the rows and pages that read it; it means a file is stored."""
    extension, media_type = _checked_offer_file(data)
    row = find_offer_letter(item_id)
    if not row:
        raise FileNotFoundError("Offer letter not found")
    _write_offer_letter_file(item_id, data, extension)
    updated = update_vault_item(
        "offer_letters",
        item_id,
        {
            "has_pdf": True,
            "file_type": "pdf" if extension == "pdf" else "image",
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


def create_offer_letter_from_file(filename: str, data: bytes) -> dict:
    """Persist a new offer letter -- a PDF, or a photo or scan of one -- and create its editable, auto-filled row."""
    extension, _ = _checked_offer_file(data)

    from features.offer_letter_extract import extract_offer_letter_fields

    fields = (extract_offer_letter_fields(data, filename) if extension == "pdf"
              else extract_offer_letter_fields(data, filename, image=True))
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
        "file_type": "pdf" if extension == "pdf" else "image",
        "uploaded_at": _now_iso(),
        "analysis_method": fields["analysis_method"],
        "analysis_confidence": fields["analysis_confidence"],
    }

    _write_offer_letter_file(item_id, data, extension)
    _, error = create_vault_item("offer_letters", row)
    if error:
        _remove_offer_letter_files(item_id)
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
