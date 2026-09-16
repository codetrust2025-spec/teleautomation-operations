from features import candidate_store


def test_profile_collapse_keeps_proof_owner_for_edit_actions():
    rows = [
        {
            "id": "active",
            "name": "Kaleshwar",
            "service_type": "profile_service",
            "updated_at": "2026-07-15T00:00:00+00:00",
            "payment": 10000,
            "payment_proofs": [],
        },
        {
            "id": "legacy",
            "name": "KALESHWAR",
            "service_type": "profile_service",
            "updated_at": "2026-05-26T00:00:00+00:00",
            "payment": 9000,
            "payment_proofs": [{"id": "proof-1", "url": "/candidates/legacy/attachments/payment_proof/proof-1"}],
        },
    ]

    merged = candidate_store._collapse_profile_candidates(rows)[0]

    assert merged["id"] == "active"
    assert merged["proof_count"] == 1
    assert merged["payment_proofs"][0]["candidate_id"] == "legacy"


def test_profile_rename_propagates_across_same_phone_legacy_rows(monkeypatch):
    rows = [
        {
            **candidate_store._normalise(
            {
                "name": "KALESHWAR",
                "phone": "9000000104",
                "technology": "React JS",
                "service_type": "profile_service",
            },
            ),
            "id": "active",
        },
        {
            **candidate_store._normalise(
            {
                "name": "KALESHWAR",
                "phone": "9000000104",
                "technology": "React JS",
                "service_type": "profile_service",
                "payment_proofs": [{"id": "proof-1"}],
            },
            ),
            "id": "legacy",
            "payment_proofs": [{"id": "proof-1"}],
        },
    ]
    data = {"candidates": rows}
    monkeypatch.setattr(candidate_store, "_load", lambda: data)
    monkeypatch.setattr(candidate_store, "_save", lambda updated: data.update(updated))

    candidate_store.update_candidate(
        "active",
        {
            "name": "Mehta Bhaskar",
            "email": "mehta.bhaskar@example.com",
            "technology": "Java Full Stack",
        },
    )

    assert {row["name"] for row in data["candidates"]} == {"Mehta Bhaskar"}
    assert {row["email"] for row in data["candidates"]} == {"mehta.bhaskar@example.com"}
    assert {row["technology"] for row in data["candidates"]} == {"Java Full Stack"}
    assert data["candidates"][1]["payment_proofs"][0]["id"] == "proof-1"


def test_profile_dropped_stage_propagates_across_legacy_rows(monkeypatch):
    rows = [
        {**candidate_store._normalise({"name": "Ravali", "phone": "9876543210"}), "id": "active"},
        {**candidate_store._normalise({"name": "Ravali", "phone": "9876543210"}), "id": "legacy"},
    ]
    data = {"candidates": rows}
    monkeypatch.setattr(candidate_store, "_load", lambda: data)
    monkeypatch.setattr(candidate_store, "_save", lambda updated: data.update(updated))

    candidate_store.update_candidate("active", {"stage": "dropped"})

    assert {row["stage"] for row in data["candidates"]} == {"dropped"}


def test_bootstrap_pending_works_ignores_old_active_clone_of_dropped_profile(monkeypatch):
    rows = [
        {
            **candidate_store._normalise({"name": "Ravali", "stage": "dropped", "phone": "9876543210"}),
            "id": "current",
            "updated_at": "2026-07-22T10:00:00+00:00",
        },
        {
            **candidate_store._normalise({"name": "Ravali", "stage": "in_progress", "phone": "9876543210"}),
            "id": "legacy",
            "updated_at": "2026-05-26T10:00:00+00:00",
        },
    ]
    monkeypatch.setattr(candidate_store, "_load", lambda: {"candidates": rows})

    payload = candidate_store.bootstrap_data(month="all")

    assert payload["stats"]["pending_works"] == []
    assert payload["stats"]["pending_works_candidates"] == 0
