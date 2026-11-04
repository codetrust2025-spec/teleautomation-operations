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


def test_sync_once_uses_existing_sealed_delivery_only_and_reports_unmatched(monkeypatch, tmp_path):
    from features import daybreak_credential_sync as sync

    view = {"workers": [worker()], "accounts": [
        *[account(i) for i in range(24)],
        *[account(i, codes=["missing_in_daybreak"], synced=False) for i in range(24, 28)],
    ]}
    calls = []
    monkeypatch.setattr(auto, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync, "status_view", lambda: view)
    def deliver(ids, *, actor):
        calls.append((ids, actor))
        return {"delivered": ids, "refused": []}
    monkeypatch.setattr(sync, "deliver", deliver)
    result = auto.sync_once()
    assert result == {"status": "delivered", "delivered": 10, "refused": 0,
                      "total": 28, "unmatched": 4}
    assert calls == [([str(i) for i in range(10)], "automatic-data-room-sync")]


def test_sync_once_does_not_duplicate_delivered_or_inflight_accounts(monkeypatch, tmp_path):
    from features import daybreak_credential_sync as sync

    monkeypatch.setattr(auto, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync, "status_view", lambda: {
        "workers": [worker()],
        "accounts": [account(1, delivery={"waiting_for": ["RTX 4060"]}),
                     account(2, delivery={"receipts": ["RTX 4060", "Jagadeesh"]})],
    })
    monkeypatch.setattr(sync, "deliver", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("No duplicate credential deliveries allowed")))
    assert auto.sync_once()["status"] == "up_to_date"


def test_default_is_opt_out_until_ops_release_explicitly_enables(monkeypatch):
    monkeypatch.delenv("DAYBREAK_CREDENTIAL_AUTO_SYNC_ENABLED", raising=False)
    assert not auto.enabled()
    monkeypatch.setenv("DAYBREAK_CREDENTIAL_AUTO_SYNC_ENABLED", "1")
    assert auto.enabled()
