"""Referrer lifecycle: month-granular Active/Inactive with historical preservation.

The feature is a presentation/eligibility filter. Marking a referrer inactive
effective YYYY-MM must exclude them (and their active candidate workflow) from
that month onward in current/active views — Candidates, Earnings, Pending Works,
dropdowns, new-workflow pick-lists — while leaving every earlier month's
candidates, payments, expenses and earnings untouched.
"""

import json

import pytest

from features import candidate_store
from features import referrer_registry


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    """A registry backed by a temp file with three referrers, no dynamic names."""
    referrers_path = tmp_path / "referrers.json"
    accounts_path = tmp_path / "accounts.json"
    referrers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "referrers": [
                    {"id": "referrer-venugopal", "name": "Venugopal", "aliases": [], "is_active": True},
                    {"id": "referrer-thrilok", "name": "Thrilok", "aliases": [], "is_active": True},
                    {"id": "referrer-charan", "name": "Charan", "aliases": [], "is_active": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    accounts_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("REFERRER_REGISTRY_FILE", str(referrers_path))
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(accounts_path))
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setattr(referrer_registry, "_dynamic_reference_names", lambda: [])
    # Pin "now" so current-month behaviour is deterministic: pretend it is Oct 2026.
    monkeypatch.setattr(referrer_registry, "_current_month", lambda: "2026-10")
    return referrers_path


# ── Core predicate: month-granular, last-working-month authoritative ─────────

def test_active_referrer_is_active_for_every_month(isolated_registry):
    row = referrer_registry.resolve_referrer("Thrilok")
    assert referrer_registry.is_referrer_active_for_month(row, "2026-09") is True
    assert referrer_registry.is_referrer_active_for_month(row, "2026-10") is True
    assert referrer_registry.is_referrer_active_for_month(row, "2027-01") is True


def test_inactive_from_effective_month_onward_but_present_before(isolated_registry):
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="admin"
    )
    row = referrer_registry.resolve_referrer("Venugopal")
    # Last working month Sep 2026 → present through Sep, gone from Oct onward.
    assert referrer_registry.is_referrer_active_for_month(row, "2026-08") is True
    assert referrer_registry.is_referrer_active_for_month(row, "2026-09") is True
    assert referrer_registry.is_referrer_active_for_month(row, "2026-10") is False
    assert referrer_registry.is_referrer_active_for_month(row, "2026-11") is False
    # Current context (pinned to Oct 2026) → inactive.
    assert referrer_registry.is_referrer_active_for_month(row, None) is False


def test_marking_inactive_requires_effective_month(isolated_registry):
    with pytest.raises(ValueError):
        referrer_registry.set_referrer_lifecycle(
            "Venugopal", status="INACTIVE", effective_month="", actor="admin"
        )


def test_reactivation_restores_active_presence(isolated_registry):
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="admin"
    )
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="ACTIVE", actor="admin"
    )
    row = referrer_registry.resolve_referrer("Venugopal")
    assert referrer_registry.is_referrer_active_for_month(row, "2026-10") is True
    assert referrer_registry.is_referrer_active_for_month(row, "2027-05") is True


def test_lifecycle_changes_are_audited(isolated_registry):
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="alice", note="left in Sep"
    )
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="ACTIVE", actor="bob"
    )
    row = referrer_registry.resolve_referrer("Venugopal")
    history = row.get("lifecycle_history") or []
    assert len(history) == 2
    assert history[0]["to_status"] == "INACTIVE"
    assert history[0]["effective_month"] == "2026-10"
    assert history[0]["by"] == "alice"
    assert history[0]["note"] == "left in Sep"
    assert history[1]["to_status"] == "ACTIVE"
    assert history[1]["from_status"] == "INACTIVE"
    assert history[1]["by"] == "bob"


# ── Active / Inactive / All filter ───────────────────────────────────────────

def test_status_filter_returns_correct_sets(isolated_registry):
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="admin"
    )
    active = {r["name"] for r in referrer_registry.list_referrers(include_inactive=True, status_filter="ACTIVE")}
    inactive = {r["name"] for r in referrer_registry.list_referrers(include_inactive=True, status_filter="INACTIVE")}
    everyone = {r["name"] for r in referrer_registry.list_referrers(include_inactive=True, status_filter="ALL")}
    assert active == {"Thrilok", "Charan"}
    assert inactive == {"Venugopal"}
    assert everyone == {"Thrilok", "Charan", "Venugopal"}


def test_default_list_hides_currently_inactive_referrer(isolated_registry):
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="admin"
    )
    # Current month is pinned to Oct 2026 → Venugopal excluded by default.
    names_now = {r["name"] for r in referrer_registry.list_referrers()}
    assert names_now == {"Thrilok", "Charan"}
    # But a September-scoped active list still includes them.
    names_sep = {r["name"] for r in referrer_registry.list_referrers(as_of_month="2026-09")}
    assert "Venugopal" in names_sep


# ── Backward compatibility ───────────────────────────────────────────────────

def test_legacy_row_without_lifecycle_fields_is_active(isolated_registry):
    # The seed rows carry only is_active=True and no lifecycle fields.
    row = referrer_registry.resolve_referrer("Charan")
    assert row["lifecycle_status"] == "ACTIVE"
    assert referrer_registry.is_referrer_active_for_month(row, "2026-10") is True


def test_active_reference_names_tracks_month(isolated_registry):
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="admin"
    )
    oct_names = referrer_registry.active_reference_names("2026-10")
    sep_names = referrer_registry.active_reference_names("2026-09")
    assert "venugopal" not in oct_names
    assert "venugopal" in sep_names


# ── Candidate store integration: dropdowns, pending works, earnings ──────────

@pytest.fixture
def store_with_departed_referrer(monkeypatch, isolated_registry):
    """Candidate store with a Sep and an Oct candidate for a soon-departed referrer.

    Venugopal is marked inactive effective Oct 2026 (last worked Sep). Thrilok
    stays active. We assert Venugopal drops out of Oct+ active views but his Sep
    data is preserved.
    """
    candidates = [
        {
            "id": "c-ven-sep", "name": "Cand Ven Sep", "reference": "Venugopal",
            "stage": "completed", "payment": 10000, "technology": "Java",
            "date": "2026-09-15", "logged_date": "2026-09-15",
            "service_type": "profile_service",
        },
        {
            "id": "c-ven-oct", "name": "Cand Ven Oct", "reference": "Venugopal",
            "stage": "in_progress", "payment": 10000, "technology": "Java",
            "date": "2026-10-05", "logged_date": "2026-10-05",
            "service_type": "profile_service",
        },
        {
            "id": "c-thr-oct", "name": "Cand Thr Oct", "reference": "Thrilok",
            "stage": "in_progress", "payment": 8000, "technology": "Python",
            "date": "2026-10-06", "logged_date": "2026-10-06",
            "service_type": "profile_service",
        },
    ]
    monkeypatch.setattr(candidate_store, "_load", lambda *a, **k: {"candidates": candidates})
    referrer_registry.set_referrer_lifecycle(
        "Venugopal", status="INACTIVE", effective_month="2026-10", actor="admin"
    )
    candidate_store.reload_reference_aliases()
    return candidates


def test_dropdown_excludes_departed_referrer(store_with_departed_referrer):
    names = candidate_store.reference_dropdown_names()
    assert "Thrilok" in names
    assert "Venugopal" not in names


def test_oct_earnings_exclude_departed_referrer(store_with_departed_referrer):
    oct_stats = candidate_store.stats("2026-10")
    # Venugopal's Oct candidate (₹10k) must not count toward Oct revenue;
    # only Thrilok's Oct candidate (₹8k) remains.
    assert oct_stats["revenue_total"] == 8000


def test_sep_earnings_preserve_departed_referrer(store_with_departed_referrer):
    sep_stats = candidate_store.stats("2026-09")
    # Venugopal's September candidate (₹10k) is preserved unchanged in the Sep view.
    assert sep_stats["revenue_total"] == 10000


def test_pending_works_exclude_departed_referrer_in_current_month(store_with_departed_referrer):
    # Unscoped pending works (current context = Oct 2026) must drop Venugopal's
    # active Oct candidate but keep Thrilok's.
    pw = candidate_store.pending_works()
    references = {(w.get("reference") or "") for w in pw.get("works", [])}
    assert "Venugopal" not in references
