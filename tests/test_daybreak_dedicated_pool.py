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


def test_daybreak_rtx_outage_never_uses_healthy_jagadeesh(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path, offline={'rtx4060'})
    with pytest.raises(ai_gateway.AIGatewayError):
        ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert 'jagadeesh' not in probes
    assert 'our_machine' not in probes


def test_daybreak_two_node_outage_never_borrows_praveen(monkeypatch, tmp_path):
    probes = configure(monkeypatch, tmp_path, offline={'rtx4060','jagadeesh'})
    with pytest.raises(ai_gateway.AIGatewayError):
        ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='daybreak_headline', max_retries=0)
    assert 'our_machine' not in probes


def test_other_operations_workloads_keep_their_existing_nodes(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    result = ai_gateway.chat(messages=[{'role':'user','content':'fixture'}], model='qwen2.5:7b', workload='generic', max_retries=0)
    assert result.node_id == 'our_machine'
