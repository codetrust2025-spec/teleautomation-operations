import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import daybreak_integration as integration
from core.ai_gateway import AIGatewayError


def client(monkeypatch):
    monkeypatch.setenv("DAYBREAK_HEADLINE_TOKEN", "d" * 48)
    app = FastAPI()
    app.include_router(integration.router)
    return TestClient(app)


def payload():
    return {
        "candidate_ref": "candidate-a",
        # Daybreak rewrites the account's current Naukri headline.
        "previous_headline": "React developer | 6 years",
        "facts": {
            "target_role": "React developer",
            "experience": "6 years",
            "skills": ["JavaScript", "React.js"],
            "profile_facts": "",
        },
        "max_length": 250,
    }


#: A candidate whose approved facts actually match their current headline, so every verified fact is
#: genuinely required and the strict rules can be exercised. payload() deliberately does not match.
JAVA_HEADLINE = "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"


def java_body(**overrides):
    return {
        "candidate_ref": "candidate-java",
        "previous_headline": JAVA_HEADLINE,
        "facts": {
            "target_role": "Java Developer",
            "experience": "4 Years 3 Months",
            "skills": ["Spring Boot", "MySQL", "Kafka", "REST APIs", "Docker"],
            "profile_facts": "",
        },
        "max_length": 250,
        "rewrite": True,
        **overrides,
    }


def java_request(**overrides):
    return integration.DaybreakHeadlineRequest.model_validate(java_body(**overrides))


def test_daybreak_headline_requires_dedicated_bearer_token(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/api/integrations/daybreak/headline", json=payload()).status_code == 401
    assert c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer wrong"}, json=payload()).status_code == 401


def test_daybreak_headline_uses_shared_gateway_and_returns_selected_node(monkeypatch):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            # Keeps the exact job title and experience, and adds one approved skill the headline
            # did not show. Dropping "React developer" for "JavaScript developer" would be refused.
            content="React developer, 6 years, JavaScript",
            node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=47,
        )

    c = client(monkeypatch)
    monkeypatch.setattr("core.ai_gateway.chat", fake_chat)
    response = c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "headline": "React developer, 6 years, JavaScript",
        "rewrite_accepted": True, "rewrite_reason": "", "rewrite_attempts": 1,
        "node_id": "rtx4060", "node_label": "RTX 4060", "model": "qwen2.5:7b", "duration_ms": 47,
    }
    assert calls[0]["workload"] == "daybreak_headline"
    assert "candidate-a" not in calls[0]["messages"][0]["content"]


def test_daybreak_headline_fails_closed_for_node_or_model_failures(monkeypatch):
    c = client(monkeypatch)
    headers = {"Authorization": "Bearer " + "d" * 48}
    monkeypatch.setattr("core.ai_gateway.chat", lambda **_: (_ for _ in ()).throw(AIGatewayError("secret node detail", code="OLLAMA_CONNECTION_FAILED")))
    response = c.post("/api/integrations/daybreak/headline", headers=headers, json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "AI generation unavailable"}


def test_without_a_current_headline_nothing_is_generated(monkeypatch):
    monkeypatch.setattr("core.ai_gateway.chat", lambda **_: (_ for _ in ()).throw(AssertionError("no model call")))
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: (_ for _ in ()).throw(AssertionError("no model call")))
    response = client(monkeypatch).post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=payload() | {"previous_headline": ""})
    assert response.status_code == 400
    assert response.json() == {"detail": "No current headline to rewrite"}


def test_the_removed_skill_selection_flow_is_gone():
    for name in ("_prompt", "_brief", "_selection_schema", "_render", "_daybreak_coverage_key", "_skill_present", "_rewrite"):
        assert not hasattr(integration, name), name


def test_daybreak_headline_rejects_extra_or_oversized_candidate_input(monkeypatch):
    c = client(monkeypatch)
    headers = {"Authorization": "Bearer " + "d" * 48}
    assert c.post("/api/integrations/daybreak/headline", headers=headers, json=payload() | {"unexpected": "no"}).status_code == 422
    bad = payload()
    bad["facts"]["skills"] = ["x" * 61]
    assert c.post("/api/integrations/daybreak/headline", headers=headers, json=bad).status_code == 422


def test_daybreak_machine_route_bypasses_dashboard_cookie_gate_only():
    """The route authenticates with its own bearer token, never a dashboard cookie."""
    from core import dashboard_auth_vps as dashboard_auth

    assert dashboard_auth.is_public_path("/api/integrations/daybreak/headline") is True
    assert dashboard_auth.is_public_path("/api/integrations/daybreak/other") is False


def _rewrite_call(monkeypatch, content):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=content, node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=31)

    monkeypatch.setattr("core.ai_gateway.chat", fake_chat)
    def fake_plan(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"groups":{"0":0,"1":0,"2":0,"3":0,"4":0,"5":0}}', node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=31)
    monkeypatch.setattr("core.ai_gateway.chat_structured", fake_plan)
    body = {**payload(), "previous_headline": "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs", "rewrite": True}
    response = client(monkeypatch).post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=body)
    return response, calls


def test_rewrite_sends_the_exact_tested_prompt_and_the_current_headline_to_qwen(monkeypatch):
    rewritten = "Java Developer with 4 Years 3 Months in REST APIs on Kafka, MySQL and Spring Boot"
    response, calls = _rewrite_call(monkeypatch, 'Headline: "' + rewritten + '"\n')
    assert response.status_code == 200
    assert response.json()["headline"] == rewritten and response.json()["node_id"] == "rtx4060"
    assert response.json()["rewrite_accepted"] is True
    assert response.json()["rewrite_attempts"] == 1
    assert len(calls) == 1
    # P10 "Free safe restructure", verbatim, with this candidate's title, experience and headline.
    assert calls[0]["messages"][0]["content"] == (
        "Rewrite the current Naukri resume headline.\n"
        "TASK: Rewrite more creatively with a different sentence structure and skill order. "
        "A small change is fine, but preserve every verified fact exactly.\n\n"
        "STRICT RULES:\n"
        "- Preserve the exact job title 'React developer'.\n"
        "- Preserve '6 years' without shortening or increasing experience.\n"
        "- Preserve ALL listed technical skills, without omissions, additions or altered spellings.\n"
        "- No invented results, roles, credentials, achievements, or exaggerated claims.\n"
        "- Maximum 250 characters in the COMPLETE output.\n"
        "- Return ONLY the headline, without explanations, labels or numbering.\n\n"
        "ORIGINAL HEADLINE:\n" + JAVA_HEADLINE
    )
    # The settings the prompt was measured with. num_ctx comes from the gateway's own 4096 floor.
    assert integration.REWRITE_MODEL == "qwen2.5:7b"
    assert (integration.REWRITE_TEMPERATURE, integration.REWRITE_NUM_PREDICT) == (0.7, 160)
    assert calls[0]["model"] == "qwen2.5:7b" and calls[0]["workload"] == "daybreak_headline"
    assert calls[0]["temperature"] == 0.7 and calls[0]["num_predict"] == 160 and calls[0]["think"] is False
    from core.ai_gateway import MIN_NUM_CTX, resolved_num_ctx
    assert MIN_NUM_CTX == 4096 and resolved_num_ctx() >= 4096


def test_a_rewrite_that_drops_or_alters_a_verified_fact_is_refused(monkeypatch):
    """Reverses #442: dropping a skill, the title or the exact experience is no longer accepted."""
    request = java_request()
    for draft, dropped in (
        # Kafka is an approved skill the current headline shows.
        ("Java Developer with 4 Years 3 Months building REST APIs on MySQL and Spring Boot", ["Kafka"]),
        # The experience is reversed, which is a different claim about the candidate.
        ("Java Developer with 3 Years 4 Months on Spring Boot, MySQL, Kafka and REST APIs", ["4 Years 3 Months"]),
        # The exact job title is gone.
        ("Spring Boot, MySQL, Kafka and REST APIs over 4 Years 3 Months", ["Java Developer"]),
    ):
        assert integration._dropped_facts(request, draft) == dropped, draft
        assert integration._rewrite_candidate(request, draft) == (None, "dropped_facts"), draft
    # A skill the current headline never showed is not demanded of the rewrite: Docker is approved but absent.
    assert "Docker" not in integration._required_facts(request)


def test_rewrite_corrects_a_too_long_first_draft_without_losing_skills(monkeypatch):
    original = ("Senior Full Stack Developer | 5 Years 5 Months | MERN Stack Development, React.js, "
                "Node.js, JavaScript, Redux, RESTful APIs | Express.js | MongoDB | TypeScript | "
                "API Development | API Integration | Microservices | Redis | JWT Authentication | GIT")
    valid = ("5 Years 5 Months | Senior Full Stack Developer | GIT, JWT Authentication, Redis, "
             "Microservices, API Integration, API Development | TypeScript, MongoDB, Express.js | "
             "RESTful APIs, Redux, JavaScript, Node.js, React.js | MERN Stack Development")
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        content = "Proficient in " * 30 + original if len(calls) == 1 else valid
        return SimpleNamespace(content=content, node_id="rtx4060", node_label="RTX 4060",
                               model="qwen2.5:7b", duration_ms=25)

    monkeypatch.setattr("core.ai_gateway.chat", fake_chat)
    parts = integration._rewrite_fragments(original)
    groups = {str(i): min(4, (len(parts)-1-i)*5//len(parts)) for i in range(len(parts))}
    order = [0,1] + sorted(range(2,len(parts)), key=lambda i: groups[str(i)])
    valid = ", ".join(parts[i] for i in order)
    def fake_plan(**kwargs):
        import json
        calls.append(kwargs)
        return SimpleNamespace(content=json.dumps({"groups": groups}), node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=25)
    monkeypatch.setattr("core.ai_gateway.chat_structured", fake_plan)
    body = {**payload(), "previous_headline": original, "rewrite": True}
    response = client(monkeypatch).post(
        "/api/integrations/daybreak/headline",
        headers={"Authorization": "Bearer " + "d" * 48}, json=body)
    assert response.status_code == 200
    assert response.json()["headline"] == valid
    assert response.json()["rewrite_accepted"] is True
    assert response.json()["rewrite_attempts"] == 2
    assert response.json()["duration_ms"] == 50
    assert len(calls) == 2 and "too_long" in calls[1]["messages"][0]["content"]
    assert len(valid) <= 250


def test_an_unchanged_draft_is_told_why_and_then_falls_back_to_reordering_its_own_phrases(monkeypatch):
    """Three attempts: P10, P10 told what was refused, then a verbatim reorder that cannot lose a fact."""
    response, calls = _rewrite_call(monkeypatch, JAVA_HEADLINE)
    assert response.status_code == 200
    assert response.json()["rewrite_attempts"] == 3 and len(calls) == 3
    assert response.json()["headline"] == "Java Developer, 4 Years 3 Months, Spring Boot, MySQL, Kafka, REST APIs"
    assert response.json()["rewrite_accepted"] is True
    # The second attempt is still P10, with a refusal block naming what to fix -- not a different prompt.
    second = calls[1]["messages"][0]["content"]
    assert second.startswith("Rewrite the current Naukri resume headline.\nTASK: " + integration.REWRITE_TASK)
    refusal = json.loads(second.split("\nREFUSAL=", 1)[1])
    assert refusal["reason"] == "unchanged" and refusal["maximum_characters"] == 250


def test_old_worker_flags_still_get_a_rewrite_not_the_removed_selection(monkeypatch):
    # Workers that still send editorial_proposal (or no rewrite flag) are not refused and are rewritten.
    rewritten = "Java Developer with 4 Years 3 Months in REST APIs on Kafka, MySQL and Spring Boot"
    monkeypatch.setattr("core.ai_gateway.chat", lambda **_: SimpleNamespace(content=rewritten, node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=5))
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: (_ for _ in ()).throw(AssertionError("no correction needed")))
    original = "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"
    for extra in ({}, {"editorial_proposal": True}, {"rewrite": False}):
        response = client(monkeypatch).post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json={**payload(), "previous_headline": original, **extra})
        assert response.status_code == 200, extra
        assert response.json()["headline"] == rewritten and response.json()["rewrite_accepted"] is True


def test_corrective_plan_is_facts_only_and_validates_every_source_phrase():
    import json
    import pytest
    request = integration.DaybreakHeadlineRequest(**{
        **payload(), "previous_headline": "QA Engineer | 5 Years | Hybrid Framework | Java | API Testing", "rewrite": True,
    })
    prompt = integration._rewrite_plan_prompt(request, "QA Engineer with 5 Years and Hybrid Frameworks", "original_details_missing")
    source = json.loads(prompt.split("\nSOURCE=", 1)[1])
    assert "framework" in source["missing_words"]
    assert source["maximum_characters"] == 250
    assert source["phrases"][2] == {"id": 2, "text": "Hybrid Framework"}
    schema = integration._rewrite_plan_schema(request)
    assert schema["properties"]["groups"]["required"] == ["0","1","2","3","4"]
    text = integration._render_rewrite_plan(request, '{"groups":{"0":3,"1":3,"2":2,"3":0,"4":1}}')
    assert text == "QA Engineer, 5 Years, Java, API Testing, Hybrid Framework"
    assert integration._rewrite_candidate(request, text) == (text, "accepted")
    for plan in [{"groups": {"0":0}}, {"groups": {"0":0,"1":1,"2":2,"3":3,"4":9}},
                 {"groups": {"0":True,"1":1,"2":2,"3":3,"4":4}},
                 {"groups": {"0":0,"1":1,"2":2,"3":3,"4":4,"5":0}},
                 {"groups": {"0":0,"1":1,"2":2,"3":3,"4":4}, "new_skill":"AWS"}, {"groups": None}]:
        with pytest.raises(ValueError):
            integration._render_rewrite_plan(request, json.dumps(plan))


def test_invalid_corrective_plan_is_reported_unchanged_without_extra_model_attempts(monkeypatch):
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content="Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs", node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=1)
    monkeypatch.setattr("core.ai_gateway.chat", fake)
    monkeypatch.setattr("core.ai_gateway.chat_structured", fake)
    original = "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"
    response = client(monkeypatch).post("/api/integrations/daybreak/headline",headers={"Authorization":"Bearer "+"d"*48},json={**payload(),"previous_headline":original,"rewrite":True})
    assert response.json()["headline"] == original
    assert response.json()["rewrite_accepted"] is False
    assert response.json()["rewrite_reason"] == "invalid_rewrite_plan"
    assert len(calls) == response.json()["rewrite_attempts"] == 3


def test_schema_rejection_on_correction_returns_unchanged_without_weakening_validation(monkeypatch):
    original = "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"
    monkeypatch.setattr("core.ai_gateway.chat", lambda **_: SimpleNamespace(content=original,node_id="rtx4060",node_label="RTX 4060",model="qwen2.5:7b",duration_ms=1))
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: (_ for _ in ()).throw(AIGatewayError("private details",code="OLLAMA_SCHEMA_VALIDATION_FAILED")))
    response = client(monkeypatch).post("/api/integrations/daybreak/headline",headers={"Authorization":"Bearer "+"d"*48},json={**payload(),"previous_headline":original,"rewrite":True})
    assert response.status_code == 200
    assert response.json()["headline"] == original
    assert response.json()["rewrite_accepted"] is False
    assert response.json()["rewrite_reason"] == "invalid_rewrite_plan"


def test_a_word_absent_from_the_approved_facts_is_refused(monkeypatch):
    """Reverses #442: an unapproved skill, a seniority word or a proficiency claim cannot be added."""
    request = java_request()
    for term in ("AWS", "Proficient", "Senior", "certified"):
        draft = f"Java Developer, 4 Years 3 Months, Spring Boot, MySQL, Kafka, REST APIs, {term}"
        assert integration._invented_terms(request, draft) == [term.lower()], draft
        assert integration._rewrite_candidate(request, draft) == (None, "invented_terms"), draft
    # Connectives are not claims, and an approved skill may be added even when the headline omits it.
    approved = "Java Developer with 4 Years 3 Months across Spring Boot, MySQL, Kafka, REST APIs and Docker"
    assert integration._invented_terms(request, approved) == []
    assert integration._rewrite_candidate(request, approved) == (approved, "accepted")
    # End to end, an unapproved word is refused twice and the verbatim reorder is what gets returned,
    # so the candidate still receives a changed headline that invented nothing.
    response, calls = _rewrite_call(monkeypatch, "Java Developer, 4 Years 3 Months, Spring Boot, MySQL, Kafka, REST APIs, AWS")
    assert response.json()["rewrite_attempts"] == 3 and len(calls) == 3
    assert response.json()["rewrite_accepted"] is True
    assert "AWS" not in response.json()["headline"]
    refused = json.loads(calls[1]["messages"][0]["content"].split("REFUSAL=", 1)[1])
    assert refused["words_not_in_the_approved_facts"] == ["aws"]


def test_every_character_counts_and_whitespace_is_never_folded_away():
    request = java_request(previous_headline="Java Developer | 4 Years")
    assert integration._rewrite_candidate(request, "   ") == (None, "empty")
    assert integration._rewrite_candidate(request, "Java Developer | 4 Years") == (None, "unchanged")
    # One letter of case and one comma each count as a change. casefold() used to hide the first.
    for draft in ("JAVA DEVELOPER | 4 YEARS", "Java Developer, 4 Years"):
        assert integration._rewrite_candidate(request, draft) == (draft, "accepted"), draft
    # A difference made only of whitespace is refused by name, not silently trimmed away: the companion
    # reads innerText().trim() and verifies with a whitespace-collapsing compare, so it is unverifiable.
    for draft in ("Java Developer |  4 Years", " Java Developer | 4 Years", "Java Developer\t| 4 Years"):
        assert integration._rewrite_candidate(request, draft)[1] in {"whitespace_only", "unchanged"}, draft
    assert integration._rewrite_candidate(request, "Java Developer |  4 Years") == (None, "whitespace_only")
    # Only the model's own envelope is unwrapped -- a label, surrounding quotes, a wrapped line --
    # and that happens before the comparison, so it can never hide a difference in the headline itself.
    assert integration._clean_rewrite('Headline: "Java Developer, 4 Years"\n') == "Java Developer, 4 Years"
    assert integration._rewrite_candidate(request, '  Java Developer | 4 Years  ') == (None, "unchanged")
    long_draft = "Java Developer, " + ", ".join(["Spring Boot"] * 21)
    assert len(long_draft) > 250 and integration._rewrite_candidate(request, long_draft) == (None, "too_long")


def test_the_two_services_judge_the_same_drafts_alike():
    """The agreement table.

    The Daybreak companion asserts the same rows against its own implementation
    (companion/tests/restructure-policy.test.mjs, "the companion and the gateway judge the same drafts
    alike"). Two services validate every headline -- the gateway before it returns one, the companion
    before it saves one -- and a disagreement would either waste a candidate's single daily save or
    quietly let through what the other refuses.
    """
    request = java_request()
    agreed = [
        ("Java Developer, 4 Years 3 Months, Spring Boot, MySQL, Kafka, REST APIs", "accepted"),
        ("Java Developer with 4 Years 3 Months across Spring Boot, MySQL, Kafka, REST APIs and Docker", "accepted"),
        ("Java Developer with 4 Years 3 Months building REST APIs on MySQL and Spring Boot", "dropped_facts"),
        ("Java Developer, 4 Years 3 Months, Spring Boot, MySQL, Kafka, REST APIs, AWS", "invented_terms"),
        (JAVA_HEADLINE, "unchanged"),
        ("Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka |  REST APIs", "whitespace_only"),
    ]
    for draft, expected in agreed:
        candidate, reason = integration._rewrite_candidate(request, draft)
        assert reason == expected, draft
        assert (candidate == draft) is (expected == "accepted"), draft
    assert integration._dropped_facts(request, agreed[2][0]) == ["Kafka"]
    assert integration._invented_terms(request, agreed[3][0]) == ["aws"]


def test_the_job_title_is_the_approved_role_or_a_role_shaped_summary_opening():
    def title(**facts):
        return integration._candidate_title(integration.ApprovedFacts.model_validate(
            {"target_role": "", "experience": "", "skills": [], "profile_facts": "", **facts}))

    assert title(target_role="Salesforce Developer") == "Salesforce Developer"
    assert title(profile_facts="Salesforce Developer | 5 Years 2 Months | Apex | LWC") == "Salesforce Developer"
    assert title(experience="4 Years 5 Months",
                 profile_facts="Automation Test Engineer with 4 Years 5 Months | Selenium WebDriver") == "Automation Test Engineer"
    assert title(experience="6 Years", profile_facts="Web Developer with 6 Years Experience in Javascript") == "Web Developer"
    # A summary opening is not a job title. Claiming one would make every rewrite preserve a sentence.
    assert title(experience="6 Years", profile_facts="6 Years of Experience in Web Technologies | React") == ""
    assert title(profile_facts="MERN Stack Development | React.js | Node.js") == ""
    assert title() == ""
    assert "job title" not in integration._rewrite_prompt(integration.DaybreakHeadlineRequest.model_validate(
        java_body(facts={"target_role": "", "experience": "6 Years", "skills": ["React"],
                         "profile_facts": "6 Years of Experience in Web Technologies"})))
