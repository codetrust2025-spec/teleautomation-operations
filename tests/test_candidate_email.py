from features import candidate_store


def test_candidate_email_is_normalised_and_preserved_on_patch():
    candidate = candidate_store._normalise(
        {"name": "Test Candidate", "email": "  Test.Candidate@Example.COM  "}
    )

    assert candidate["email"] == "test.candidate@example.com"
    assert candidate_store._normalise({"phone": "9000000000"}, existing=candidate)[
        "email"
    ] == "test.candidate@example.com"


def test_candidate_email_is_an_allowed_profile_field():
    assert "email" in candidate_store._ALLOWED_FIELDS
