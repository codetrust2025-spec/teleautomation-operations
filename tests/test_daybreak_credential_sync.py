"""Data Room -> Daybreak queue -> RTX/Jagadeesh, driven through the real routes with synthetic logins only.

Daybreak is replaced by an in-process fake that keeps the same contract as the intake release (operations_status,
prepare, commit) and whose two workers hold real RSA-3072 keys; each "worker" opens its copy exactly as Daybreak's
openCredential does (AAD = the envelope metadata in its fixed order). The real Daybreak release runs these same flows in
workerd with this Python code: production/releases/operations-intake-20261028/tests in daybreak-naukri-morning-desk.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routers import daybreak_credential_sync as routes
from features import daybreak_credential_sync as sync
from features import data_room_credentials_store as creds

ACCOUNTS = [
    {"id": "asha_rao", "label": "Asha Rao", "service": "Naukri", "username": "Asha.Rao@Example.com ", "password": "Asha-Pass-1"},
    {"id": "ravi_kumar", "label": "Ravi Kumar", "service": "Naukri", "username": "ravi@example.com", "password": "Ravi=Pass-2"},
    {"id": "new_person", "label": "New Person", "service": "Naukri", "username": "new.person@example.com", "password": "New#Pass-3"},
    {"id": "twin_a", "label": "Twin A", "service": "Naukri", "username": "twin@example.com", "password": "Twin-A-Pass"},
    {"id": "twin_b", "label": "Twin B", "service": "Naukri", "username": "TWIN@example.com", "password": "Twin-B-Pass"},
    {"id": "phone_login", "label": "Phone Login", "service": "Naukri", "username": "+91 90000 00000", "password": "Phone-Pass-4"},
    {"id": "dupe_daybreak", "label": "Dupe", "service": "Naukri", "username": "dupe@example.com", "password": "Dupe-Pass-5"},
    {"id": "no_consent", "label": "No Consent", "service": "Naukri", "username": "noconsent@example.com", "password": "NoConsent-6"},
    {"id": "cased", "label": "Cased", "service": "Naukri", "username": "cased@example.com", "password": "Cased-Pass-7"},
    {"id": "telegram", "label": "Telegram login", "service": "Telegram", "username": "tg@example.com", "password": "Telegram-Pass-8"},
]
SECRETS = [a["password"] for a in ACCOUNTS] + ["Asha-Changed-9"]
P = {name: f"{n:08x}-0000-4000-8000-000000000000" for n, name in enumerate(
    ["asha", "ravi", "dupe1", "dupe2", "nc", "only", "cased"], start=1)}
T0 = "2026-10-01T00:00:00.000Z"


def b64url(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


class Worker:
    def __init__(self, name, priority):
        self.id, self.name, self.priority, self.key_id = str(uuid.uuid4()), name, priority, str(uuid.uuid4())
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        numbers = self.private.public_key().public_numbers()
        # WebCrypto's export order, as the worker stores it and machine-check hashes it.
        self.jwk = {"alg": "RSA-OAEP-256", "e": b64url(numbers.e), "ext": True, "key_ops": ["encrypt"], "kty": "RSA", "n": b64url(numbers.n)}
        self.text = json.dumps(self.jwk, separators=(",", ":"))
        self.fingerprint = hashlib.sha256(self.text.encode()).hexdigest()

    def open(self, envelope: dict) -> str:
        """Daybreak's openCredential, in Python."""
        meta = {k: envelope[k] for k in sync.ENVELOPE_FIELDS}
        key = self.private.decrypt(base64.b64decode(envelope["wrappedKey"]),
                                   padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
        plain = AESGCM(key).decrypt(base64.b64decode(envelope["iv"]), base64.b64decode(envelope["ciphertext"]),
                                    json.dumps(meta, separators=(",", ":")).encode())
        return json.loads(plain)["password"]


class FakeDaybreak:
    """The intake release's contract, in memory."""

    def __init__(self):
        self.workers = [Worker("RTX 4060", 0), Worker("Jagadeesh", 1)]
        self.roster = [
            {"id": P["asha"], "name": "Asha Rao", "email": "asha.rao@example.com", "consent_at": T0, "enabled": 1},
            {"id": P["ravi"], "name": "Ravi K", "email": "ravi@example.com", "consent_at": T0, "enabled": 1},
            {"id": P["dupe1"], "name": "Dupe One", "email": "dupe@example.com", "consent_at": T0, "enabled": 1},
            {"id": P["dupe2"], "name": "Dupe Two", "email": " dupe@example.com", "consent_at": T0, "enabled": 1},
            {"id": P["nc"], "name": "No Consent", "email": "noconsent@example.com", "consent_at": None, "enabled": 0},
            {"id": P["only"], "name": "Only In Daybreak", "email": "only@example.com", "consent_at": T0, "enabled": 1},
            {"id": P["cased"], "name": "Cased", "email": "Cased@example.com", "consent_at": T0, "enabled": 1},
        ]
        self.rows: dict[str, dict] = {}
        self.evidence: list[dict] = []
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, action, payload=None):
        payload = payload or {}
        self.calls.append((action, json.loads(json.dumps(payload))))
        if action == "operations_status":
            return {"workers": [{"id": w.id, "name": w.name, "priority": w.priority, "keyId": w.key_id, "publicKey": json.loads(w.text),
                                 "publicKeySha256": w.fingerprint, "lastSeen": int(time.time() * 1000), "enabled": True} for w in self.workers],
                    "roster": self.roster,
                    "deliveries": [{"id": i, "receipt_at": r.get("receipt_at"), "committed": 1 if r.get("envelope") else 0}
                                   for i, r in self.rows.items() if i in payload.get("deliveryIds", [])],
                    "evidence": self.evidence, "remoteControl": {"enabled": False, "writes": False}}
        if action == "prepare":
            profile = next((p for p in self.roster if p["id"] == payload.get("id")), None) if payload.get("id") else \
                next((p for p in self.roster if p["email"] == payload["email"]), None)
            if profile is None:
                if not payload.get("create"):
                    raise sync.SyncError(409, "Daybreak refused: Candidate is not in Daybreak.")
                profile = {"id": str(uuid.uuid4()), "name": payload["name"], "email": payload["email"], "consent_at": "now", "enabled": 1}
                self.roster.append(profile)
            if profile["email"] != payload["email"]:
                raise sync.SyncError(409, "Daybreak refused: Candidate account does not match.")
            expires = int(time.time() * 1000) + 15 * 60000
            slots = []
            for w in self.workers:
                slot = {"id": str(uuid.uuid4()), "workerId": w.id, "keyId": w.key_id, "publicKey": json.loads(w.text),
                        "email": profile["email"], "candidateId": profile["id"], "expiresAt": expires}
                self.rows[slot["id"]] = {"worker": w, "profile_id": profile["id"], "email": profile["email"], "expires": expires}
                slots.append(slot)
            return {"candidate": {"id": profile["id"], "name": profile["name"], "email": profile["email"]}, "deliveries": slots}
        if action == "commit":
            envelopes = payload["envelopes"]
            assert {e["workerId"] for e in envelopes} == {w.id for w in self.workers}, "all workers or none"
            for e in envelopes:
                row = self.rows[e["enrollmentId"]]
                assert (e["candidateId"], e["email"], e["workerId"], e["expiresAt"]) == (row["profile_id"], row["email"], row["worker"].id, row["expires"])
                row["envelope"] = e
            return {"accepted": True, "status": "awaiting_worker_receipts"}
        raise AssertionError(action)

    def collect(self, worker_name=None):
        """Workers collect and acknowledge: each opens its own copy; Daybreak records the receipt."""
        opened = {}
        for row in self.rows.values():
            if row.get("envelope") and not row.get("receipt_at") and worker_name in (None, row["worker"].name):
                opened[(row["worker"].name, row["profile_id"])] = row["worker"].open(row["envelope"])
                row["receipt_at"] = int(time.time() * 1000)
        return opened

    def run(self, profile, worker, *, minutes=0, state="success", code="saved", same_account=1):
        at = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        w = next(x for x in self.workers if x.name == worker)
        self.evidence.append({"profile_id": profile, "kind": "update", "state": state, "result_code": code,
                              "runner_worker": w.id, "completed_at": at, "same_account": same_account})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    accounts = tmp_path / "data_room" / "credentials.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps({"service_accounts": [dict(r) for r in ACCOUNTS]}), encoding="utf-8")
    monkeypatch.setattr(creds, "_FILE", str(accounts))
    monkeypatch.setattr(sync, "_FILE", str(tmp_path / "data_room" / "daybreak_sync.json"))
    monkeypatch.setenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    creds.migrate_service_accounts()  # the passwords are encrypted at rest, as in production
    daybreak = FakeDaybreak()
    monkeypatch.setattr(sync, "_daybreak", daybreak)
    audits = []
    from core import recruitment_mail_store
    monkeypatch.setattr(recruitment_mail_store, "audit", lambda **kw: audits.append(kw))
    admin = {"username": "admin", "role": "admin"}

    def require_admin(request):
        if not admin.get("username"):
            raise HTTPException(status_code=403, detail="Only the admin")
        return admin

    monkeypatch.setattr(routes, "_require_admin", require_admin)
    app = FastAPI()
    app.include_router(routes.router)
    return {"client": TestClient(app), "daybreak": daybreak, "audits": audits, "admin": admin,
            "store": tmp_path / "data_room" / "daybreak_sync.json"}


def view(env) -> dict:
    data = sync._load()
    if data.get("snapshot"):  # the view re-reads Daybreak once its copy is a minute old; tests need it now
        data["snapshot"]["fetched_at"] = None
        sync._save(data)
    body = env["client"].get("/data-room/daybreak").json()
    return {row["account_id"]: row for row in body["accounts"]} | {"_": body}


def approve_all(env):
    for w in env["daybreak"].workers:
        res = env["client"].post(f"/data-room/daybreak/workers/{w.key_id}/approve", json={"fingerprint": w.fingerprint[:16].upper()})
        assert res.status_code == 200 and res.json()["worker"]["approved"], res.text


def deliver(env, *ids, **kw):
    return env["client"].post("/data-room/daybreak/deliveries", json={"account_ids": list(ids), **kw})


def no_secret_in(*blobs):
    text = "".join(blobs)
    assert not any(secret in text for secret in SECRETS)


class TestOperationsIdentity:
    def test_requests_are_signed_with_a_key_derived_from_the_credential_key(self, monkeypatch):
        monkeypatch.setenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
        sent = {}

        class Response:
            status_code = 200

            def json(self):
                return {"ok": True}

        import httpx
        monkeypatch.setattr(httpx, "post", lambda url, content, headers, **kw: sent.update(url=url, body=content, headers=headers) or Response())
        monkeypatch.setenv("DAYBREAK_SITE_URL", "https://daybreak.example.test/")
        assert sync._daybreak("operations_status", {"deliveryIds": []}) == {"ok": True}
        h = sent["headers"]
        assert sent["url"] == "https://daybreak.example.test/api/credentials" and h["Content-Type"] == "application/json"
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(sync.intake_public_key()))
        key.verify(base64.b64decode(h["X-Operations-Signature"]),
                   sync.signed_message("POST", "/api/credentials", h["X-Operations-Timestamp"], h["X-Operations-Nonce"], sent["body"]))
        assert sync.intake_public_key() == sync.intake_public_key()  # deterministic: no new secret to store

    def test_the_public_key_is_public_and_nothing_else_is(self, env):
        from core import dashboard_auth_vps as auth
        body = env["client"].get("/api/integrations/daybreak/intake-key").json()
        assert body["public_key"] == sync.intake_public_key() and body["algorithm"] == "Ed25519"
        assert auth.is_public_path("/api/integrations/daybreak/intake-key")
        assert not auth.is_public_path("/data-room/daybreak") and not auth.is_public_path("/data-room/daybreak/deliveries")
        env["admin"]["username"] = None
        assert env["client"].get("/data-room/daybreak").status_code == 403
        assert deliver(env, "asha_rao").status_code == 403


class TestWorkerApproval:
    def test_only_the_fingerprint_shown_on_the_laptop_approves(self, env):
        rtx = env["daybreak"].workers[0]
        approve = lambda value: env["client"].post(f"/data-room/daybreak/workers/{rtx.key_id}/approve", json={"fingerprint": value})
        assert approve("0" * 16).status_code == 400 and approve(rtx.fingerprint[:10]).status_code == 400
        assert approve(rtx.fingerprint).json()["worker"]["approved"]
        assert env["client"].post(f"/data-room/daybreak/workers/{rtx.key_id}/revoke").json()["worker"]["approved"] is False

    def test_nothing_is_sealed_until_every_enrolled_worker_is_approved(self, env):
        rtx = env["daybreak"].workers[0]
        env["client"].post(f"/data-room/daybreak/workers/{rtx.key_id}/approve", json={"fingerprint": rtx.fingerprint})
        res = deliver(env, "asha_rao").json()
        assert res["delivered"] == [] and "Jagadeesh's key is not approved" in res["refused"][0]["detail"]
        assert [a for a, _ in env["daybreak"].calls].count("commit") == 0

    def test_a_key_replaced_after_approval_is_not_trusted(self, env):
        approve_all(env)
        jag, replaced = env["daybreak"].workers[1], Worker("Jagadeesh", 1)
        jag.private, jag.jwk, jag.text, jag.fingerprint = replaced.private, replaced.jwk, replaced.text, replaced.fingerprint
        res = deliver(env, "asha_rao").json()
        assert res["delivered"] == [] and "not approved" in res["refused"][0]["detail"]
        assert next(w for w in view(env)["_"]["workers"] if w["name"] == "Jagadeesh")["approved"] is False


class TestReconciliation:
    def test_exact_normalised_address_matching_and_every_problem_named(self, env):
        rows = view(env)
        assert rows["asha_rao"]["status"] == "synced" and rows["asha_rao"]["profile"]["id"] == P["asha"]
        assert rows["ravi_kumar"]["synced"] and rows["ravi_kumar"]["notes"] == ["name_differs"]
        assert rows["new_person"]["attention_codes"] == ["missing_in_daybreak"] and rows["new_person"]["can_add_to_daybreak"]
        assert rows["twin_a"]["attention_codes"] == ["duplicate_in_data_room"] == rows["twin_b"]["attention_codes"]
        assert rows["phone_login"]["attention_codes"] == ["invalid_username"]
        assert rows["dupe_daybreak"]["attention_codes"] == ["duplicate_in_daybreak"]
        assert rows["no_consent"]["attention_codes"] == ["consent_missing"]
        assert rows["cased"]["attention_codes"] == ["daybreak_address_not_normalised"]
        assert "telegram" not in rows
        body = rows["_"]
        assert [p["id"] for p in body["daybreak_only"]] == [P["only"]]
        assert body["reconciliation"] == {"data_room_accounts": 9, "matched": 4, "missing_in_daybreak": 1, "duplicates": 3,
                                          "invalid_usernames": 1, "names_differ": 1, "daybreak_only": 1}


class TestDelivery:
    def test_sealed_per_worker_in_daybreaks_format_and_never_written_anywhere(self, env):
        approve_all(env)
        res = deliver(env, "asha_rao", "twin_a", "new_person", "telegram", "cased").json()
        assert res["delivered"] == ["asha_rao"]
        assert {r["account_id"]: r["reason"] for r in res["refused"]} == {
            "twin_a": "duplicate_in_data_room", "new_person": "missing_in_daybreak", "telegram": "not_a_naukri_account",
            "cased": "daybreak_address_not_normalised"}
        prepare = [p for a, p in env["daybreak"].calls if a == "prepare"]
        assert prepare == [{"name": "Asha Rao", "email": "asha.rao@example.com", "consent": True, "id": P["asha"]}]  # never "create"
        assert env["daybreak"].collect() == {("RTX 4060", P["asha"]): "Asha-Pass-1", ("Jagadeesh", P["asha"]): "Asha-Pass-1"}
        commits = json.dumps([p for a, p in env["daybreak"].calls if a == "commit"])
        no_secret_in(commits, env["store"].read_text(encoding="utf-8"), json.dumps(env["audits"]), env["client"].get("/data-room/daybreak").text)

    def test_the_bound_fields_cannot_change_in_transit(self, env):
        approve_all(env)
        deliver(env, "asha_rao")
        row = next(r for r in env["daybreak"].rows.values() if r.get("envelope"))
        with pytest.raises(Exception):
            row["worker"].open({**row["envelope"], "email": "someone@example.com"})
        other = next(w for w in env["daybreak"].workers if w is not row["worker"])
        with pytest.raises(Exception):
            other.open(row["envelope"])

    def test_adding_an_account_needs_confirmed_consent(self, env):
        approve_all(env)
        assert env["client"].post("/data-room/daybreak/deliveries", json={"create_ids": ["new_person"]}).status_code == 400
        res = env["client"].post("/data-room/daybreak/deliveries", json={"create_ids": ["new_person"], "consent_confirmed": True}).json()
        assert res["delivered"] == ["new_person"]
        assert [p for a, p in env["daybreak"].calls if a == "prepare"][-1] == {
            "name": "New Person", "email": "new.person@example.com", "consent": True, "create": True}
        env["daybreak"].collect()
        assert view(env)["new_person"]["status"] == "delivered"

    def test_at_most_ten_per_request(self, env):
        approve_all(env)
        assert deliver(env, *[f"a{i}" for i in range(11)]).status_code == 400


class TestStatuses:
    def test_delivered_only_when_every_worker_acknowledged_the_current_password(self, env):
        approve_all(env)
        deliver(env, "asha_rao")
        row = view(env)["asha_rao"]
        assert row["status"] == "synced" and row["delivery"]["waiting_for"] == ["RTX 4060", "Jagadeesh"]
        env["daybreak"].collect("RTX 4060")
        assert view(env)["asha_rao"]["delivery"]["waiting_for"] == ["Jagadeesh"]
        env["daybreak"].collect("Jagadeesh")
        row = view(env)["asha_rao"]
        assert row["status"] == "delivered" and {r["worker"] for r in row["delivery"]["receipts"]} == {"RTX 4060", "Jagadeesh"}
        creds.update_service_account("asha_rao", {"password": "Asha-Changed-9"})
        assert "changed_since_delivery" in view(env)["asha_rao"]["attention_codes"]

    def test_an_uncollected_delivery_expires_into_attention(self, env, monkeypatch):
        approve_all(env)
        deliver(env, "asha_rao")
        later = datetime.now(timezone.utc) + timedelta(minutes=16)
        monkeypatch.setattr(sync, "_now", lambda: later)
        assert "delivery_expired" in view(env)["asha_rao"]["attention_codes"]

    def test_connected_needs_a_real_sign_in_after_that_workers_receipt(self, env):
        approve_all(env)
        deliver(env, "asha_rao")
        env["daybreak"].collect()
        day = env["daybreak"]
        day.run(P["asha"], "RTX 4060", minutes=-30)                     # before the delivery: does not count
        day.run(P["asha"], "RTX 4060", minutes=1, same_account=0)       # another account on screen: does not count
        assert view(env)["asha_rao"]["status"] == "delivered"
        day.run(P["asha"], "RTX 4060", minutes=2, state="skipped", code="unchanged")  # signed in, nothing to save
        row = view(env)["asha_rao"]
        assert row["status"] == "connected" and row["connection"]["worker"] == "RTX 4060"
        day.run(P["asha"], "Jagadeesh", minutes=3, state="attention", code="login")
        row = view(env)["asha_rao"]
        assert row["status"] == "needs_attention" and row["attention"] == ["Naukri refused the password"]
        day.run(P["asha"], "Jagadeesh", minutes=4)
        assert view(env)["asha_rao"]["status"] == "connected"
