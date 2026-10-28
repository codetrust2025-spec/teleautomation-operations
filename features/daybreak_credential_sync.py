"""Data Room -> Daybreak: Naukri logins sealed to Daybreak's dedicated workers (RTX 4060, Jagadeesh) through
Daybreak's own credential queue.

The Data Room holds the Naukri logins (passwords Fernet-encrypted at rest). Daybreak's workers each hold an RSA-3072
key pair in their own encrypted vault; Daybreak's registry has the public halves. To deliver an account, Operations:

  1. asks Daybreak to `prepare` a delivery for that candidate (Daybreak answers with one slot per enrolled worker:
     worker, key, a fresh enrollment id and a 15-minute expiry);
  2. checks every one of those worker keys against the keys the admin approved here after reading each worker's
     fingerprint on that laptop itself (the worker kit's machine-check prints it);
  3. seals the password once per worker in Daybreak's own envelope format (AES-256-GCM, its key wrapped with
     RSA-OAEP-SHA256, candidate/worker/key/enrollment/address/expiry bound as associated data) and `commit`s them.

Only the workers can open what Daybreak stores. Each worker collects its copy and acknowledges it; Daybreak records the
receipt. The password exists in the clear only inside this process while it is sealed. Nothing here writes a password,
sealed or not, to disk, a log, an audit row or a response a browser sees.

Operations proves who it is to Daybreak with an Ed25519 key derived from the deployment's existing credential key
(no new secret to manage); a Daybreak release pins the public half. Each request is signed over the method, path,
time, a nonce and the body.

The Data Room's Daybreak view is built from Daybreak's own records:
  Synced                -- the account's address matches exactly one Daybreak candidate (normalised, exact).
  Credential Delivered  -- every enrolled worker acknowledged the Data Room's current password.
  Connected             -- a worker then signed in to Naukri as exactly that account in a real run (Daybreak's
                           AI-only previews never count). Never inferred.
  Needs Attention       -- anything that stops the above, with the reason.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

from core.config import DATA_DIR

_FILE = os.path.join(DATA_DIR, "data_room", "daybreak_sync.json")
_lock = Lock()

DEFAULT_DAYBREAK_URL = "https://daybreak-naukri-morning-desk.codetrust2025.workers.dev"
_INTAKE_INFO = b"teleautomation daybreak intake signing v1"
_SIGNED_PREFIX = "daybreak-operations-intake-v1"
SNAPSHOT_MAX_AGE_SEC = 60
MAX_PER_REQUEST = 10
# Naukri outcomes in a worker's run that mean the login itself needs a person.
_LOGIN_ATTENTION = {
    "login": "Naukri refused the password",
    "credentials": "The worker had no password to sign in with",
    "identity": "Naukri showed a different account",
    "restricted": "Naukri restricted access",
    "challenge": "Naukri asked for an OTP or CAPTCHA that was not completed",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class SyncError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    # Fixed width (always microseconds), so stored times order correctly as strings.
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _ms_iso(ms) -> str | None:
    try:
        return _iso(datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _when(value) -> str:
    """Any stored time as the fixed-width form, so Daybreak's ISO times and ours compare as strings."""
    parsed = _parse(value)
    return _iso(parsed) if parsed else ""


def normalise_email(value) -> str:
    return str(value or "").strip().lower()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64url_int(value: str) -> int:
    text = str(value)
    return int.from_bytes(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)), "big")


# ---------------------------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------------------------

def _empty() -> dict:
    return {"approvals": {}, "snapshot": None, "deliveries": {}, "updated_at": None}


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
# Operations' identity towards Daybreak, and the Daybreak client
# ---------------------------------------------------------------------------------------------

def _intake_key():
    """Ed25519 key derived from the deployment's credential key, kept apart from its other uses by the HKDF label."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    root = (os.getenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY") or "").strip().encode()
    if not root:
        raise SyncError(503, "MAILBOX_CREDENTIAL_ENCRYPTION_KEY is not configured")
    seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INTAKE_INFO).derive(root)
    return Ed25519PrivateKey.from_private_bytes(seed)


def intake_public_key() -> str:
    """What a Daybreak release pins to accept Operations' requests. Public; safe to show the admin."""
    from cryptography.hazmat.primitives import serialization

    raw = _intake_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(raw)


def signed_message(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    return "\n".join([_SIGNED_PREFIX, method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()]).encode()


def daybreak_url() -> str:
    return (os.getenv("DAYBREAK_SITE_URL") or DEFAULT_DAYBREAK_URL).strip().rstrip("/")


def _daybreak(action: str, payload: dict | None = None) -> dict:
    """One signed POST to Daybreak's credentials route. Daybreak's refusal text is passed on; a password never is."""
    import httpx

    path = "/api/credentials"
    body = json.dumps({"action": action, **(payload or {})}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp, nonce = str(int(time.time() * 1000)), secrets.token_hex(16)
    signature = _intake_key().sign(signed_message("POST", path, timestamp, nonce, body))
    headers = {"Content-Type": "application/json", "X-Operations-Timestamp": timestamp, "X-Operations-Nonce": nonce,
               "X-Operations-Signature": _b64(signature)}
    try:
        response = httpx.post(daybreak_url() + path, content=body, headers=headers, timeout=30.0, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise SyncError(502, "Daybreak could not be reached") from exc
    try:
        result = response.json()
    except ValueError:
        result = {}
    if response.status_code >= 400:
        detail = (result.get("error") or result.get("message") or result.get("detail")) if isinstance(result, dict) else ""
        raise SyncError(502 if response.status_code >= 500 else 409, f"Daybreak refused: {detail or response.status_code}")
    if not isinstance(result, dict):
        raise SyncError(502, "Daybreak answered with something unexpected")
    return result


# ---------------------------------------------------------------------------------------------
# Daybreak's workers and the admin's approval of their keys
# ---------------------------------------------------------------------------------------------

def worker_fingerprint(public_key) -> str:
    """sha256 of the worker's public key exactly as the worker kit's machine-check prints it (JSON.stringify of the JWK)."""
    text = public_key if isinstance(public_key, str) else json.dumps(public_key, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _public_worker(worker: dict, approvals: dict) -> dict:
    fingerprint = worker_fingerprint(worker["publicKey"]) if worker.get("publicKey") else ""
    approval = approvals.get(str(worker.get("keyId") or ""))
    approved = bool(approval and approval.get("fingerprint") == fingerprint and approval.get("worker_id") == worker.get("id"))
    return {"id": worker.get("id"), "name": worker.get("name"), "key_id": worker.get("keyId"), "fingerprint": fingerprint,
            "enabled": bool(worker.get("enabled")), "last_seen": _ms_iso(worker.get("lastSeen")) if worker.get("lastSeen") else None,
            "approved": approved, "approved_by": (approval or {}).get("approved_by") if approved else None,
            "approved_at": (approval or {}).get("approved_at") if approved else None}


def approve_worker(key_id: str, *, typed_fingerprint: str, actor: str) -> dict:
    """Approve one worker key the admin checked on that laptop: what is typed must be the start (at least 16 hex
    characters) of the fingerprint the worker kit's machine-check printed there."""
    data = refresh(force=True)
    worker = next((w for w in data["snapshot"]["workers"] if w.get("keyId") == key_id), None)
    if worker is None or not worker.get("publicKey"):
        raise SyncError(404, "Daybreak has no worker with that key")
    fingerprint = worker_fingerprint(worker["publicKey"])
    typed = re.sub(r"[^0-9a-f]", "", str(typed_fingerprint or "").lower())
    if len(typed) < 16 or not fingerprint.startswith(typed):
        raise SyncError(400, "The fingerprint does not match. Read it on the worker laptop (machine-check) and type at least 16 characters.")
    data["approvals"][key_id] = {"worker_id": worker["id"], "name": worker.get("name"), "fingerprint": fingerprint,
                                 "approved_by": actor, "approved_at": _iso(_now())}
    _save(data)
    _audit("daybreak_worker_key_approved", actor=actor, source_id=key_id, new={"worker": worker.get("name")})
    return _public_worker(worker, data["approvals"])


def revoke_worker(key_id: str, *, actor: str) -> dict:
    data = _load()
    if data["approvals"].pop(str(key_id), None) is None:
        raise SyncError(404, "That key is not approved")
    _save(data)
    _audit("daybreak_worker_key_revoked", actor=actor, source_id=str(key_id))
    return {"key_id": key_id, "approved": False}


# ---------------------------------------------------------------------------------------------
# Accounts and reconciliation
# ---------------------------------------------------------------------------------------------

def _naukri_accounts() -> list[dict]:
    from features import data_room_credentials_store as creds

    rows = creds._load().get("service_accounts") or []
    return [row for row in rows if isinstance(row, dict)
            and (creds.normalise_service(row.get("service")) or creds.classify_service(row)) == "Naukri"]


def credential_version(row: dict) -> str:
    """Changes whenever the stored password changes, without decrypting it: the Fernet token is rewritten only then."""
    token = str(row.get("password_enc") or row.get("password") or "")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24] if token else ""


def _account_password(row: dict) -> str:
    from features import data_room_credentials_store as creds

    return creds._stored_password(row)


def _account_name(row: dict) -> str:
    return str(row.get("label") or "").strip()


def _name_key(value) -> str:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return " ".join(w for w in words if w != "naukri")


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
            if str(profile.get("email") or "") != email:
                # Daybreak's prepare matches the stored address exactly; a case/space variant would be refused.
                entry["problems"].append("daybreak_address_not_normalised")
            if _name_key(profile.get("name")) != _name_key(_account_name(row)):
                entry["notes"].append("name_differs")
            if not profile.get("consent_at"):
                entry["problems"].append("consent_missing")
        matches[str(row.get("id"))] = entry
    known = set(account_emails)
    daybreak_only = [p for p in profiles if normalise_email(p.get("email")) not in known]
    return {"accounts": matches, "daybreak_only": daybreak_only}


# ---------------------------------------------------------------------------------------------
# Daybreak's state
# ---------------------------------------------------------------------------------------------

def _enrollment_ids(data: dict) -> list[str]:
    ids = []
    for delivery in data["deliveries"].values():
        ids.extend(e["id"] for e in delivery.get("enrollments", []) if _UUID.match(str(e.get("id") or "")))
    return ids[-400:]


def refresh(*, force: bool = False) -> dict:
    """Daybreak's workers, candidates, receipts and real-run results, at most a minute old."""
    data = _load()
    snapshot = data.get("snapshot") or {}
    fetched = _parse(snapshot.get("fetched_at"))
    if not force and fetched and (_now() - fetched).total_seconds() < SNAPSHOT_MAX_AGE_SEC:
        return data
    starts = [_parse(d.get("at")) for d in data["deliveries"].values()]
    since = min([s for s in starts if s] or [_now()]) - timedelta(days=1)
    result = _daybreak("operations_status", {"deliveryIds": _enrollment_ids(data), "since": int(since.timestamp() * 1000)})
    receipts = {str(r.get("id")): r for r in result.get("deliveries") or [] if isinstance(r, dict)}
    for delivery in data["deliveries"].values():
        for enrollment in delivery.get("enrollments", []):
            row = receipts.get(enrollment["id"])
            if row is not None:
                enrollment["receipt_at"] = _ms_iso(row["receipt_at"]) if row.get("receipt_at") else None
                enrollment["committed"] = bool(row.get("committed"))
    data["snapshot"] = {
        "fetched_at": _iso(_now()),
        "workers": [w for w in result.get("workers") or [] if isinstance(w, dict)],
        "roster": [p for p in result.get("roster") or [] if isinstance(p, dict) and _UUID.match(str(p.get("id") or ""))],
        "evidence": [e for e in result.get("evidence") or [] if isinstance(e, dict)],
        "remote_control": result.get("remoteControl"),
    }
    _save(data)
    return data


# ---------------------------------------------------------------------------------------------
# Sealing in Daybreak's worker envelope format, and delivering
# ---------------------------------------------------------------------------------------------

ENVELOPE_FIELDS = ("version", "candidateId", "workerId", "enrollmentId", "keyId", "email", "expiresAt")


def seal_for_worker(public_key: dict, meta: dict, password: str) -> dict:
    """Byte-compatible with Daybreak's sealCredential: the worker's own openCredential opens it."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(public_key, dict) or public_key.get("kty") != "RSA" or public_key.get("d"):
        raise SyncError(409, "A worker key is not a public RSA key")
    n, e = _b64url_int(public_key["n"]), _b64url_int(public_key["e"])
    if n.bit_length() != 3072:
        raise SyncError(409, "A worker key is not RSA-3072")
    target = RSAPublicNumbers(e, n).public_key()
    ordered = {key: meta[key] for key in ENVELOPE_FIELDS}
    key, iv = AESGCM.generate_key(bit_length=256), os.urandom(12)
    plain = json.dumps({"password": password}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    aad = json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(iv, plain, aad)
    wrapped = target.encrypt(key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return {**ordered, "iv": _b64(iv), "wrappedKey": _b64(wrapped), "ciphertext": _b64(ciphertext)}


def _approved_recipients(data: dict, offered: list[dict]) -> list[dict]:
    """Every worker Daybreak offers must be one whose key the admin approved, with the same public key."""
    workers = {w.get("id"): w for w in data["snapshot"]["workers"]}
    for slot in offered:
        approval = data["approvals"].get(str(slot.get("keyId") or ""))
        fingerprint = worker_fingerprint(slot.get("publicKey")) if slot.get("publicKey") else ""
        name = (workers.get(slot.get("workerId")) or {}).get("name") or "A Daybreak worker"
        if not approval or approval["fingerprint"] != fingerprint or approval["worker_id"] != slot.get("workerId"):
            raise SyncError(409, f"{name}'s key is not approved in the Data Room. Compare its fingerprint on that laptop and approve it first.")
    return offered


def deliver(account_ids: list[str], *, actor: str, create_ids: list[str] | None = None, consent_confirmed: bool = False) -> dict:
    """Seal the Data Room's current password for each account to every enrolled worker, through Daybreak's queue.

    Only accounts matching exactly one Daybreak candidate are delivered. An account Daybreak lacks is added only when
    named in create_ids AND the admin confirms the candidate's consent (Daybreak creates it with its consent time; its
    daily round still runs only the candidates on its own list, so nothing saves to Naukri by itself). Each worker
    replaces whatever password it held for that candidate with this one when it collects it.
    """
    create = set(create_ids or [])
    if create and not consent_confirmed:
        raise SyncError(400, "Confirm that these candidates agreed to Daybreak before adding them")
    wanted = list(dict.fromkeys([*account_ids, *create]))
    if len(wanted) > MAX_PER_REQUEST:
        raise SyncError(400, f"Deliver at most {MAX_PER_REQUEST} accounts at a time")
    data = refresh(force=True)
    accounts = {str(r.get("id")): r for r in _naukri_accounts()}
    matched = reconcile(list(accounts.values()), data["snapshot"]["roster"])["accounts"]
    if not [w for w in data["snapshot"]["workers"] if w.get("enabled")]:
        raise SyncError(409, "Daybreak has no enrolled worker to deliver to")
    delivered, refused = [], []
    for account_id in wanted:
        row, match = accounts.get(account_id), matched.get(account_id)
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
        profile = match["profile"]
        request = {"name": (_account_name(row) or match["email"])[:100], "email": match["email"], "consent": True}
        if profile is not None:
            request["id"] = profile["id"]
        else:
            request["create"] = True
        try:
            prepared = _daybreak("prepare", request)
            offered = [slot for slot in prepared.get("deliveries") or [] if isinstance(slot, dict)]
            candidate = prepared.get("candidate") or {}
            if (not offered or normalise_email(candidate.get("email")) != match["email"]
                    or (profile is not None and candidate.get("id") != profile["id"])
                    or any(s.get("candidateId") != candidate.get("id") or s.get("email") != match["email"] for s in offered)):
                raise SyncError(409, "Daybreak prepared a different candidate")
            recipients = _approved_recipients(data, offered)
            password = _account_password(row)
            try:
                envelopes = [seal_for_worker(slot["publicKey"], {
                    "version": 1, "candidateId": slot["candidateId"], "workerId": slot["workerId"], "enrollmentId": slot["id"],
                    "keyId": slot["keyId"], "email": slot["email"], "expiresAt": int(slot["expiresAt"])}, password)
                    for slot in recipients]
            finally:
                password = ""
            _daybreak("commit", {"envelopes": envelopes})
        except SyncError as exc:
            refused.append({"account_id": account_id, "reason": "daybreak", "detail": exc.detail})
            continue
        data["deliveries"][account_id] = {
            "account_id": account_id, "profile_id": candidate["id"], "email": match["email"],
            "credential_version": credential_version(row), "at": _iso(_now()), "by": actor, "created": profile is None,
            "enrollments": [{"id": slot["id"], "worker_id": slot["workerId"], "key_id": slot["keyId"],
                             "expires_at": _ms_iso(slot["expiresAt"]), "receipt_at": None, "committed": True}
                            for slot in recipients],
        }
        delivered.append(account_id)
        _save(data)
    if delivered:
        # Daybreak's candidate list and queue changed: the next view reads them again instead of the cached copy.
        data["snapshot"]["fetched_at"] = None
        _save(data)
    _audit("daybreak_credential_delivered", actor=actor, source_id="data_room",
           new={"accounts": len(delivered), "refused": len(refused), "created": len(create & set(delivered))})
    return {"delivered": delivered, "refused": refused}


# ---------------------------------------------------------------------------------------------
# The Data Room's view
# ---------------------------------------------------------------------------------------------

ATTENTION_TEXT = {
    "invalid_username": "The username is not an email address, so it cannot be matched",
    "duplicate_in_data_room": "Another Naukri account in the Data Room has the same address",
    "duplicate_in_daybreak": "Daybreak has more than one candidate with this address",
    "daybreak_address_not_normalised": "Daybreak stores this address with different case or spaces",
    "missing_in_daybreak": "Not in Daybreak yet",
    "consent_missing": "Daybreak has no consent recorded for this candidate",
    "no_roster": "Daybreak's candidate list could not be read",
    "changed_since_delivery": "The password changed in the Data Room after it was delivered",
    "delivery_expired": "A worker did not collect the delivery within 15 minutes; deliver again",
}


def _signed_in(row: dict) -> bool:
    """A real run by a dedicated worker that reached the candidate's own Naukri profile. Daybreak leaves AI-only
    previews (preview_mode) out of the evidence it returns."""
    if not row.get("same_account"):
        return False
    return row.get("state") == "success" or (row.get("state") == "skipped" and row.get("result_code") == "unchanged")


def status_view() -> dict:
    data = refresh()
    snapshot = data["snapshot"]
    workers = [_public_worker(w, data["approvals"]) for w in snapshot["workers"]]
    worker_of = {w["id"]: w for w in workers} | {w["name"]: w for w in workers}  # runs name a worker by id or name
    accounts = sorted(_naukri_accounts(), key=lambda r: _account_name(r).lower())
    matched = reconcile(accounts, snapshot["roster"])
    runs_of: dict[str, list[dict]] = {}
    for run in snapshot.get("evidence") or []:
        runs_of.setdefault(str(run.get("profile_id") or ""), []).append(run)
    now = _now()
    rows = []
    for account in accounts:
        account_id = str(account.get("id"))
        match = matched["accounts"][account_id]
        profile = match["profile"]
        attention = list(match["problems"])
        delivery = data["deliveries"].get(account_id)
        delivered, waiting, receipts = False, [], {}
        if delivery:
            for enrollment in delivery["enrollments"]:
                name = (worker_of.get(enrollment["worker_id"]) or {}).get("name") or "worker"
                if enrollment.get("receipt_at"):
                    receipts[enrollment["worker_id"]] = {"worker": name, "at": _when(enrollment["receipt_at"])}
                elif (_parse(enrollment.get("expires_at")) or now) <= now:
                    attention.append("delivery_expired")
                else:
                    waiting.append(name)
            current = delivery.get("credential_version") == credential_version(account)
            if not current:
                attention.append("changed_since_delivery")
            delivered = current and len(receipts) == len(delivery["enrollments"]) > 0
        profile_id = str((profile or {}).get("id") or (delivery or {}).get("profile_id") or "")
        runs = sorted(runs_of.get(profile_id, []), key=lambda r: _when(r.get("completed_at")))
        after_receipt = [r for r in runs if (w := worker_of.get(str(r.get("runner_worker") or "")))
                         and w["id"] in receipts and _when(r.get("completed_at")) >= receipts[w["id"]]["at"]]
        sign_ins = [r for r in after_receipt if _signed_in(r)]
        refusals = [r for r in after_receipt if r.get("state") == "attention" and r.get("result_code") in _LOGIN_ATTENTION]
        connected = bool(delivered and sign_ins)
        login_problem = None
        if delivered and refusals and (not sign_ins or _when(refusals[-1]["completed_at"]) > _when(sign_ins[-1]["completed_at"])):
            login_problem, connected = refusals[-1]["result_code"], False
        reasons = [ATTENTION_TEXT[a] for a in dict.fromkeys(attention)]
        if login_problem:
            reasons.append(_LOGIN_ATTENTION[login_problem])
        synced = profile is not None
        status = "needs_attention" if reasons else "connected" if connected else "delivered" if delivered else "synced" if synced else "needs_attention"
        last = sign_ins[-1] if sign_ins else None
        rows.append({
            "account_id": account_id, "label": _account_name(account), "email": match["email"], "status": status,
            "synced": synced, "delivered": delivered, "connected": connected, "attention": reasons,
            "attention_codes": list(dict.fromkeys(attention)) + ([login_problem] if login_problem else []),
            "notes": match["notes"], "can_add_to_daybreak": match["problems"] == ["missing_in_daybreak"],
            "profile": None if profile is None else {"id": profile["id"], "name": profile.get("name"), "enabled": bool(profile.get("enabled"))},
            "delivery": None if delivery is None else {"at": delivery.get("at"), "by": delivery.get("by"), "created": bool(delivery.get("created")),
                                                       "receipts": list(receipts.values()), "waiting_for": waiting},
            "connection": None if last is None else {"signed_in_at": _when(last.get("completed_at")), "kind": last.get("kind"),
                                                     "worker": (worker_of.get(str(last.get("runner_worker") or "")) or {}).get("name")},
        })
    counts = {key: sum(1 for r in rows if r["status"] == key) for key in ("synced", "delivered", "connected", "needs_attention")}
    return {
        "daybreak_url": daybreak_url(), "fetched_at": snapshot.get("fetched_at"), "workers": workers,
        "remote_control": snapshot.get("remote_control"), "accounts": rows,
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


def _audit(action: str, *, actor: str, source_id: str | None = None, new=None) -> None:
    import logging

    try:
        from core import recruitment_mail_store

        recruitment_mail_store.audit(actor=actor, role="admin", action=action, source_id=source_id, new=new)
    except Exception:  # noqa: BLE001 - the record is wanted, not a condition of delivering
        logging.getLogger(__name__).exception("Could not record a Daybreak sync audit row")
