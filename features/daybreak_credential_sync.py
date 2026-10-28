"""Data Room -> Daybreak: Naukri logins delivered, sealed, to the one computer that runs Daybreak's browser.

The Data Room holds the Naukri logins (passwords Fernet-encrypted at rest). The computer that runs Daybreak's
browser -- Praveen's companion today -- holds a key pair of its own and registers its two public keys here. An admin
approves that computer only after reading its fingerprint on the computer itself. From then on an admin can ask for an
account's login to be delivered: when the computer next checks in, its password is decrypted in this process and at
once sealed to that computer's public key (X25519 + HKDF-SHA256 + AES-256-GCM, the account, address and computer bound
in as associated data). Nothing here writes a password, sealed or not, to disk, a log, an audit row, a response a
browser sees, or Cloudflare; the sealed copy exists only in the one response to the approved computer.

The computer answers with receipts (stored / unchanged / replaced / differs / rejected, plus a proof that it opened the
right password) and with what its Naukri runs showed (signed in as the exact account, or why not). The Data Room's
Daybreak view is built only from those reports:

  Synced                -- the account's address matches exactly one Daybreak candidate (normalised, exact).
  Credential Delivered  -- the approved computer confirmed it holds the account's current password.
  Connected             -- that computer then signed in to Naukri as exactly that account. Never inferred.
  Needs Attention       -- anything that stops the above, with the reason.

Every request from the computer carries Daybreak's existing bearer token AND an Ed25519 signature by its approved key
over the method, path, time, a one-use nonce and the body. The token alone can only register a key, which then waits
for an admin.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock

from core.config import DATA_DIR

_FILE = os.path.join(DATA_DIR, "data_room", "daybreak_sync.json")
_lock = Lock()

EXECUTOR_KINDS = ("praveen", "rtx", "jagadeesh")
# Daybreak's own names for the dedicated workers, as its roster reports the active one.
_DAYBREAK_WORKER_NAMES = {"rtx": "RTX 4060", "jagadeesh": "Jagadeesh"}
SIGNATURE_WINDOW_SEC = 300
DELIVERY_TTL = timedelta(hours=24)
RESEND_AFTER = timedelta(minutes=10)
MAX_SENDS = 3
MAX_ROSTER = 500
MAX_REPORTS = 200
_ENVELOPE_INFO = b"daybreak credential delivery v1"
_RECEIPT_PREFIX = "daybreak-receipt-v1"
_SIGNED_PREFIX = "daybreak-executor-v1"

DELIVERED = frozenset({"stored", "unchanged", "replaced", "created"})
RECEIPT_STATES = DELIVERED | {"differs", "rejected"}
REJECT_REASONS = frozenset({
    "not_in_roster", "email_mismatch", "profile_mismatch", "already_in_daybreak", "expired", "unreadable",
    "wrong_computer", "vault_failed", "import_failed", "consent_missing",
})
# Naukri outcomes that mean the login itself needs a person.
_LOGIN_ATTENTION = {
    "login": "Naukri refused the password",
    "credentials": "Praveen had no password to sign in with",
    "identity": "Naukri showed a different account",
    "restricted": "Naukri restricted access",
    "challenge": "Naukri asked for an OTP or CAPTCHA that was not completed",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_seen_nonces: dict[str, float] = {}


class SyncError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def normalise_email(value) -> str:
    return str(value or "").strip().lower()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value, length: int | None = None) -> bytes:
    try:
        raw = base64.b64decode(str(value or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise SyncError(400, "Malformed key or signature") from exc
    if length is not None and len(raw) != length:
        raise SyncError(400, "Malformed key or signature")
    return raw


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------------------------

def _empty() -> dict:
    return {"executors": {}, "roster": None, "deliveries": {}, "connections": {}, "updated_at": None}


def _load() -> dict:
    with _lock:
        try:
            with open(_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return _empty()
        except (OSError, json.JSONDecodeError) as exc:
            # Never start from empty over a file that exists: that would forget approvals and deliveries.
            raise SyncError(503, "The Daybreak sync record is unreadable") from exc
    if not isinstance(data, dict):
        raise SyncError(503, "The Daybreak sync record is unreadable")
    for key, default in _empty().items():
        data.setdefault(key, default)
    return data


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    data["updated_at"] = _iso(_now())
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


# ---------------------------------------------------------------------------------------------
# Computers (executors)
# ---------------------------------------------------------------------------------------------

def fingerprint(sign_pub: bytes, box_pub: bytes) -> str:
    digest = hashlib.sha256(b"daybreak-executor-keys-v1" + sign_pub + box_pub).hexdigest()[:20].upper()
    return "-".join(digest[i:i + 4] for i in range(0, 20, 4))


def executor_id(sign_pub: bytes) -> str:
    return "exe_" + hashlib.sha256(sign_pub).hexdigest()[:16]


def _public_executor(row: dict) -> dict:
    keep = ("id", "name", "kind", "fingerprint", "status", "active", "registered_at", "last_seen_at",
            "approved_at", "approved_by", "revoked_at", "revoked_by", "daybreak_mode", "daybreak_worker")
    return {key: row.get(key) for key in keep}


def signed_message(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    return "\n".join([_SIGNED_PREFIX, method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()]).encode()


def _check_signature(sign_pub: bytes, headers: dict, method: str, path: str, body: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    timestamp = str(headers.get("timestamp") or "")
    nonce = str(headers.get("nonce") or "")
    if not re.fullmatch(r"\d{10,16}", timestamp) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise SyncError(401, "Unsigned request")
    if abs(time.time() - int(timestamp) / 1000) > SIGNATURE_WINDOW_SEC:
        raise SyncError(401, "Request time is outside the accepted window")
    signature = _unb64(headers.get("signature"), 64)
    try:
        Ed25519PublicKey.from_public_bytes(sign_pub).verify(signature, signed_message(method, path, timestamp, nonce, body))
    except InvalidSignature as exc:
        raise SyncError(401, "Signature does not verify") from exc
    cutoff = time.time() - 2 * SIGNATURE_WINDOW_SEC
    for seen, at in list(_seen_nonces.items()):
        if at < cutoff:
            _seen_nonces.pop(seen, None)
    if nonce in _seen_nonces:
        raise SyncError(401, "Request was already used")
    _seen_nonces[nonce] = time.time()


def register_executor(body: bytes, headers: dict, *, method: str, path: str) -> dict:
    """A computer offers its public keys. It waits, unapproved, until an admin compares the fingerprint."""
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise SyncError(400, "Malformed request") from exc
    name = str(payload.get("name") or "").strip()
    kind = str(payload.get("kind") or "").strip().lower()
    if not 2 <= len(name) <= 60 or kind not in EXECUTOR_KINDS:
        raise SyncError(400, "A computer needs a name and one of: " + ", ".join(EXECUTOR_KINDS))
    sign_pub = _unb64(payload.get("sign_pub"), 32)
    box_pub = _unb64(payload.get("box_pub"), 32)
    _check_signature(sign_pub, headers, method, path, body)
    data = _load()
    exe_id = executor_id(sign_pub)
    row = data["executors"].get(exe_id)
    if row is None:
        row = {"id": exe_id, "name": name, "kind": kind, "sign_pub": _b64(sign_pub), "box_pub": _b64(box_pub),
               "fingerprint": fingerprint(sign_pub, box_pub), "status": "pending", "active": False,
               "registered_at": _iso(_now())}
        data["executors"][exe_id] = row
        _save(data)
        _audit("daybreak_executor_registered", source_id=exe_id, new={"name": name, "kind": kind})
    elif row.get("box_pub") != _b64(box_pub):
        # The same signing key with another sealing key is not this computer any more.
        raise SyncError(409, "These keys do not match the registered computer")
    return {"executor_id": exe_id, "fingerprint": row["fingerprint"], "status": row["status"], "active": bool(row.get("active"))}


def _normal_fingerprint(value) -> str:
    return re.sub(r"[^0-9A-F]", "", str(value or "").upper())


def approve_executor(exe_id: str, *, typed_fingerprint: str, actor: str, cutover_confirmed: bool = False) -> dict:
    """Approve a computer the admin has checked in person: the fingerprint typed here must be the one its screen shows.

    Praveen becomes the active computer when no other is. RTX and Jagadeesh may be approved, but deliveries go to them
    only after a cutover: Daybreak itself must report that it runs on that worker, and the admin must confirm it.
    """
    data = _load()
    row = data["executors"].get(str(exe_id))
    if row is None:
        raise SyncError(404, "No such computer")
    if row.get("status") == "revoked":
        raise SyncError(409, "A revoked computer must register new keys")
    if _normal_fingerprint(typed_fingerprint) != _normal_fingerprint(row["fingerprint"]):
        raise SyncError(400, "The fingerprint does not match. Read it on the computer itself and type it exactly.")
    others_active = [r for r in data["executors"].values() if r.get("active") and r["id"] != row["id"]]
    if row["kind"] != "praveen":
        expected = _DAYBREAK_WORKER_NAMES[row["kind"]]
        if not cutover_confirmed:
            raise SyncError(409, f"{row['name']} can receive logins only after Daybreak's cutover to it is confirmed")
        if row.get("daybreak_mode") != "remote" or row.get("daybreak_worker") != expected:
            raise SyncError(409, f"Daybreak does not report {expected} as its active browser worker yet")
    row["status"] = "approved"
    row["approved_at"] = _iso(_now())
    row["approved_by"] = actor
    if row["kind"] != "praveen":
        for other in others_active:
            other["active"] = False
        row["active"] = True
    elif not others_active:
        row["active"] = True
    _save(data)
    _audit("daybreak_executor_approved", actor=actor, source_id=row["id"],
           new={"kind": row["kind"], "active": row["active"], "cutover": bool(cutover_confirmed)})
    return _public_executor(row)


def revoke_executor(exe_id: str, *, actor: str) -> dict:
    data = _load()
    row = data["executors"].get(str(exe_id))
    if row is None:
        raise SyncError(404, "No such computer")
    row.update(status="revoked", active=False, revoked_at=_iso(_now()), revoked_by=actor)
    for delivery in data["deliveries"].values():
        if delivery.get("executor_id") == row["id"] and delivery["state"] in ("requested", "sent"):
            delivery["state"] = "cancelled"
    _save(data)
    _audit("daybreak_executor_revoked", actor=actor, source_id=row["id"])
    return _public_executor(row)


def _active(data: dict) -> dict | None:
    return next((r for r in data["executors"].values() if r.get("active") and r.get("status") == "approved"), None)


# ---------------------------------------------------------------------------------------------
# Accounts and reconciliation
# ---------------------------------------------------------------------------------------------

def _naukri_accounts() -> list[dict]:
    from features import data_room_credentials_store as creds

    rows = creds._load().get("service_accounts") or []
    out = []
    for row in rows:
        if isinstance(row, dict) and (creds.normalise_service(row.get("service")) or creds.classify_service(row)) == "Naukri":
            out.append(row)
    return out


def credential_version(row: dict) -> str:
    """Changes whenever the stored password changes, without decrypting it: the Fernet token is rewritten only then."""
    token = str(row.get("password_enc") or row.get("password") or "")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24] if token else ""


def _account_password(row: dict) -> str:
    from features import data_room_credentials_store as creds

    return creds._stored_password(row)


def reconcile(accounts: list[dict], roster: list[dict] | None) -> dict:
    """Exact normalised-address matching of Data Room Naukri accounts to Daybreak candidates. Nothing is guessed."""
    profiles = roster or []
    by_email: dict[str, list[dict]] = {}
    for profile in profiles:
        by_email.setdefault(normalise_email(profile.get("email")), []).append(profile)
    account_emails: dict[str, list[str]] = {}
    for row in accounts:
        account_emails.setdefault(normalise_email(row.get("username")), []).append(str(row.get("id")))
    matches: dict[str, dict] = {}
    for row in accounts:
        email = normalise_email(row.get("username"))
        entry = {"email": email, "profile": None, "problems": [], "notes": []}
        if not _EMAIL.match(email):
            entry["problems"].append("invalid_username")
        elif len(account_emails[email]) > 1:
            entry["problems"].append("duplicate_in_data_room")
        elif roster is None:
            entry["problems"].append("no_roster")
        elif len(by_email.get(email, [])) > 1:
            entry["problems"].append("duplicate_in_daybreak")
        elif not by_email.get(email):
            entry["problems"].append("missing_in_daybreak")
        else:
            profile = by_email[email][0]
            entry["profile"] = profile
            if _name_key(profile.get("name")) != _name_key(_account_name(row)):
                entry["notes"].append("name_differs")
            if not profile.get("consent"):
                entry["problems"].append("consent_missing")
        matches[str(row.get("id"))] = entry
    known = set(account_emails)
    daybreak_only = [p for p in profiles if normalise_email(p.get("email")) not in known]
    return {"accounts": matches, "daybreak_only": daybreak_only}


def _account_name(row: dict) -> str:
    return str(row.get("label") or "").strip()


def _name_key(value) -> str:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return " ".join(w for w in words if w != "naukri")


# ---------------------------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------------------------

def request_deliveries(account_ids: list[str], *, actor: str, replace_ids: list[str] | None = None,
                       create_ids: list[str] | None = None, consent_confirmed: bool = False,
                       verify_ids: list[str] | None = None) -> dict:
    """Ask for accounts' logins to be delivered to the active computer at its next check-in.

    Only accounts that match exactly one Daybreak candidate are delivered. An account Daybreak does not have is added
    only when the admin names it in create_ids AND confirms the candidate's consent; it is added with automation off,
    so nothing saves to Naukri until someone turns it on in Daybreak. A password different from the one the computer
    already holds replaces it only when named in replace_ids. An account named in verify_ids asks the computer to prove
    the login with one preview run once it holds the password (it signs in and never saves; Daybreak allows previews
    only for candidates in its daily round); an added account is always connected once. One open delivery per account:
    a new request replaces an open one, never adds a second.
    """
    data = _load()
    executor = _active(data)
    if executor is None:
        raise SyncError(409, "No approved computer is active. Approve Praveen's fingerprint first.")
    accounts = {str(r.get("id")): r for r in _naukri_accounts()}
    roster = (data.get("roster") or {}).get("profiles") if (data.get("roster") or {}).get("executor_id") == executor["id"] else None
    matched = reconcile(list(accounts.values()), roster)["accounts"]
    replace = set(replace_ids or [])
    create = set(create_ids or [])
    verify = set(verify_ids or [])
    if create and not consent_confirmed:
        raise SyncError(400, "Confirm that these candidates agreed to Daybreak before adding them")
    wanted = list(dict.fromkeys([*account_ids, *replace, *create, *verify]))
    queued, refused = [], []
    now = _now()
    for account_id in wanted:
        row = accounts.get(account_id)
        match = matched.get(account_id)
        if row is None or match is None:
            refused.append({"account_id": account_id, "reason": "not_a_naukri_account"})
            continue
        problems = [p for p in match["problems"] if not (p == "missing_in_daybreak" and account_id in create)]
        if problems:
            refused.append({"account_id": account_id, "reason": problems[0]})
            continue
        if not credential_version(row):
            refused.append({"account_id": account_id, "reason": "no_password"})
            continue
        creating = match["profile"] is None
        for old in data["deliveries"].values():
            if old["account_id"] == account_id and old["state"] in ("requested", "sent"):
                old["state"] = "superseded"
        delivery = {
            "id": "dlv_" + secrets.token_hex(12), "account_id": account_id, "executor_id": executor["id"],
            "email": match["email"], "name": _account_name(row) or match["email"],
            "profile_id": None if creating else str(match["profile"].get("id")),
            "create": creating, "replace": account_id in replace, "verify": creating or account_id in verify,
            "state": "requested",
            "requested_at": _iso(now), "requested_by": actor, "expires_at": _iso(now + DELIVERY_TTL),
            "sends": 0,
        }
        data["deliveries"][delivery["id"]] = delivery
        queued.append(account_id)
    _prune(data)
    _save(data)
    _audit("daybreak_credential_delivery_requested", actor=actor, source_id=executor["id"],
           new={"accounts": len(queued), "refused": len(refused), "replace": len(replace & set(queued)),
                "create": len(create & set(queued)), "verify": len(verify & set(queued))})
    return {"queued": queued, "refused": refused, "executor": _public_executor(executor)}


OPEN = frozenset({"requested", "sent"})
FINISHED = DELIVERED | {"differs", "rejected", "expired"}


def _latest(deliveries, states) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in deliveries:
        if row["state"] not in states:
            continue
        best = latest.get(row["account_id"])
        if best is None or str(row.get("requested_at")) > str(best.get("requested_at")):
            latest[row["account_id"]] = row
    return latest


def _prune(data: dict) -> None:
    """Keep what is still open and the latest finished delivery per account (superseded and cancelled ones go)."""
    rows = list(data["deliveries"].values())
    keep = {r["id"] for r in rows if r["state"] in OPEN} | {r["id"] for r in _latest(rows, FINISHED).values()}
    data["deliveries"] = {key: value for key, value in data["deliveries"].items() if key in keep}


def seal(password: str, *, box_pub: bytes, aad: dict) -> dict:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    ephemeral = X25519PrivateKey.generate()
    epk = ephemeral.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(box_pub))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + box_pub, info=_ENVELOPE_INFO).derive(shared)
    iv = os.urandom(12)
    plaintext = json.dumps({"password": password}, ensure_ascii=False).encode("utf-8")
    sealed = AESGCM(key).encrypt(iv, plaintext, canonical(aad))
    return {"v": 1, "alg": "X25519-HKDF-SHA256-A256GCM", "epk": _b64(epk), "iv": _b64(iv), "ct": _b64(sealed), "aad": aad}


def receipt_proof(delivery_id: str, password: str) -> str:
    return hashlib.sha256(f"{_RECEIPT_PREFIX}\n{delivery_id}\n{password}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------
# The computer's check-in
# ---------------------------------------------------------------------------------------------

def _clean_roster(values) -> list[dict]:
    if not isinstance(values, list) or len(values) > MAX_ROSTER:
        raise SyncError(400, "Roster is missing or too large")
    out = []
    for item in values:
        if not isinstance(item, dict) or not _UUID.match(str(item.get("id") or "")):
            raise SyncError(400, "Roster entry without a candidate id")
        out.append({
            "id": str(item["id"]).lower(), "name": str(item.get("name") or "")[:100],
            "email": normalise_email(item.get("email"))[:254], "enabled": bool(item.get("enabled")),
            "consent": bool(item.get("consent")), "session_status": str(item.get("session_status") or "")[:30],
            "has_credential": bool(item.get("has_credential")),
        })
    return out


def executor_sync(body: bytes, headers: dict, *, method: str, path: str) -> dict:
    """One check-in: the computer reports Daybreak's roster, its receipts and its Naukri results; it gets back sealed
    logins for whatever an admin asked to deliver to it. A computer that is not approved and active gets nothing."""
    exe_id = str(headers.get("executor") or "")
    data = _load()
    row = data["executors"].get(exe_id)
    if row is None:
        raise SyncError(401, "Unknown computer")
    _check_signature(_unb64(row["sign_pub"], 32), headers, method, path, body)
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise SyncError(400, "Malformed request") from exc
    now = _now()
    row["last_seen_at"] = _iso(now)
    daybreak = payload.get("daybreak") if isinstance(payload.get("daybreak"), dict) else {}
    row["daybreak_mode"] = "remote" if daybreak.get("mode") == "remote" else "praveen"
    row["daybreak_worker"] = str(daybreak.get("active_worker") or "")[:60] or None
    if row.get("status") != "approved":
        _save(data)
        return {"status": row["status"], "active": False, "deliveries": [], "fingerprint": row["fingerprint"]}
    roster = _clean_roster(payload.get("roster"))
    if row.get("active"):
        data["roster"] = {"executor_id": row["id"], "reported_at": _iso(now), "profiles": roster,
                          "mode": row["daybreak_mode"], "worker": row["daybreak_worker"]}
    _record_receipts(data, row, payload.get("receipts"), now)
    _record_events(data, row, payload.get("events"), now)
    deliveries = _outgoing(data, row, roster, now) if row.get("active") else []
    _save(data)
    return {"status": "approved", "active": bool(row.get("active")), "deliveries": deliveries,
            "fingerprint": row["fingerprint"]}


def _record_receipts(data: dict, executor: dict, receipts, now: datetime) -> None:
    if receipts is None:
        return
    if not isinstance(receipts, list) or len(receipts) > MAX_REPORTS:
        raise SyncError(400, "Too many receipts")
    accounts = {str(r.get("id")): r for r in _naukri_accounts()}
    for item in receipts:
        if not isinstance(item, dict):
            continue
        delivery = data["deliveries"].get(str(item.get("delivery_id") or ""))
        status = str(item.get("status") or "")
        if delivery is None or delivery["executor_id"] != executor["id"] or status not in RECEIPT_STATES:
            continue
        if delivery["state"] not in ("sent", "requested"):
            continue  # a receipt arrives once; a repeat changes nothing
        reason = str(item.get("reason") or "")
        profile_id = str(item.get("profile_id") or "").lower()
        proof_ok = None
        if status in DELIVERED:
            row = accounts.get(delivery["account_id"])
            if row is not None and credential_version(row) == delivery.get("sent_version"):
                proof_ok = secrets.compare_digest(str(item.get("proof") or ""), receipt_proof(delivery["id"], _account_password(row)))
            if proof_ok is False or not _UUID.match(profile_id) or (delivery.get("profile_id") and profile_id != delivery["profile_id"]):
                status, reason = "rejected", "unreadable" if proof_ok is False else "profile_mismatch"
        delivery.update(state=status, receipt_at=_iso(now), proof_ok=proof_ok,
                        reason=reason if reason in REJECT_REASONS or status == "differs" else ("" if status in DELIVERED else "unreadable"))
        if status in DELIVERED and _UUID.match(profile_id):
            delivery["profile_id"] = profile_id
        if item.get("verify") in ("queued", "refused"):
            # Whether Daybreak accepted the preview (or, for an added candidate, the connect) that proves the login.
            delivery["verify_result"] = item["verify"]
        _audit("daybreak_credential_receipt", actor=executor["name"], source_id=delivery["account_id"],
               new={"delivery": delivery["id"], "state": status, "reason": delivery.get("reason") or None, "proof": proof_ok})


def _record_events(data: dict, executor: dict, events, now: datetime) -> None:
    if events is None:
        return
    if not isinstance(events, list) or len(events) > MAX_REPORTS:
        raise SyncError(400, "Too many results")
    for item in events:
        if not isinstance(item, dict) or not _UUID.match(str(item.get("profile_id") or "")) or not _UUID.match(str(item.get("job_id") or "")):
            continue
        at = _parse(item.get("at"))
        if at is None or at > now + timedelta(minutes=5):
            continue
        event = {
            "job_id": str(item["job_id"]).lower(), "kind": str(item.get("kind") or "")[:20],
            "outcome": str(item.get("outcome") or "")[:20], "code": str(item.get("code") or "")[:30],
            "signed_in": item.get("signed_in") is True, "password_used": item.get("password_used") is True,
            "at": _iso(at), "executor_id": executor["id"],
        }
        record = data["connections"].setdefault(str(item["profile_id"]).lower(), {})
        if str(record.get("latest", {}).get("at") or "") <= event["at"]:
            record["latest"] = event
        if event["signed_in"] and str(record.get("signed_in", {}).get("at") or "") <= event["at"]:
            record["signed_in"] = event


def _outgoing(data: dict, executor: dict, roster: list[dict], now: datetime) -> list[dict]:
    accounts = {str(r.get("id")): r for r in _naukri_accounts()}
    box_pub = _unb64(executor["box_pub"], 32)
    out = []
    for delivery in data["deliveries"].values():
        if delivery["executor_id"] != executor["id"] or delivery["state"] not in ("requested", "sent"):
            continue
        if _parse(delivery["expires_at"]) <= now:
            delivery["state"] = "expired"
            continue
        if delivery["state"] == "sent":
            last = _parse(delivery.get("sent_at"))
            if delivery.get("sends", 0) >= MAX_SENDS:
                if last and now - last > RESEND_AFTER:
                    delivery.update(state="rejected", reason="unreadable")
                continue
            if last and now - last < RESEND_AFTER:
                continue
        row = accounts.get(delivery["account_id"])
        if row is None or normalise_email(row.get("username")) != delivery["email"] or not credential_version(row):
            delivery.update(state="cancelled", reason="account_changed")
            continue
        aad = {"delivery_id": delivery["id"], "executor_id": executor["id"], "account_id": delivery["account_id"],
               "profile_id": delivery["profile_id"], "email": delivery["email"], "name": delivery["name"],
               "create": bool(delivery["create"]), "replace": bool(delivery["replace"]),
               "verify": bool(delivery.get("verify")),
               "expires_at": _iso(min(_parse(delivery["expires_at"]), now + timedelta(minutes=30)))}
        out.append({"delivery_id": delivery["id"], "envelope": seal(_account_password(row), box_pub=box_pub, aad=aad)})
        delivery.update(state="sent", sent_at=_iso(now), sends=delivery.get("sends", 0) + 1, sent_version=credential_version(row))
    if out:
        _audit("daybreak_credential_sent", actor="system", source_id=executor["id"], new={"deliveries": len(out)})
    return out


# ---------------------------------------------------------------------------------------------
# The Data Room's view
# ---------------------------------------------------------------------------------------------

ATTENTION_TEXT = {
    "invalid_username": "The username is not an email address, so it cannot be matched",
    "duplicate_in_data_room": "Another Naukri account in the Data Room has the same address",
    "duplicate_in_daybreak": "Daybreak has more than one candidate with this address",
    "missing_in_daybreak": "Not in Daybreak yet",
    "consent_missing": "Daybreak has no consent recorded for this candidate",
    "no_roster": "Praveen has not reported Daybreak's candidates yet",
    "no_computer": "No approved computer is active",
    "changed_since_delivery": "The password changed in the Data Room after it was delivered",
    "differs": "Praveen holds a different password for this candidate; choose Replace to use the Data Room's",
    "delivery_rejected": "The computer refused the delivery",
    "delivery_expired": "The delivery was never collected",
}


def status_view() -> dict:
    data = _load()
    executor = _active(data)
    roster_record = data.get("roster") or {}
    roster = roster_record.get("profiles") if executor and roster_record.get("executor_id") == executor["id"] else None
    accounts = sorted(_naukri_accounts(), key=lambda r: _account_name(r).lower())
    matched = reconcile(accounts, roster)
    current = [d for d in data["deliveries"].values() if executor and d["executor_id"] == executor["id"]]
    # What the computer last confirmed decides the status; a delivery still on its way is shown beside it.
    latest_delivery, pending = _latest(current, FINISHED), _latest(current, OPEN)
    rows = []
    for account in accounts:
        account_id = str(account.get("id"))
        match = matched["accounts"][account_id]
        profile = match["profile"]
        attention = [p for p in match["problems"]]
        if executor is None:
            attention.insert(0, "no_computer")
        delivery = latest_delivery.get(account_id)
        waiting = pending.get(account_id)
        delivered = False
        delivered_at = None
        if delivery:
            if delivery["state"] in DELIVERED:
                if delivery.get("sent_version") == credential_version(account):
                    delivered, delivered_at = True, delivery.get("receipt_at")
                else:
                    attention.append("changed_since_delivery")
            elif delivery["state"] == "differs":
                attention.append("differs")
            elif delivery["state"] == "rejected":
                attention.append("delivery_rejected")
            elif delivery["state"] == "expired":
                attention.append("delivery_expired")
        profile_id = str((profile or {}).get("id") or (delivery or {}).get("profile_id") or "") or None
        connection = data["connections"].get(profile_id or "", {}) if profile_id else {}
        signed_in = connection.get("signed_in")
        latest = connection.get("latest")
        connected = bool(delivered and signed_in and executor and signed_in.get("executor_id") == executor["id"]
                         and str(signed_in.get("at")) >= str(delivered_at or ""))
        login_problem = None
        if latest and not latest.get("signed_in") and latest.get("code") in _LOGIN_ATTENTION and executor \
                and latest.get("executor_id") == executor["id"] and (not signed_in or str(latest["at"]) > str(signed_in["at"])):
            login_problem = latest["code"]
            connected = False
        synced = profile is not None
        reasons = [ATTENTION_TEXT[a] for a in attention]
        if login_problem:
            reasons.append(_LOGIN_ATTENTION[login_problem])
        status = "needs_attention" if reasons else "connected" if connected else "delivered" if delivered else "synced" if synced else "needs_attention"
        rows.append({
            "account_id": account_id, "label": _account_name(account), "email": match["email"],
            "status": status, "synced": synced, "delivered": delivered, "connected": connected,
            "attention": reasons, "attention_codes": attention + ([login_problem] if login_problem else []),
            "notes": match["notes"], "can_add_to_daybreak": "missing_in_daybreak" in match["problems"] and len(match["problems"]) == 1,
            "profile": None if profile is None else {"id": profile["id"], "name": profile.get("name"), "enabled": profile.get("enabled"),
                                                     "has_credential": profile.get("has_credential")},
            "delivery": None if delivery is None else {key: delivery.get(key) for key in (
                "id", "state", "reason", "requested_at", "requested_by", "sent_at", "receipt_at", "replace", "create",
                "verify", "verify_result")},
            "pending": None if waiting is None else {key: waiting.get(key) for key in (
                "id", "state", "requested_at", "sent_at", "replace", "create", "verify")},
            "connection": None if not (signed_in or latest) else {
                "signed_in_at": (signed_in or {}).get("at"), "password_used": bool((signed_in or {}).get("password_used")),
                "job_id": (signed_in or latest or {}).get("job_id"), "latest_outcome": (latest or {}).get("outcome"),
                "latest_code": (latest or {}).get("code"), "latest_at": (latest or {}).get("at")},
        })
    counts = {key: sum(1 for r in rows if r["status"] == key) for key in ("synced", "delivered", "connected", "needs_attention")}
    return {
        "executors": [_public_executor(r) for r in sorted(data["executors"].values(), key=lambda r: str(r.get("registered_at")))],
        "active_executor": _public_executor(executor) if executor else None,
        "roster_reported_at": roster_record.get("reported_at") if roster is not None else None,
        "daybreak_mode": roster_record.get("mode") if roster is not None else None,
        "accounts": rows,
        "daybreak_only": [{"id": p["id"], "name": p.get("name"), "email": p.get("email")} for p in matched["daybreak_only"]],
        "counts": counts,
        "reconciliation": {
            "data_room_accounts": len(rows),
            "matched": sum(1 for r in rows if r["synced"]),
            "missing_in_daybreak": sum(1 for r in rows if "missing_in_daybreak" in r["attention_codes"]),
            "duplicates": sum(1 for r in rows if {"duplicate_in_data_room", "duplicate_in_daybreak"} & set(r["attention_codes"])),
            "invalid_usernames": sum(1 for r in rows if "invalid_username" in r["attention_codes"]),
            "names_differ": sum(1 for r in rows if "name_differs" in r["notes"]),
            "daybreak_only": len(matched["daybreak_only"]),
        },
    }


def _audit(action: str, *, actor: str = "daybreak-executor", source_id: str | None = None, new=None) -> None:
    import logging

    try:
        from core import recruitment_mail_store

        recruitment_mail_store.audit(actor=actor, role="admin" if actor not in ("system", "daybreak-executor") else "system",
                                     action=action, source_id=source_id, new=new)
    except Exception:  # noqa: BLE001 - the record is wanted, not a condition of delivering
        logging.getLogger(__name__).exception("Could not record a Daybreak sync audit row")


def new_nonce() -> str:
    return uuid.uuid4().hex
