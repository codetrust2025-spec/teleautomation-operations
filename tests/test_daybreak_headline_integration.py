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
        "status": "ok", "headline": "React developer | 6 years | JavaScript, React.js",
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


def test_actual_route_keeps_exact_months_and_candidate_scoped_schema(monkeypatch):
    calls=[]
    def selected(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"skills":["JavaScript","React.js"],"use_existing":false}',node_id='rtx4060',node_label='RTX 4060',model='qwen2.5:7b',duration_ms=1)
    monkeypatch.setattr('core.ai_gateway.chat_structured',selected)
    request=payload() | {'previous_headline':'React developer | 3.5 YOE | JavaScript, React.js'}
    request['facts']['experience']='3 Years 5 Months'
    response=client(monkeypatch).post('/api/integrations/daybreak/headline',headers={'Authorization':'Bearer '+'d'*48},json=request)
    assert response.status_code==200
    assert response.json()['headline']=='React developer | 3 Years 5 Months | JavaScript, React.js'
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
    assert response.json()['headline']=='React developer | 6 years | JavaScript, React.js'


def test_core_terms_are_deduplicated_by_case_and_alias_without_changing_facts():
    request=integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'','experience':'3 Years 5 Months','skills':['Identityiq','IIQ','IAM','Rules','Connectors'],'profile_facts':'SailPoint IAM Engineer | 3.5 YOE | IdentityIQ | IAM'},previous_headline='SailPoint IAM Engineer | 3.5 YOE | IdentityIQ | IAM')
    brief=integration._brief(request)
    assert brief['skills'].count('IdentityIQ')==1
    assert 'Identityiq' not in brief['skills'] and 'IIQ' not in brief['skills']
    headline=integration._render(request,{'skills':['Rules','Connectors'],'use_existing':False})
    assert headline.lower().count('identityiq')==1
    assert '3 Years 5 Months' in headline
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
    assert headline.startswith('Frontend Development | 4 Years 5 Months |')
    assert 'Developer' not in headline


def test_ambiguous_source_terms_are_omitted_not_silently_corrected():
    request=integration.DaybreakHeadlineRequest(candidate_ref='own',facts={'target_role':'Web Developer','experience':'5 Years','skills':['React.js','Pas Services','Restfull API','Snow','GraphQL'],'profile_facts':''})
    assert integration._selection_schema(request)['properties']['skills']['items']['enum']==['React.js','GraphQL']
    assert request.facts.skills==['React.js','Pas Services','Restfull API','Snow','GraphQL']
