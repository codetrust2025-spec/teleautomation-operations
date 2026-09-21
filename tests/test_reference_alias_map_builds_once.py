"""The referrer alias map is built once, not once per candidate row.

Listing the referrers reads every candidate, and each candidate's computed
fields ask for the alias map again. Unguarded, each of those calls started its
own build: on 21 Sep 2026 the first Daily Ops load after a deploy made 124
nested builds and 27,026 row computations -- about four seconds with the only
API worker frozen, so every operator's request waited behind it.
"""

from __future__ import annotations

import pytest

from features import candidate_store as cs
from features import referrer_registry


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "_FILE", str(tmp_path / "candidates.json"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)
    monkeypatch.setattr(cs, "_ALIAS_CACHE", None)
    # The registry's own rows are where aliases come from.
    monkeypatch.setattr(referrer_registry, "_materialized_referrers",
                        lambda: [{"name": "Pavan Kalyan", "aliases": ["LUKKA PAVAN KALYAN"]}])
    for number in range(12):
        cs.create_candidate({
            "name": f"Synthetic Person {number}", "phone": f"90000002{number:02d}",
            "service_type": "profile_service", "stage": "in_progress",
            "reference": "LUKKA PAVAN KALYAN" if number % 2 else "Pavan Kalyan",
            "payment": 10000, "expected_payment": 20000,
        })
    monkeypatch.setattr(cs, "_ALIAS_CACHE", None)
    return tmp_path


def test_one_build_reads_the_candidates_once(store, monkeypatch):
    reads = []
    real = cs.list_candidates
    monkeypatch.setattr(cs, "list_candidates", lambda *a, **k: reads.append(1) or real(*a, **k))

    cs._reference_alias_map()

    assert len(reads) == 1, f"the candidate list was read {len(reads)} times for one alias map"


def test_the_finished_map_is_the_registrys_aliases(store):
    assert cs._reference_alias_map() == {"lukka pavan kalyan": "pavan kalyan"}
    assert cs._reference_key("LUKKA PAVAN KALYAN") == "pavan kalyan"
    assert cs._reference_key("Someone Else") == "someone else"


def test_a_call_made_during_the_build_gets_raw_keys_not_a_second_build(store, monkeypatch):
    seen = []
    real = referrer_registry.list_referrers

    def listing(*args, **kwargs):
        seen.append(cs._reference_alias_map())  # the re-entry, from inside the build
        return real(*args, **kwargs)

    monkeypatch.setattr(referrer_registry, "list_referrers", listing)

    assert cs._reference_alias_map() == {"lukka pavan kalyan": "pavan kalyan"}
    assert seen == [{}]


def test_reload_still_rebuilds(store):
    cs._reference_alias_map()
    cs.reload_reference_aliases()
    assert cs._ALIAS_CACHE is None
    assert cs._reference_alias_map() == {"lukka pavan kalyan": "pavan kalyan"}
