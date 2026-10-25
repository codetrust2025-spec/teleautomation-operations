import pytest
from core import ai_gateway, ollama_nodes


def configure(monkeypatch, tmp_path, offline=()):
    monkeypatch.setenv('OLLAMA_NODE_STATE_FILE', str(tmp_path / 'nodes.json'))
    monkeypatch.setattr(ollama_nodes, 'candidate_order', lambda model=None: ['our_machine', 'rtx4060', 'jagadeesh'])
    probes = []
    def node_health(node_id, **kwargs):
        probes.append(node_id)
        return {'endpoint_reachable': node_id not in offline, 'model_available': True, 'error': 'fixture offline'}
    monkeypatch.setattr(ollama_nodes, 'node_health', node_health)
    monkeypatch.setattr(ai_gateway, 'health', lambda **kw: {'endpoint_reachable':kw['node_id'] not in offline,'model_available':True,'error_message':'fixture offline','error_code':'OLLAMA_UNAVAILABLE'})
    monkeypatch.setattr(ai_gateway, '_request_json', lambda *a, **kw: {'message':{'content':'fixture response'}})
    ollama_nodes.reset_breakers()
    return probes


def test_daybreak_uses_rtx_instead_of_another_workloads_primary(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path)
    result = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert result.node_id == 'rtx4060'
    assert 'our_machine' not in probes


def test_daybreak_rtx_outage_uses_health_checked_jagadeesh(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path, offline={'rtx4060'})
    result = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert result.node_id == 'jagadeesh'
    assert probes[:2] == ['rtx4060', 'jagadeesh']
    assert 'our_machine' not in probes


def test_daybreak_recovery_affects_next_request_not_inflight_backup(monkeypatch, tmp_path):
    offline = {'rtx4060'}
    configure(monkeypatch, tmp_path, offline=offline)
    calls = []
    def answer(*args, **kwargs):
        calls.append(kwargs.get('base_url'))
        offline.clear()  # RTX recovers while this backup request is running.
        return {'message': {'content': 'fixture response'}}
    monkeypatch.setattr(ai_gateway, '_request_json', answer)
    first = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    second = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert [first.node_id, second.node_id] == ['jagadeesh', 'rtx4060']
    assert len(calls) == 2


def test_missing_backup_model_fails_closed(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path, offline={'rtx4060'})
    monkeypatch.setattr(ollama_nodes, 'node_health', lambda node_id, **kw: {'endpoint_reachable': node_id != 'rtx4060', 'model_available': False, 'error':'fixture missing model'})
    monkeypatch.setattr(ai_gateway, 'health', lambda **kw: {'endpoint_reachable':kw['node_id']!='rtx4060','model_available':False,'error_message':'fixture missing model','error_code':'OLLAMA_MODEL_NOT_FOUND'})
    with pytest.raises(ai_gateway.AIGatewayError):
        ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)


def test_invalid_primary_output_does_not_repeat_generation_on_backup(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path)
    def invalid(*args, **kwargs):
        raise ai_gateway.AIGatewayError('invalid output', code='OLLAMA_SCHEMA_VALIDATION_FAILED')
    monkeypatch.setattr(ai_gateway, '_request_json', invalid)
    with pytest.raises(ai_gateway.AIGatewayError):
        ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert 'jagadeesh' not in probes


def test_transport_failure_retries_only_generation_on_backup(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs.get('base_url'))
        if kwargs.get('base_url') == ollama_nodes.base_url_for('rtx4060'):
            raise ai_gateway.AIGatewayError('fixture tunnel failure', code='OLLAMA_CONNECTION_FAILED')
        return {'message':{'content':'fixture response'}}
    monkeypatch.setattr(ai_gateway, '_request_json', request)
    result = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=1)
    assert result.node_id == 'jagadeesh'
    assert calls == [ollama_nodes.base_url_for('rtx4060'), ollama_nodes.base_url_for('jagadeesh')]


def test_daybreak_two_node_outage_never_borrows_praveen(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path, offline={'rtx4060','jagadeesh'})
    with pytest.raises(ai_gateway.AIGatewayError):
        ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert 'our_machine' not in probes


def test_other_operations_workloads_keep_their_existing_nodes(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    result = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='generic', max_retries=0)
    assert result.node_id == 'our_machine'
