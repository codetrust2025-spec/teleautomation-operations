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
        # The live append-only contract requires a verified existing baseline.
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
            content='{"skills":["JavaScript","React.js"],"use_existing":false}',
            node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=47,
        )

    c = client(monkeypatch)
    monkeypatch.setattr("core.ai_gateway.chat_structured", fake_chat)
    response = c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "headline": "React developer | 6 years | JavaScript | React.js",
        "node_id": "rtx4060", "node_label": "RTX 4060", "model": "qwen2.5:7b", "duration_ms": 47,
    }
    assert calls[0]["workload"] == "daybreak_headline"
    assert calls[0]["temperature"] == 0
    assert "candidate-a" not in calls[0]["messages"][1]["content"]


def test_daybreak_headline_fails_closed_for_node_or_model_failures(monkeypatch):
    c = client(monkeypatch)
    headers = {"Authorization": "Bearer " + "d" * 48}
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: (_ for _ in ()).throw(AIGatewayError("secret node detail", code="OLLAMA_CONNECTION_FAILED")))
    response = c.post("/api/integrations/daybreak/headline", headers=headers, json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "AI generation unavailable"}
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: SimpleNamespace(content='{"headline":"x"}', node_id="", node_label="", model="", duration_ms=0))
    response = c.post("/api/integrations/daybreak/headline", headers=headers, json=payload())
    assert response.status_code == 502
    assert response.json() == {"detail": "AI generation returned an invalid result"}


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

def test_daybreak_prompt_requires_grounded_terms():
    text = integration._prompt(integration.DaybreakHeadlineRequest.model_validate(payload()))
    assert "Every non-neutral word must appear verbatim in the approved facts" in text
    assert "experienced, strong, expertise" in text

def test_existing_headline_is_comparison_only_not_a_long_string_for_the_model_to_extend(monkeypatch):
    import json
    calls = []
    def fake_chat(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"skills":["JavaScript","React.js"],"use_existing":false}', node_id="node", node_label="node", model="model", duration_ms=1)
    monkeypatch.setattr("core.ai_gateway.chat_structured", fake_chat)
    c = client(monkeypatch)
    previous = "React developer - JavaScript, React.js"
    response = c.post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=payload() | {"previous_headline": previous})
    assert response.status_code == 200
    prompt = calls[0]["messages"][1]["content"]
    assert json.dumps(previous) not in prompt
    assert 'EXISTING_APPROVED_SKILL_COVERAGE=["JavaScript", "React.js"]' in prompt
    assert "improve its clarity and wording" in prompt.lower()
    assert "does not authorize claims absent from APPROVED_FACTS" in prompt
    assert "return the existing headline unchanged" in prompt
    assert calls[0]["workload"] == "daybreak_headline"


def test_rewriting_prioritizes_explicit_approved_experience_without_inflation():
    text = integration._prompt(integration.DaybreakHeadlineRequest.model_validate(payload() | {"previous_headline": "React developer - JavaScript, React.js"}))
    assert "include that exact experience" in text
    assert "Never round it or add a plus sign" in text
    assert '"experience":"6 years"' in text


def test_editorial_review_lists_only_approved_missing_skills_without_spelling_churn():
    request = payload() | {"previous_headline": "React.js developer using JavaScript"}
    request["facts"]["skills"] = ["React.js", "JavaScript", "Docker"]
    prompt = integration._prompt(integration.DaybreakHeadlineRequest.model_validate(request))
    review = prompt.split("EDITORIAL_REVIEW:", 1)[1]
    assert '["Docker"]' in review
    assert '"React.js"' not in review
    assert '"JavaScript"' not in review
    assert "genuinely strengthen this existing role" in review
    assert "return existing headline unchanged" in review


def test_long_approved_summary_does_not_seed_a_truncated_headline():
    request = payload()
    request['facts']['target_role'] = ''
    request['facts']['profile_facts'] = 'React developer | ' + 'JavaScript and React.js ' * 10
    request['previous_headline'] = request['facts']['profile_facts'][:250]
    prompt = integration._prompt(integration.DaybreakHeadlineRequest.model_validate(request))
    assert request['previous_headline'] not in prompt
    assert '"role_context":"React developer"' in prompt
    assert '"experience":"6 years"' in prompt
    assert 'Do not write or extend a headline string' in prompt


def test_explicit_no_improvement_returns_previous_headline_without_inventing_a_change(monkeypatch):
    c = client(monkeypatch)
    monkeypatch.setattr('core.ai_gateway.chat_structured', lambda **_: SimpleNamespace(content='{"skills":[],"use_existing":true}', node_id='rtx4060', node_label='RTX 4060', model='qwen2.5:7b', duration_ms=1))
    previous = 'React developer with 6 years using JavaScript and React.js'
    response = c.post('/api/integrations/daybreak/headline', headers={'Authorization':'Bearer '+'d'*48}, json=payload() | {'previous_headline':previous})
    assert response.status_code == 200
    assert response.json()['headline'] == previous
    assert c.post('/api/integrations/daybreak/headline', headers={'Authorization':'Bearer '+'d'*48}, json=payload()).status_code == 502


def test_actual_route_rejects_invented_cross_candidate_truncated_and_malformed_selections(monkeypatch):
    import json
    c = client(monkeypatch)
    for selection in [
        {'skills':['AWS'],'use_existing':False},
        {'skills':['SQL'],'use_existing':False},
        {'skills':['React.j'],'use_existing':False},
        {'skills':['JavaScript云'],'use_existing':False},
        {'skills':['JavaScript'],'use_existing':'false'},
        {'skills':['JavaScript'],'use_existing':False,'experience':'99 years'},
        {'headline':'React developer with 99 years using AWS'},
    ]:
        monkeypatch.setattr('core.ai_gateway.chat_structured', lambda **_: SimpleNamespace(content=json.dumps(selection),node_id='rtx4060',node_label='RTX 4060',model='qwen2.5:7b',duration_ms=1))
        response=c.post('/api/integrations/daybreak/headline',headers={'Authorization':'Bearer '+'d'*48},json=payload())
        assert response.status_code==502,selection


def test_actual_route_never_adds_a_second_experience_statement_and_keeps_scoped_schema(monkeypatch):
    # Append-only rendering cannot replace "3.5 YOE", and a published headline cannot be cleaned
    # up automatically, so appending "3 Years 5 Months" would state experience twice for good.
    # A precise-experience rewrite needs an owner-approved editorial save.
    calls=[]
    def selected(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"skills":["JavaScript","React.js"],"use_existing":false}',node_id='rtx4060',node_label='RTX 4060',model='qwen2.5:7b',duration_ms=1)
    monkeypatch.setattr('core.ai_gateway.chat_structured',selected)
    request=payload() | {'previous_headline':'React developer | 3.5 YOE | JavaScript, React.js'}
    request['facts']['experience']='3 Years 5 Months'
    response=client(monkeypatch).post('/api/integrations/daybreak/headline',headers={'Authorization':'Bearer '+'d'*48},json=request)
    assert response.status_code==200
    # Existing coverage is never rewritten or trimmed, and no second experience statement is added.
    assert response.json()['headline']==request['previous_headline']
    assert calls[0]['schema']['properties']['skills']['items']['enum']==['JavaScript','React.js']
    assert calls[0]['schema']['additionalProperties'] is False


def test_formatting_only_selection_returns_exact_existing_headline(monkeypatch):
    monkeypatch.setattr('core.ai_gateway.chat_structured',lambda **_:SimpleNamespace(content='{"skills":["React.js","JavaScript"],"use_existing":false}',node_id='rtx4060',node_label='RTX 4060',model='qwen2.5:7b',duration_ms=1))
    previous='React developer - 6 YOE - JavaScript - React.js'
    response=client(monkeypatch).post('/api/integrations/daybreak/headline',headers={'Authorization':'Bearer '+'d'*48},json=payload() | {'previous_headline':previous})
    assert response.status_code==200
    assert response.json()['headline']==previous


def test_render_drops_whole_skills_instead_of_clipping_words(monkeypatch):
    import json
    skills=['JavaScript','React.js','Application Performance Optimization','Responsive Interface Development']
    monkeypatch.setattr('core.ai_gateway.chat_structured',lambda **_:SimpleNamespace(content=json.dumps({'skills':skills[:3],'use_existing':False}),node_id='rtx4060',node_label='RTX 4060',model='qwen2.5:7b',duration_ms=1))
    request=payload() | {'max_length':85}
    request['facts']['skills']=skills
    response=client(monkeypatch).post('/api/integrations/daybreak/headline',headers={'Authorization':'Bearer '+'d'*48},json=request)
    assert response.status_code==200
    assert response.json()['headline']=='React developer | 6 years | JavaScript | React.js'


def test_core_terms_are_deduplicated_by_case_and_alias_without_changing_facts():
    request=integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'','experience':'3 Years 5 Months','skills':['Identityiq','IIQ','IAM','Rules','Connectors'],'profile_facts':'SailPoint IAM Engineer | 3.5 YOE | IdentityIQ | IAM'},previous_headline='SailPoint IAM Engineer | 3.5 YOE | IdentityIQ | IAM')
    brief=integration._brief(request)
    assert brief['skills'].count('IdentityIQ')==1
    assert 'Identityiq' not in brief['skills'] and 'IIQ' not in brief['skills']
    headline=integration._render(request,{'skills':['Rules','Connectors'],'use_existing':False})
    assert headline.lower().count('identityiq')==1
    assert headline=='SailPoint IAM Engineer | 3.5 YOE | IdentityIQ | IAM | Rules | Connectors'
    assert '3 Years 5 Months' not in headline  # one experience statement, never two formats
    assert request.facts.skills==['Identityiq','IIQ','IAM','Rules','Connectors']


def test_cloud_and_role_aliases_never_create_a_false_factual_gain():
    previous='GCP Data Engineer | 4 Yrs 5 Mos | BigQuery | Dataflow'
    request=integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'GCP Data Engineer','experience':'4 Years 5 Months','skills':['Data Engineering','gcp','Bigquery','google cloud platforms','GCP Dataflow','Bigtable'],'profile_facts':'GCP Data Engineer | 4 Yrs 5 Mos | BigQuery | Dataflow'},previous_headline=previous)
    brief=integration._brief(request)
    assert brief['missing']==['Bigtable']
    assert integration._render(request,{'skills':['Data Engineering','gcp','GCP Dataflow'],'use_existing':False})==previous
    assert 'Bigtable' in integration._render(request,{'skills':['Bigtable'],'use_existing':False})


def test_missing_title_uses_approved_work_focus_not_an_invented_job_title():
    request=integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'','experience':'4 Years 5 Months','skills':['React.js','TypeScript','Frontend Development','Tailwind CSS'],'profile_facts':'4 Years 5 Months of experience in web technologies with JavaScript and React.js and TypeScript'},previous_headline='4 Years 5 Months of experience in web technologies with React.js and TypeScript')
    assert integration._brief(request)['role_context']=='Frontend Development'
    headline=integration._render(request,{'skills':['Tailwind CSS'],'use_existing':False})
    assert headline == request.previous_headline + ' | Tailwind CSS'
    assert 'Developer' not in headline


def test_ambiguous_source_terms_are_omitted_not_silently_corrected():
    request=integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'Web Developer','experience':'5 Years','skills':['React.js','Pas Services','Restfull API','Snow','GraphQL'],'profile_facts':''})
    assert integration._selection_schema(request)['properties']['skills']['items']['enum']==['React.js','GraphQL']
    assert request.facts.skills==['React.js','Pas Services','Restfull API','Snow','GraphQL']


def _platform_request(previous, max_length=250):
    # Invented candidate: the shape of the 27 Oct drafts (six listed skills, three new approved ones).
    skills=['Ci/Cd','Terraform','Docker','Jenkins','Powershell','Ansible','Kubernetes','GIT','Linux','Prometheus','Grafana','Devsecops']
    return integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'Platform Engineer','experience':'4 Years 1 Month','skills':skills,'profile_facts':''},previous_headline=previous,max_length=max_length)


def test_a_new_selection_never_rotates_out_existing_skills():
    # 27 Oct 2026: a fixed six-skill rewrite swapped Docker/Jenkins/Powershell for new skills, so
    # Daybreak's editorial gate refused every draft ("Removes existing approved capability coverage").
    previous='Platform Engineer | 4 Years 1 Month | Ci/Cd, Terraform, Docker, Jenkins, Powershell, Ansible'
    headline=integration._render(_platform_request(previous),{'skills':['Kubernetes','GIT','Linux'],'use_existing':False})
    assert headline==previous+' | Kubernetes | GIT | Linux'
    for kept in ['Ci/Cd','Terraform','Docker','Jenkins','Powershell','Ansible','4 Years 1 Month']:
        assert kept in headline


def test_additions_use_the_requests_naukri_limit_not_a_stale_220_cap():
    # 28 Oct 2026: headlines of 207-220 characters stayed unchanged and selected skills were dropped
    # although Naukri accepts and verifies 250-character headlines.
    previous='Platform Engineer | 4 Years 1 Month | Ci/Cd, Terraform, Docker, Jenkins, Powershell, Ansible | Kubernetes | GIT | Linux'
    previous=previous+' | Release Automation | '+'Observability Dashboards '*4
    previous=previous[:215]+'s'
    assert len(previous)==216  # +13 Prometheus +10 Grafana = 239; +12 Devsecops would be 251
    headline=integration._render(_platform_request(previous),{'skills':['Prometheus','Grafana','Devsecops'],'use_existing':False})
    assert headline.startswith(previous) and 220<len(headline)<=250
    assert headline.endswith(' | Prometheus | Grafana')  # Devsecops no longer fits 250; whole skills only
    tight=integration._render(_platform_request(previous,max_length=230),{'skills':['Prometheus','Grafana'],'use_existing':False})
    assert tight==previous+' | Prometheus' and len(tight)<=230
    full=previous+' | '+'x'*(250-len(previous)-3)
    assert integration._render(_platform_request(full),{'skills':['Grafana'],'use_existing':False})==full


def test_experience_is_appended_only_when_the_headline_states_none():
    previous='Platform Engineer | Ci/Cd, Terraform, Docker'
    assert integration._render(_platform_request(previous),{'skills':['GIT'],'use_existing':False})==previous+' | GIT | 4 Years 1 Month'
    for stated in ['Platform Engineer | 4+ Yrs | Docker','Platform Engineer with 4 Years 1 Month | Docker','Platform Engineer | 4.1 YOE | Docker']:
        assert integration._render(_platform_request(stated),{'skills':['GIT'],'use_existing':False})==stated+' | GIT'


def test_no_verified_baseline_never_produces_a_first_headline(monkeypatch):
    # Deliberate since 18fb435: Daybreak's editorial gate also refuses a save without comparison text.
    monkeypatch.setattr('core.ai_gateway.chat_structured',lambda **_:SimpleNamespace(content='{"skills":["JavaScript","React.js"],"use_existing":false}',node_id='rtx4060',node_label='RTX 4060',model='qwen2.5:7b',duration_ms=1))
    response=client(monkeypatch).post('/api/integrations/daybreak/headline',headers={'Authorization':'Bearer '+'d'*48},json=payload() | {'previous_headline':''})
    assert response.status_code==502


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



def test_without_rewrite_the_existing_selection_flow_is_unchanged(monkeypatch):
    monkeypatch.setattr("core.ai_gateway.chat", lambda **_: (_ for _ in ()).throw(AssertionError("no rewrite requested")))
    monkeypatch.setattr("core.ai_gateway.chat_structured", lambda **_: SimpleNamespace(content='{"skills":[],"use_existing":true}', node_id="rtx4060", node_label="RTX 4060", model="qwen2.5:7b", duration_ms=5))
    body = {**payload(), "previous_headline": "React developer | 6 years | JavaScript | React.js"}
    response = client(monkeypatch).post("/api/integrations/daybreak/headline", headers={"Authorization": "Bearer " + "d" * 48}, json=body)
    assert response.status_code == 200 and response.json()["headline"] == "React developer | 6 years | JavaScript | React.js"


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



def test_rewrite_allows_additional_terms_only_when_approved():
    request = payload() | {'previous_headline': 'Java Developer | 4 Years 3 Months | Spring Boot, MySQL, Kafka | REST APIs', 'rewrite': True}
    request['facts']['skills'].append('AWS')
    candidate = 'Java Developer with 4 Years 3 Months in REST APIs on Kafka, MySQL and Spring Boot with AWS'
    assert integration._rewrite_candidate(integration.DaybreakHeadlineRequest.model_validate(request), candidate) == (candidate, 'accepted')
