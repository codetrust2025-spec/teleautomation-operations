"""No automatic creation, no duplicate delivery, and no weakening of worker approval."""
from __future__ import annotations

from services import daybreak_credential_auto_sync as auto


def worker(*, approved=True, enabled=True, recipient=True):
    return {"name": "RTX 4060", "enabled": enabled, "recipient": recipient,
            "approved": approved, "key_problem": None}


def account(i, *, codes=None, delivery=None, synced=True, enabled=True):
    return {"account_id": str(i), "synced": synced, "profile": {"enabled": enabled},
            "attention_codes": codes or [], "delivery": delivery}


def test_28_source_credentials_only_24_existing_daybreak_matches_are_eligible():
    view = {"workers": [worker()], "accounts": [
        *[account(i) for i in range(24)],
        *[account(i, codes=["missing_in_daybreak"], synced=False) for i in range(24, 28)],
    ]}
    first, blocked = auto.plan(view)
    assert blocked is None
    assert first == [str(i) for i in range(10)]  # per-tick limit; no candidate creation
    assert not set(first) & {str(i) for i in range(24, 28)}


def test_no_auto_delivery_for_quarantined_disabled_unconsented_or_duplicate_account():
    rows = [
        account(1, enabled=False),
        account(2, codes=["consent_missing"]),
        account(3, codes=["duplicate_in_data_room"]),
        account(4, codes=["missing_in_daybreak"], synced=False),
        account(5, codes=["restricted"]),
        account(6, delivery={"waiting_for": ["RTX 4060"]}),
        account(7, delivery={"receipts": ["RTX 4060", "Jagadeesh"]}),
        account(8),
    ]
    chosen, blocked = auto.plan({"workers": [worker()], "accounts": rows})
    assert blocked is None and chosen == ["8"]


def test_refresh_only_changed_or_expired_deliveries_not_failed_logins():
    rows = [
        account(1, codes=["changed_since_delivery"], delivery={"at": "yesterday"}),
        account(2, codes=["delivery_expired"], delivery={"at": "yesterday"}),
        account(3, codes=["login"], delivery={"at": "yesterday"}),
        account(4, codes=["credentials"], delivery={"at": "yesterday"}),
        account(5, codes=["consent_missing", "changed_since_delivery"], delivery={"at": "yesterday"}),
    ]
    chosen, blocked = auto.plan({"workers": [worker()], "accounts": rows})
    assert blocked is None and chosen == ["1", "2"]


def test_worker_keys_must_be_approved_and_cannot_be_substituted():
    rows = [account(1)]
    for workers in ([], [worker(approved=False)],
                    [worker(), worker(approved=False)]):
        chosen, blocked = auto.plan({"workers": workers, "accounts": rows})
        assert chosen == [] and blocked
    chosen, blocked = auto.plan({"workers": [worker(), worker(approved=False, recipient=False)],
                                  "accounts": rows})
    assert chosen == ["1"] and blocked is None


def stub_roster(monkeypatch, total=28):
    from features import daybreak_credential_sync as sync
    source = [{"id": str(i), "label": f"Person {i}", "username": f"person{i}@example.test"}
              for i in range(total)]
    requests = []
    monkeypatch.setattr(sync, "authoritative_naukri_accounts", lambda: source)
    monkeypatch.setattr(sync, "refresh", lambda **kwargs: {})
    def daybreak(action, payload):
        requests.append((action, payload))
        assert action == "roster_sync"
        assert payload["sourceCount"] == total
        assert len(payload["accounts"]) == total
        assert all(set(item) == {"id", "label", "email", "enabled"} for item in payload["accounts"])
        return {"status": "ok", "sourceCount": total, "matchedCount": total}
    monkeypatch.setattr(sync, "_daybreak", daybreak)
    return requests


def test_sync_once_uses_existing_sealed_delivery_only_and_reports_unmatched(monkeypatch, tmp_path):
    from features import daybreak_credential_sync as sync

    view = {"workers": [worker()], "accounts": [
        *[account(i) for i in range(24)],
        *[account(i, codes=["missing_in_daybreak"], synced=False) for i in range(24, 28)],
    ]}
    calls = []
    monkeypatch.setattr(auto, "DATA_DIR", str(tmp_path))
    requests = stub_roster(monkeypatch)
    monkeypatch.setattr(sync, "status_view", lambda: view)
    def deliver(ids, *, actor):
        calls.append((ids, actor))
        return {"delivered": ids, "refused": []}
    monkeypatch.setattr(sync, "deliver", deliver)
    result = auto.sync_once()
    assert result == {"status": "delivered", "delivered": 10, "refused": 0,
                      "total": 28, "unmatched": 4}
    assert calls == [([str(i) for i in range(10)], "automatic-data-room-sync")]
    assert len(requests) == 1


def test_sync_once_does_not_duplicate_delivered_or_inflight_accounts(monkeypatch, tmp_path):
    from features import daybreak_credential_sync as sync

    monkeypatch.setattr(auto, "DATA_DIR", str(tmp_path))
    stub_roster(monkeypatch, total=2)
    monkeypatch.setattr(sync, "status_view", lambda: {
        "workers": [worker()],
        "accounts": [account(1, delivery={"waiting_for": ["RTX 4060"]}),
                     account(2, delivery={"receipts": ["RTX 4060", "Jagadeesh"]})],
    })
    monkeypatch.setattr(sync, "deliver", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("No duplicate credential deliveries allowed")))
    assert auto.sync_once()["status"] == "up_to_date"


def test_reject_incomplete_daybreak_roster(monkeypatch, tmp_path):
    from features import daybreak_credential_sync as sync
    monkeypatch.setattr(auto, "DATA_DIR", str(tmp_path))
    stub_roster(monkeypatch, total=2)
    monkeypatch.setattr(sync, "_daybreak",
                        lambda action, payload: {"status": "ok", "sourceCount": 2, "matchedCount": 1})
    monkeypatch.setattr(sync, "status_view", lambda: (_ for _ in ()).throw(
        AssertionError("Must verify account mirroring before delivering credentials")))
    import pytest
    with pytest.raises(sync.SyncError, match="did not reconcile"):
        auto.sync_once()


def test_default_is_opt_out_until_ops_release_explicitly_enables(monkeypatch):
    monkeypatch.delenv("DAYBREAK_CREDENTIAL_AUTO_SYNC_ENABLED", raising=False)
    assert not auto.enabled()
    monkeypatch.setenv("DAYBREAK_CREDENTIAL_AUTO_SYNC_ENABLED", "1")
    assert auto.enabled()


def test_missing_or_corrupt_operations_source_does_not_send_a_deletion_snapshot(monkeypatch,tmp_path):
    import pytest
    from features import daybreak_credential_sync as sync
    from features import data_room_credentials_store as creds
    monkeypatch.setattr(auto,"DATA_DIR",str(tmp_path))
    monkeypatch.setattr(creds,"_FILE",str(tmp_path/"missing.json"))
    monkeypatch.setattr(sync,"_daybreak",lambda *a,**k: (_ for _ in ()).throw(AssertionError("Must not send incomplete snapshot")))
    for contents in (None,"{broken}",'{"service_accounts":null}'):
        path=tmp_path/"missing.json"
        if contents is not None:path.write_text(contents)
        with pytest.raises(sync.SyncError,match="source is unavailable"):auto.sync_once()


def test_disabled_source_state_is_carried_in_signed_roster(monkeypatch,tmp_path):
    from features import daybreak_credential_sync as sync
    monkeypatch.setattr(auto,"DATA_DIR",str(tmp_path));requests=stub_roster(monkeypatch,total=1)
    monkeypatch.setattr(sync,"authoritative_naukri_accounts",lambda:[{"id":"0","label":"Person Zero","username":"person0@example.test","enabled":False}])
    monkeypatch.setattr(sync,"status_view",lambda:{"workers":[],"accounts":[]})
    auto.sync_once()
    assert requests[0][1]["accounts"][0]["enabled"] is False
