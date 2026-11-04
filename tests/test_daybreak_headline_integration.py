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


def test_daybreak_headline_requires_dedicated_bearer_token(monkeypatch):
    c = client(monkeypatch)
    assert c.post("/api/integrations/daybreak/headline", json=payload()).status_code == 401
    assert c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer wrong"}, json=payload()).status_code == 401


def test_daybreak_headline_uses_shared_gateway_and_returns_selected_node(monkeypatch):
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content="React.js and JavaScript developer with 6 years",
            node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=47,
        )

    c = client(monkeypatch)
    monkeypatch.setattr("core.ai_gateway.chat", fake_chat)
    response = c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "headline": "React.js and JavaScript developer with 6 years",
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
    prompt = calls[0]["messages"][0]["content"]
    assert prompt == integration.REWRITE_PROMPT + "\n\nJava Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"
    assert integration.REWRITE_PROMPT == ("Rewrite the current Naukri headline into a genuinely different, natural, professional sentence within 250 characters. Reorganize sentence structure and skill order. Preserve all existing skills, job titles, experience and factual details. Do not invent, exaggerate or omit information. Avoid unnecessary filler. Return only the rewritten headline.")
    assert calls[0]["model"] == "qwen2.5:7b" and calls[0]["workload"] == "daybreak_headline"


def test_content_validation_removed_rewrites_are_not_blocked_by_word_order_or_terms(monkeypatch):
    for content in (
        "Java Developer with 4 Years 3 Months building REST APIs on MySQL and Spring Boot",
        "Java Developer with 3 Years 4 Months building REST APIs on Kafka, MySQL and Spring Boot",
        "Too short",
    ):
        response, calls = _rewrite_call(monkeypatch, content)
        assert response.status_code == 200 and response.json()["headline"] == content
        assert response.json()["rewrite_accepted"] is True
        assert response.json()["rewrite_attempts"] == 1 and len(calls) == 1


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


def test_unchanged_first_draft_gets_a_single_corrective_retry(monkeypatch):
    original = "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"
    response, calls = _rewrite_call(monkeypatch, original)
    assert response.status_code == 200
    assert response.json()["rewrite_attempts"] == 2 and len(calls) == 2
    # Without the deleted editorial gate, a changed separator/order response is returned.
    assert response.json()["headline"] == "Java Developer, 4 Years 3 Months, Spring Boot, MySQL, Kafka, REST APIs"
    assert response.json()["rewrite_accepted"] is True


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
    assert len(calls) == response.json()["rewrite_attempts"] == 2


def test_schema_rejection_on_correction_returns_unchanged_without_weakening_validation(monkeypatch):
    original = "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs"
    monkeypatch.setattr("core.ai_gateway.chat", lambda **_: SimpleNamespace(content=original,node_id="rtx4060",node_label="RTX 4060",model="qwen2.5:7b",duration_ms=1))
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: (_ for _ in ()).throw(AIGatewayError("private details",code="OLLAMA_SCHEMA_VALIDATION_FAILED")))
    response = client(monkeypatch).post("/api/integrations/daybreak/headline",headers={"Authorization":"Bearer "+"d"*48},json={**payload(),"previous_headline":original,"rewrite":True})
    assert response.status_code == 200
    assert response.json()["headline"] == original
    assert response.json()["rewrite_accepted"] is False
    assert response.json()["rewrite_reason"] == "invalid_rewrite_plan"


def test_removed_content_filter_does_not_reject_terms_or_proficiency_claims(monkeypatch):
    for term in ("AWS", "Proficient", "specializing"):
        candidate = f"Java Developer with 4 Years 3 Months in REST APIs on Kafka, MySQL and Spring Boot {term}"
        request = integration.DaybreakHeadlineRequest.model_validate({
            **payload(),
            "previous_headline": "Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs",
            "rewrite": True,
        })
        assert integration._rewrite_candidate(request, candidate) == (candidate, "accepted")
        response, calls = _rewrite_call(monkeypatch, candidate)
        assert response.json()["rewrite_accepted"] is True
        assert response.json()["headline"] == candidate
        assert response.json()["rewrite_attempts"] == 1 and len(calls) == 1


def test_rewrite_allows_additional_terms_without_approval():
    request = payload() | {'previous_headline': 'Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs', 'rewrite': True}
    candidate = 'Java Developer with 4 Years 3 Months in REST APIs on Kafka, MySQL and Spring Boot with AWS'
    assert integration._rewrite_candidate(integration.DaybreakHeadlineRequest.model_validate(request), candidate) == (candidate, 'accepted')


def test_only_naukri_limits_reject_a_rewrite():
    request = integration.DaybreakHeadlineRequest.model_validate({**payload(), 'previous_headline': 'Java Developer | 4 Years'})
    assert integration._rewrite_candidate(request, '   ') == (None, 'empty')
    assert integration._rewrite_candidate(request, 'x' * 251) == (None, 'too_long')
    assert integration._rewrite_candidate(request, 'JAVA DEVELOPER | 4 YEARS') == (None, 'unchanged')
    assert integration._rewrite_candidate(request, 'x' * 250) == ('x' * 250, 'accepted')
