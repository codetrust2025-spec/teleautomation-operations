from pathlib import Path
import base64
import json
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.recruitment_mail_api import install_recruitment_mail_routes
from core import recruitment_mail_api


def test_dashboard_query_does_not_use_reserved_day_alias():
    source = Path("core/recruitment_mail_api.py").read_text(encoding="utf-8")
    assert "created_at::date day" not in source
    assert "created_at::date AS event_day" in source

def test_mail_reprocess_paths_preserve_trust_metadata():
    """The worker-owned notification reprocess retains every trust field."""
    source = Path("workers/recruitment_mail_worker.py").read_text(encoding="utf-8")
    for field in (
        "authentication_results", "received_spf", "rfc_message_id",
        "message_direction", "gmail_label_ids", "to_metadata",
    ):
        assert source.count(f"'{field}':row.get('{field}')") >= 1

def app_client(monkeypatch):
    monkeypatch.delenv('DASHBOARD_PASSWORD',raising=False)
    app=FastAPI();install_recruitment_mail_routes(app);return TestClient(app)

def test_feature_config_is_available_when_disabled(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','false')
    response=app_client(monkeypatch).get('/api/ai-recruitment/config')
    assert response.status_code==200
    assert response.json()['enabled'] is False

def test_review_api_is_feature_guarded(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','false')
    response=app_client(monkeypatch).get('/api/ai-recruitment/review')
    assert response.status_code==404


def test_mailbox_connect_reports_missing_oauth_configuration(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    for name in ('GOOGLE_OAUTH_CLIENT_ID','GOOGLE_OAUTH_CLIENT_SECRET','MAILBOX_CREDENTIAL_ENCRYPTION_KEY'):
        monkeypatch.delenv(name,raising=False)
    response=app_client(monkeypatch).post('/api/candidates/example/mailbox/connect',json={
        'email_address':'candidate@example.com',
        'redirect_uri':'https://teleautomation.online/api/candidate-mailboxes/oauth/google/callback',
    })
    assert response.status_code==503
    assert 'Google OAuth is not configured' in response.json()['detail']


def test_mailbox_api_hides_unauthenticated_placeholder(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setattr(recruitment_mail_api.candidate_store, 'get_candidate', lambda _cid: {'id':'current','name':'Ram','phone':'9000000000'})
    monkeypatch.setattr(recruitment_mail_api.candidate_store, 'candidate_identity_ids', lambda _cid: ['current'])
    monkeypatch.setattr(recruitment_mail_api.store, 'mailboxes_for_candidates', lambda _ids: [{
        'id':'mailbox-1','candidate_id':'current','email_address':'ram@example.com',
        'connection_status':'DISCONNECTED','credential_ciphertext':None,
    }])
    monkeypatch.setattr(recruitment_mail_api.store, 'list_events', lambda **_kwargs: [])
    response=app_client(monkeypatch).get('/api/candidates/current/mailbox')
    assert response.status_code==200
    assert response.json()['mailbox'] is None


def test_mailbox_api_resolves_authenticated_legacy_candidate_id(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setattr(recruitment_mail_api.candidate_store, 'get_candidate', lambda _cid: {'id':'current','name':'Rohit','phone':'9000000001'})
    monkeypatch.setattr(recruitment_mail_api.candidate_store, 'candidate_identity_ids', lambda _cid: ['legacy','current'])
    monkeypatch.setattr(recruitment_mail_api.store, 'mailboxes_for_candidates', lambda ids: [{
        'id':'mailbox-2','candidate_id':ids[0],'email_address':'rohit@example.com',
        'connection_status':'CONNECTED','credential_ciphertext':'encrypted-token',
    }])
    monkeypatch.setattr(recruitment_mail_api.store, 'mailbox_stats', lambda _mid: {'important_emails':3})
    monkeypatch.setattr(recruitment_mail_api.store, 'list_events', lambda **_kwargs: [])
    response=app_client(monkeypatch).get('/api/candidates/current/mailbox')
    assert response.status_code==200
    body=response.json()
    assert body['mailbox']['email_address']=='rohit@example.com'
    assert 'credential_ciphertext' not in body['mailbox']


def test_mailbox_health_api_returns_credential_free_status(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setattr(recruitment_mail_api.store,'mailbox_health_rows',lambda:[{
        'id':'mailbox-2','candidate_id':'current','email_address':'rohit@example.com',
        'connection_status':'ERROR','last_error_code':'INVALID_GRANT',
    }])

    response=app_client(monkeypatch).get('/api/candidate-mailboxes/health')

    assert response.status_code==200
    assert response.json()['mailboxes'][0]['connection_status']=='ERROR'
    assert 'credential_ciphertext' not in response.json()['mailboxes'][0]


def test_mailbox_overview_api_returns_bulk_mailboxes_with_stats(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setattr(recruitment_mail_api.store,'mailbox_overview_rows',lambda:[{
        'mailbox': {
            'id':'mailbox-2','candidate_id':'legacy',
            'email_address':'rohit@example.com','connection_status':'CONNECTED',
        },
        'stats': {'important_emails':3,'latest_sync_status':'COMPLETED'},
    }])
    monkeypatch.setattr(
        recruitment_mail_api.candidate_store,
        'canonical_candidate_identity_ids',
        lambda candidate_ids: {
            candidate_id: 'current' if candidate_id == 'legacy' else candidate_id
            for candidate_id in candidate_ids
        },
    )

    response=app_client(monkeypatch).get('/api/candidate-mailboxes/overview')

    assert response.status_code==200
    assert response.json()['mailboxes'][0]['stats']['important_emails']==3
    assert response.json()['mailboxes'][0]['mailbox']['canonical_candidate_id']=='current'
    assert 'credential_ciphertext' not in response.json()['mailboxes'][0]['mailbox']


def test_notification_list_and_summary_are_persistent_api_fallbacks(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    row={'id':'n1','candidate_id':'c1','classification':'offer_received','is_read':False}
    monkeypatch.setattr(recruitment_mail_api.store,'list_notifications',lambda **_kwargs:([row],1))
    monkeypatch.setattr(recruitment_mail_api.store,'notification_summary',lambda:{'unread':1,'new_offers':1})
    client=app_client(monkeypatch)
    listed=client.get('/api/mail-monitoring/notifications?classification=offer_received')
    summary=client.get('/api/mail-monitoring/summary')
    assert listed.status_code==200 and listed.json()['total']==1
    assert listed.json()['notifications'][0]['classification']=='offer_received'
    assert summary.json()['summary']=={'unread':1,'new_offers':1}


def test_notification_correction_is_retired_without_mutation(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    audits=[]
    monkeypatch.setattr(recruitment_mail_api.store,'update_notification',lambda *args,**kwargs:{'id':'n1','candidate_id':'c1','classification':'joining_confirmed'})
    monkeypatch.setattr(recruitment_mail_api.store,'audit',lambda **kwargs:audits.append(kwargs))
    response=app_client(monkeypatch).post('/api/mail-monitoring/notifications/n1/correct',json={
        'notes':'Confirmed from the source email',
        'changes':{'classification':'joining_confirmed','candidate_status':'Joining Confirmed'},
    })
    assert response.status_code==410
    assert audits == []


def pubsub_payload(message_id='push-1'):
    data=base64.b64encode(json.dumps({'emailAddress':'candidate@test.invalid','historyId':'1234'}).encode()).decode()
    return {'message':{'messageId':message_id,'data':data},'subscription':'projects/example/subscriptions/mail'}


def test_gmail_pubsub_push_only_queues_history_sync(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setenv('GMAIL_PUBSUB_VERIFICATION_TOKEN','secret')
    jobs=[];updates=[]
    monkeypatch.setattr(recruitment_mail_api.store,'mailbox_by_email',lambda email:{'id':'mb1','candidate_id':'c1'})
    monkeypatch.setattr(recruitment_mail_api.store,'record_pubsub_delivery',lambda *args,**kwargs:True)
    monkeypatch.setattr(recruitment_mail_api.store,'update_mailbox',lambda *args,**kwargs:updates.append((args,kwargs)) or {})
    monkeypatch.setattr(recruitment_mail_api.store,'enqueue_sync',lambda *args,**kwargs:jobs.append((args,kwargs)) or {'id':'j1'})
    response=app_client(monkeypatch).post('/api/gmail/pubsub/push?token=secret',json=pubsub_payload())
    assert response.status_code==200 and response.json()['status']=='accepted'
    assert jobs[0][1]['requested_by']=='gmail-pubsub'
    assert updates[0][0][1]['last_push_history_id']=='1234'


def test_gmail_pubsub_duplicate_is_acknowledged_without_new_job(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setenv('GMAIL_PUBSUB_VERIFICATION_TOKEN','secret')
    monkeypatch.setattr(recruitment_mail_api.store,'mailbox_by_email',lambda email:{'id':'mb1'})
    monkeypatch.setattr(recruitment_mail_api.store,'record_pubsub_delivery',lambda *args,**kwargs:False)
    monkeypatch.setattr(recruitment_mail_api.store,'enqueue_sync',lambda *args,**kwargs:pytest.fail('duplicate must not enqueue'))
    response=app_client(monkeypatch).post('/api/gmail/pubsub/push?token=secret',json=pubsub_payload())
    assert response.status_code==200 and response.json()['status']=='duplicate'


def test_gmail_pubsub_rejects_bad_token_without_reading_mailbox(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    monkeypatch.setenv('GMAIL_PUBSUB_VERIFICATION_TOKEN','secret')
    response=app_client(monkeypatch).post('/api/gmail/pubsub/push?token=wrong',json=pubsub_payload())
    assert response.status_code==403


def test_manual_approve_and_book_is_retired_in_favor_of_worker_automation(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    from services import interview_auto_booking
    monkeypatch.setattr(
        interview_auto_booking, 'execute_manual_approved_booking',
        lambda **_kwargs: pytest.fail('manual booking must not be reachable'),
    )

    response=app_client(monkeypatch).post('/api/ai-recruitment/events/e1/approve-and-book',json={})
    assert response.status_code==410
    assert 'fully automated' in response.json()['detail']


def test_manual_event_edit_and_offer_actions_are_retired(monkeypatch):
    monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
    client=app_client(monkeypatch)

    event_edit=client.patch('/api/ai-recruitment/events/e1',json={'changes':{'summary':'override'}})
    offer_verify=client.post('/api/offer-verification/o1/verify',json={})

    assert event_edit.status_code==410
    assert offer_verify.status_code==410
