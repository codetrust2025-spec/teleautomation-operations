from features import candidate_store


def test_duplicate_profile_rows_share_all_mailboxes(monkeypatch):
    monkeypatch.setattr(
        candidate_store,
        "_load",
        lambda *args, **kwargs: {
            "candidates": [
                {
                    "id": "visible-profile",
                    "name": "Ram Charan M S",
                    "phone": "",
                    "service_type": "profile_service",
                },
                {
                    "id": "legacy-profile",
                    "name": "Reddy Charan M S",
                    "phone": "9000000102",
                    "service_type": "profile_service",
                },
            ]
        },
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

    assert candidate_store.candidate_identity_ids("visible-profile") == [
        "legacy-profile",
        "visible-profile",
    ]
    assert candidate_store.candidate_identity_ids("legacy-profile") == [
        "legacy-profile",
        "visible-profile",
    ]


def test_round_wise_row_with_same_name_is_not_a_mailbox_identity(monkeypatch):
    monkeypatch.setattr(
        candidate_store,
        "_load",
        lambda *args, **kwargs: {
            "candidates": [
                {"id": "profile", "name": "Same Name", "service_type": "profile_service"},
                {"id": "round", "name": "Same Name", "service_type": "round_wise"},
            ]
        },
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

    assert candidate_store.candidate_identity_ids("profile") == ["profile"]


def test_bulk_canonical_mailbox_ids_load_candidates_once(monkeypatch):
    loaded = {
        "candidates": [
            {
                "id": "visible-profile",
                "name": "Ram Charan M S",
                "phone": "",
                "service_type": "profile_service",
            },
            {
                "id": "legacy-profile",
                "name": "Reddy Charan M S",
                "phone": "9000000102",
                "service_type": "profile_service",
            },
            {
                "id": "independent",
                "name": "Independent Candidate",
                "phone": "9000000000",
                "service_type": "profile_service",
            },
        ]
    }
    load_calls = []
    monkeypatch.setattr(
        candidate_store,
        "_load",
        lambda *args, **kwargs: load_calls.append(True) or loaded,
    )
    monkeypatch.setattr(
        candidate_store,
        "list_candidates",
        lambda **_kwargs: [loaded["candidates"][0], loaded["candidates"][2]],
    )
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: False)

    resolved = candidate_store.canonical_candidate_identity_ids(
        ["legacy-profile", "visible-profile", "independent"]
    )

    assert resolved == {
        "legacy-profile": "visible-profile",
        "visible-profile": "visible-profile",
        "independent": "independent",
    }
    assert len(load_calls) == 1
