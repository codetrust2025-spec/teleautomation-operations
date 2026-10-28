"""Data Room -> Daybreak credential delivery, driven through the real routes with synthetic logins only.

A test computer holds real Ed25519/X25519 keys, signs its check-ins exactly as the companion does and opens the sealed
envelopes itself. What is checked: a computer gets nothing until an admin types its fingerprint; passwords leave only
sealed to that computer, never in a response, an audit row or the store file; matching is exact on the normalised
address; Connected needs a real sign-in reported after the delivery; RTX/Jagadeesh need a confirmed cutover.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import daybreak_credential_sync as routes
from features import daybreak_credential_sync as sync
from features import data_room_credentials_store as creds

TOKEN = "t" * 40
VECTOR = Path(__file__).parent / "fixtures" / "daybreak_envelope_vector.json"

# Invented people and secrets. Daybreak's roster spells two addresses differently on purpose (case, spaces).
ACCOUNTS = [
    {"id": "asha_rao", "label": "Asha Rao", "service": "Naukri", "username": "Asha.Rao@Example.com ", "password": "Asha-Pass-1"},
    {"id": "ravi_kumar", "label": "Ravi Kumar", "service": "Naukri", "username": "ravi@example.com", "password": "Ravi=2"},
    {"id": "new_person", "label": "New Person", "service": "Naukri", "username": "new.person@example.com", "password": "New#3"},
    {"id": "twin_a", "label": "Twin A", "service": "Naukri", "username": "twin@example.com", "password": "twin-a"},
    {"id": "twin_b", "label": "Twin B", "service": "Naukri", "username": "TWIN@example.com", "password": "twin-b"},
    {"id": "phone_login", "label": "Phone Login", "service": "Naukri", "username": "+91 90000 00000", "password": "phone-4"},
    {"id": "dupe_daybreak", "label": "Dupe", "service": "Naukri", "username": "dupe@example.com", "password": "dupe-5"},
    {"id": "no_consent", "label": "No Consent", "service": "Naukri", "username": "noconsent@example.com", "password": "nc-6"},
    {"id": "telegram", "label": "Telegram login", "service": "Telegram", "username": "tg@example.com", "password": "tg-7"},
]
P_ASHA, P_RAVI, P_DUPE1, P_DUPE2, P_NC, P_ONLY = (f"{n:08x}-0000-4000-8000-000000000000" for n in range(1, 7))
ROSTER = [
    {"id": P_ASHA, "name": "Asha Rao", "email": "asha.rao@example.com", "enabled": True, "consent": True},
    {"id": P_RAVI, "name": "Ravi K", "email": " Ravi@Example.com", "enabled": True, "consent": True},
    {"id": P_DUPE1, "name": "Dupe One", "email": "dupe@example.com", "enabled": True, "consent": True},
    {"id": P_DUPE2, "name": "Dupe Two", "email": "Dupe@example.com", "enabled": True, "consent": True},
    {"id": P_NC, "name": "No Consent", "email": "noconsent@example.com", "enabled": False, "consent": False},
    {"id": P_ONLY, "name": "Only In Daybreak", "email": "only@example.com", "enabled": True, "consent": True},
]


class Computer:
    """What the companion does, in Python: keys, signed check-ins, opening envelopes, receipts."""

    def __init__(self, client: TestClient, name="Praveen", kind="praveen"):
        self.client, self.name, self.kind = client, name, kind
        self.sign = Ed25519PrivateKey.generate()
        self.box = X25519PrivateKey.generate()
        raw = serialization.Encoding.Raw, serialization.PublicFormat.Raw
        self.sign_pub = self.sign.public_key().public_bytes(*raw)
        self.box_pub = self.box.public_key().public_bytes(*raw)
        self.id = sync.executor_id(self.sign_pub)
        self.fingerprint = sync.fingerprint(self.sign_pub, self.box_pub)

    def _post(self, path, payload, *, executor=True, token=TOKEN, timestamp=None, nonce=None, tamper=False):
        body = json.dumps(payload).encode()
        ts = str(timestamp if timestamp is not None else int(time.time() * 1000))
        nonce = nonce or os.urandom(16).hex()
        signature = self.sign.sign(sync.signed_message("POST", path, ts, nonce, body + (b" " if tamper else b"")))
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Daybreak-Timestamp": ts,
                   "X-Daybreak-Nonce": nonce, "X-Daybreak-Signature": base64.b64encode(signature).decode()}
        if executor:
            headers["X-Daybreak-Executor"] = self.id
        return self.client.post(path, content=body, headers=headers)

    def register(self, **kw):
        return self._post("/api/integrations/daybreak/executor/register", {
            "name": self.name, "kind": self.kind, "sign_pub": base64.b64encode(self.sign_pub).decode(),
            "box_pub": base64.b64encode(self.box_pub).decode()}, executor=False, **kw)

    def check_in(self, roster=ROSTER, receipts=None, events=None, mode="praveen", worker=None, **kw):
        return self._post("/api/integrations/daybreak/executor/sync", {
            "v": 1, "daybreak": {"mode": mode, "active_worker": worker}, "roster": roster,
            "receipts": receipts or [], "events": events or []}, **kw)

    def open(self, envelope) -> str:
        epk = base64.b64decode(envelope["epk"])
        shared = self.box.exchange(X25519PublicKey.from_public_bytes(epk))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=epk + self.box_pub, info=b"daybreak credential delivery v1").derive(shared)
        plain = AESGCM(key).decrypt(base64.b64decode(envelope["iv"]), base64.b64decode(envelope["ct"]), sync.canonical(envelope["aad"]))
        return json.loads(plain)["password"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    accounts = tmp_path / "data_room" / "credentials.json"
    accounts.parent.mkdir(parents=True)
    accounts.write_text(json.dumps({"service_accounts": [dict(r) for r in ACCOUNTS]}), encoding="utf-8")
    monkeypatch.setattr(creds, "_FILE", str(accounts))
    monkeypatch.setattr(sync, "_FILE", str(tmp_path / "data_room" / "daybreak_sync.json"))
    monkeypatch.setenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DAYBREAK_HEADLINE_TOKEN", TOKEN)
    sync._seen_nonces.clear()
    creds.migrate_service_accounts()  # the passwords are encrypted at rest, as in production
    audits = []
    from core import recruitment_mail_store
    monkeypatch.setattr(recruitment_mail_store, "audit", lambda **kw: audits.append(kw))
    admin = {"username": "admin", "role": "admin"}
    monkeypatch.setattr(routes, "_require_admin", lambda request: admin if admin.get("username") else (_ for _ in ()).throw(
        __import__("fastapi").HTTPException(status_code=403, detail="Only the admin")))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    return {"client": client, "audits": audits, "admin": admin, "accounts": accounts,
            "store": tmp_path / "data_room" / "daybreak_sync.json"}


def approved(env, **kw) -> Computer:
    computer = Computer(env["client"], **kw)
    assert computer.register().json()["status"] == "pending"
    res = env["client"].post(f"/data-room/daybreak/executors/{computer.id}/approve", json={"fingerprint": computer.fingerprint.lower()})
    assert res.status_code == 200, res.text
    assert computer.check_in().status_code == 200
    return computer


def status(env) -> dict:
    body = env["client"].get("/data-room/daybreak").json()
    return {row["account_id"]: row for row in body["accounts"]} | {"_": body}


def deliver(env, *ids, **kw):
    return env["client"].post("/data-room/daybreak/deliveries", json={"account_ids": list(ids), **kw})


def receipt(computer, delivery, password, status_="stored", profile_id=None):
    return {"delivery_id": delivery["delivery_id"], "status": status_, "profile_id": profile_id or delivery["envelope"]["aad"]["profile_id"],
            "proof": sync.receipt_proof(delivery["delivery_id"], password)}


SECRETS = [r["password"].strip() for r in ACCOUNTS]


def no_secret_in(*blobs):
    text = "".join(blobs)
    assert not any(secret in text for secret in SECRETS)


class TestAComputerMustBeApprovedInPerson:
    def test_registration_waits_and_gets_nothing(self, env):
        computer = Computer(env["client"])
        body = computer.register().json()
        assert body == {"executor_id": computer.id, "fingerprint": computer.fingerprint, "status": "pending", "active": False}
        res = computer.check_in().json()
        assert res["status"] == "pending" and res["deliveries"] == []
        assert status(env)["_"]["roster_reported_at"] is None  # an unapproved computer's roster is not believed

    def test_a_wrong_fingerprint_is_refused(self, env):
        computer = Computer(env["client"])
        computer.register()
        res = env["client"].post(f"/data-room/daybreak/executors/{computer.id}/approve", json={"fingerprint": "0000-0000-0000-0000-0000"})
        assert res.status_code == 400 and "does not match" in res.json()["detail"]

    @pytest.mark.parametrize("change", [
        dict(token="wrong" * 8), dict(tamper=True), dict(timestamp=int(time.time() * 1000) - 400_000),
    ])
    def test_requests_need_the_token_and_a_fresh_valid_signature(self, env, change):
        computer = approved(env)
        assert computer.check_in(**change).status_code == 401

    def test_a_nonce_is_used_once(self, env):
        computer = approved(env)
        assert computer.check_in(nonce="a" * 32).status_code == 200
        assert computer.check_in(nonce="a" * 32).status_code == 401

    def test_another_key_cannot_speak_for_an_approved_computer(self, env):
        computer, impostor = approved(env), Computer(env["client"])
        impostor.id = computer.id
        assert impostor.check_in().status_code == 401

    def test_only_the_admin_manages_delivery(self, env):
        env["admin"]["username"] = None
        assert env["client"].get("/data-room/daybreak").status_code == 403
        assert deliver(env, "asha_rao").status_code == 403

    def test_the_executor_paths_are_the_only_public_ones(self):
        from core import dashboard_auth_vps as auth
        assert auth.is_public_path("/api/integrations/daybreak/executor/sync")
        assert auth.is_public_path("/api/integrations/daybreak/executor/register")
        assert not auth.is_public_path("/data-room/daybreak")
        assert not auth.is_public_path("/data-room/daybreak/deliveries")

    def test_rtx_needs_a_confirmed_cutover_that_daybreak_itself_reports(self, env):
        praveen = approved(env)
        rtx = Computer(env["client"], name="RTX 4060", kind="rtx")
        rtx.register()
        approve = lambda **kw: env["client"].post(f"/data-room/daybreak/executors/{rtx.id}/approve", json={"fingerprint": rtx.fingerprint, **kw})
        assert approve().status_code == 409
        rtx.check_in(mode="praveen")
        assert "does not report" in approve(cutover_confirmed=True).json()["detail"]
        rtx.check_in(mode="remote", worker="RTX 4060")
        assert approve(cutover_confirmed=True).json()["executor"]["active"] is True
        executors = {e["id"]: e for e in status(env)["_"]["executors"]}
        assert executors[praveen.id]["active"] is False and executors[rtx.id]["active"] is True


class TestReconciliation:
    def test_exact_normalised_address_matching_and_every_problem_named(self, env):
        approved(env)
        rows = status(env)
        assert rows["asha_rao"]["status"] == "synced" and rows["asha_rao"]["profile"]["id"] == P_ASHA
        assert rows["ravi_kumar"]["synced"] and rows["ravi_kumar"]["notes"] == ["name_differs"]
        assert rows["new_person"]["attention_codes"] == ["missing_in_daybreak"] and rows["new_person"]["can_add_to_daybreak"]
        assert rows["twin_a"]["attention_codes"] == ["duplicate_in_data_room"] == rows["twin_b"]["attention_codes"]
        assert rows["phone_login"]["attention_codes"] == ["invalid_username"]
        assert rows["dupe_daybreak"]["attention_codes"] == ["duplicate_in_daybreak"]
        assert rows["no_consent"]["attention_codes"] == ["consent_missing"]
        assert "telegram" not in rows  # only Naukri accounts
        body = rows["_"]
        assert [p["id"] for p in body["daybreak_only"]] == [P_ONLY]
        assert body["reconciliation"] == {"data_room_accounts": 8, "matched": 3, "missing_in_daybreak": 1, "duplicates": 3,
                                          "invalid_usernames": 1, "names_differ": 1, "daybreak_only": 1}

    def test_without_an_approved_computer_everything_needs_attention(self, env):
        rows = status(env)
        assert rows["asha_rao"]["status"] == "needs_attention" and rows["asha_rao"]["attention_codes"][0] == "no_computer"


class TestDelivery:
    def test_sealed_to_the_computer_only_and_never_written_anywhere(self, env):
        computer = approved(env)
        res = deliver(env, "asha_rao", "twin_a", "new_person", "telegram")
        assert res.json()["queued"] == ["asha_rao"]
        assert {r["account_id"]: r["reason"] for r in res.json()["refused"]} == {
            "twin_a": "duplicate_in_data_room", "new_person": "missing_in_daybreak", "telegram": "not_a_naukri_account"}
        got = computer.check_in().json()["deliveries"]
        assert len(got) == 1
        envelope = got[0]["envelope"]
        assert envelope["aad"] | {} == {**envelope["aad"], "executor_id": computer.id, "account_id": "asha_rao",
                                        "profile_id": P_ASHA, "email": "asha.rao@example.com", "create": False}
        assert computer.open(envelope) == "Asha-Pass-1"
        no_secret_in(json.dumps(got), env["store"].read_text(encoding="utf-8"), json.dumps(env["audits"]),
                     env["client"].get("/data-room/daybreak").text)
        # The bound fields cannot be changed in transit: another account's AAD fails to open.
        with pytest.raises(Exception):
            computer.open({**envelope, "aad": {**envelope["aad"], "account_id": "ravi_kumar"}})
        # Another computer's key cannot open it either.
        with pytest.raises(Exception):
            Computer(env["client"]).open(envelope)

    def test_receipt_with_the_right_proof_marks_delivered(self, env):
        computer = approved(env)
        deliver(env, "asha_rao")
        got = computer.check_in().json()["deliveries"][0]
        computer.check_in(receipts=[receipt(computer, got, computer.open(got["envelope"]))])
        row = status(env)["asha_rao"]
        assert row["status"] == "delivered" and row["delivery"]["state"] == "stored" and not row["connected"]
        assert any(a["action"] == "daybreak_credential_receipt" and a["new"]["proof"] is True for a in env["audits"])

    def test_a_wrong_proof_is_rejected(self, env):
        computer = approved(env)
        deliver(env, "asha_rao")
        got = computer.check_in().json()["deliveries"][0]
        computer.check_in(receipts=[receipt(computer, got, "not-the-password")])
        row = status(env)["asha_rao"]
        assert row["status"] == "needs_attention" and row["delivery"]["state"] == "rejected"

    def test_differs_is_never_overwritten_without_replace(self, env):
        computer = approved(env)
        deliver(env, "asha_rao")
        got = computer.check_in().json()["deliveries"][0]
        computer.check_in(receipts=[{"delivery_id": got["delivery_id"], "status": "differs", "profile_id": P_ASHA}])
        assert "differs" in status(env)["asha_rao"]["attention_codes"]
        env["client"].post("/data-room/daybreak/deliveries", json={"replace_ids": ["asha_rao"]})
        again = computer.check_in().json()["deliveries"][0]
        assert again["envelope"]["aad"]["replace"] is True

    def test_one_open_delivery_per_account(self, env):
        computer = approved(env)
        deliver(env, "asha_rao")
        deliver(env, "asha_rao", verify_ids=["asha_rao"])
        got = computer.check_in().json()["deliveries"]
        assert len(got) == 1 and got[0]["envelope"]["aad"]["verify"] is True
        assert sum(1 for d in json.loads(env["store"].read_text())["deliveries"].values() if d["account_id"] == "asha_rao") == 1

    def test_a_password_changed_later_needs_delivering_again(self, env):
        computer = approved(env)
        deliver(env, "asha_rao")
        got = computer.check_in().json()["deliveries"][0]
        computer.check_in(receipts=[receipt(computer, got, computer.open(got["envelope"]))])
        creds.update_service_account("asha_rao", {"password": "Asha-New-9"})
        SECRETS.append("Asha-New-9")
        row = status(env)["asha_rao"]
        assert row["status"] == "needs_attention" and "changed_since_delivery" in row["attention_codes"]

    def test_unanswered_sends_are_retried_then_given_up(self, env, monkeypatch):
        computer = approved(env)
        deliver(env, "ravi_kumar")
        clock = [datetime.now(timezone.utc)]
        monkeypatch.setattr(sync, "_now", lambda: clock[0])
        sends = 0
        for _ in range(5):
            sends += len(computer.check_in().json()["deliveries"])
            clock[0] += timedelta(minutes=11)
        assert sends == sync.MAX_SENDS
        assert status(env)["ravi_kumar"]["delivery"]["state"] == "rejected"

    def test_revoking_stops_everything(self, env):
        computer = approved(env)
        deliver(env, "asha_rao")
        env["client"].post(f"/data-room/daybreak/executors/{computer.id}/revoke")
        assert computer.check_in().json() == {"status": "revoked", "active": False, "deliveries": [], "fingerprint": computer.fingerprint}


class TestAddingAnAccountDaybreakLacks:
    def test_needs_confirmed_consent_and_is_delivered_as_a_create(self, env):
        computer = approved(env)
        assert env["client"].post("/data-room/daybreak/deliveries", json={"create_ids": ["new_person"]}).status_code == 400
        res = env["client"].post("/data-room/daybreak/deliveries", json={"create_ids": ["new_person"], "consent_confirmed": True})
        assert res.json()["queued"] == ["new_person"]
        got = computer.check_in().json()["deliveries"][0]
        aad = got["envelope"]["aad"]
        assert aad["create"] is True and aad["profile_id"] is None and aad["verify"] is True and aad["name"] == "New Person"
        new_id = "0000000a-0000-4000-8000-000000000000"
        roster = ROSTER + [{"id": new_id, "name": "New Person", "email": "new.person@example.com", "consent": True, "enabled": True}]
        computer.check_in(roster=roster, receipts=[{**receipt(computer, got, computer.open(got["envelope"]), "created", new_id), "verify": "queued"}])
        row = status(env)["new_person"]
        assert row["status"] == "delivered" and row["profile"]["id"] == new_id and row["delivery"]["verify_result"] == "queued"


class TestConnectedNeedsARealSignIn:
    def _delivered(self, env):
        computer = approved(env)
        deliver(env, "asha_rao", verify_ids=["asha_rao"])
        got = computer.check_in().json()["deliveries"][0]
        computer.check_in(receipts=[receipt(computer, got, computer.open(got["envelope"]))])
        return computer

    def event(self, minutes=1, **kw):
        at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        return {"profile_id": P_ASHA, "job_id": "000000ff-0000-4000-8000-000000000000", "kind": "preview",
                "outcome": "success", "code": "previewed", "signed_in": True, "password_used": False,
                "at": at.isoformat(), **kw}

    def test_a_sign_in_before_the_delivery_does_not_count(self, env):
        computer = self._delivered(env)
        computer.check_in(events=[self.event(minutes=-30)])
        assert status(env)["asha_rao"]["status"] == "delivered"

    def test_a_run_that_did_not_sign_in_does_not_count(self, env):
        computer = self._delivered(env)
        computer.check_in(events=[self.event(signed_in=False, outcome="attention", code="screen")])
        assert status(env)["asha_rao"]["status"] == "delivered"

    def test_a_sign_in_after_the_delivery_is_connected_and_a_later_refusal_is_attention(self, env):
        computer = self._delivered(env)
        computer.check_in(events=[self.event(password_used=True)])
        row = status(env)["asha_rao"]
        assert row["status"] == "connected" and row["connection"]["password_used"] is True
        computer.check_in(events=[self.event(minutes=2, signed_in=False, outcome="attention", code="login",
                                             job_id="000000fe-0000-4000-8000-000000000000")])
        row = status(env)["asha_rao"]
        assert row["status"] == "needs_attention" and row["attention"] == ["Naukri refused the password"]


def test_the_envelope_vector_opens_with_its_keys():
    """The fixed vector the companion's own tests open: the sealing format both sides share."""
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    box = X25519PrivateKey.from_private_bytes(base64.b64decode(vector["box_private_raw"]))
    computer = Computer.__new__(Computer)
    computer.box = box
    computer.box_pub = box.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert computer.open(vector["envelope"]) == vector["password"]
    assert sync.receipt_proof(vector["envelope"]["aad"]["delivery_id"], vector["password"]) == vector["proof"]


COMPANION = os.environ.get("DAYBREAK_COMPANION_DIR", "")


@pytest.mark.skipif(not (COMPANION and shutil.which("node")), reason="needs the Daybreak companion checkout (DAYBREAK_COMPANION_DIR) and node")
def test_the_real_companion_opens_what_operations_seals(env, tmp_path):
    """Python seals with seal(); the companion's own openEnvelope (Node) opens it and computes the same proof."""
    script = tmp_path / "open.mjs"
    module = Path(COMPANION, "credential-sync.mjs").resolve().as_uri()
    script.write_text(
        f"import {{createIdentity,publicIdentity,openEnvelope,receiptProof}} from '{module}';\n"
        "import {readFileSync,writeFileSync} from 'node:fs';\n"
        "const [mode,file]=process.argv.slice(2);\n"
        "if(mode==='keys'){const id=createIdentity();writeFileSync(file,JSON.stringify({id,pub:publicIdentity(id)}));}\n"
        "else{const s=JSON.parse(readFileSync(file,'utf8'));const o=openEnvelope(s.envelope,{identity:s.id,executorId:s.pub.id});"
        "process.stdout.write(JSON.stringify({ok:o.password===s.expect,proof:receiptProof(o.aad.delivery_id,o.password)}));}\n",
        encoding="utf-8")
    keys = tmp_path / "keys.json"
    subprocess.run(["node", str(script), "keys", str(keys)], check=True)
    ident = json.loads(keys.read_text())
    sign_pub, box_pub = base64.b64decode(ident["pub"]["signPub"]), base64.b64decode(ident["pub"]["boxPub"])
    assert sync.executor_id(sign_pub) == ident["pub"]["id"] and sync.fingerprint(sign_pub, box_pub) == ident["pub"]["fingerprint"]
    secret = "Ünïcode \"quoted\" \\ pass\n"
    aad = {"delivery_id": "dlv_x", "executor_id": ident["pub"]["id"], "account_id": "a", "profile_id": None,
           "email": "a@example.com", "name": "Ä \"N\"", "create": True, "replace": False, "verify": True,
           "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    envelope = sync.seal(secret, box_pub=box_pub, aad=aad)
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"envelope": envelope, "id": ident["id"], "pub": ident["pub"], "expect": secret}), encoding="utf-8")
    out = json.loads(subprocess.run(["node", str(script), "open", str(case)], check=True, capture_output=True, text=True).stdout)
    assert out == {"ok": True, "proof": sync.receipt_proof("dlv_x", secret)}
    case.unlink()
