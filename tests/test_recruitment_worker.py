from workers.recruitment_mail_worker import RecruitmentMailWorker
import asyncio
import threading
import urllib.error

class FakeProvider:
    def __init__(self,_,on_credentials_refreshed=None):self.on_credentials_refreshed=on_credentials_refreshed
    def fetch_new_messages(self,cursor,*,batch_size,since=None):return ([{'id':'m1'}],'history-2')
    def fetch_message(self,message_id):return {'id':message_id,'threadId':'t1','payload':{'headers':[],'body':{}}}
    def fetch_attachments(self,message):return []
    def fetch_messages_by_date(self,start,end,*,limit=500):return [{'id':'m1'}]

def test_worker_completes_incremental_job(monkeypatch):
    import workers.recruitment_mail_worker as module
    mailbox={'id':'mb1','candidate_id':'c1','email_address':'candidate'+'@'+'test.invalid','credential_ciphertext':'encrypted','provider_history_id':'history-1','failed_sync_count':0}
    updates=[];finished=[]
    monkeypatch.setattr(module.store,'mailbox_by_id',lambda _:mailbox)
    monkeypatch.setattr(module.store,'update_mailbox',lambda mid,values:updates.append(values) or mailbox)
    monkeypatch.setattr(module.store,'finish_job',lambda jid,**values:finished.append(values))
    monkeypatch.setattr(module.store,'stage_gmail_messages_and_advance_cursor',lambda mid,refs,cursor:len(refs))
    monkeypatch.setattr(module.store,'claim_gmail_ingestion',lambda mid,limit:[{'id':'q1','provider_message_id':'m1'}])
    monkeypatch.setattr(module.store,'finish_gmail_ingestion',lambda *args,**kwargs:'COMPLETED')
    monkeypatch.setattr(module.store,'pending_gmail_ingestion_count',lambda mid:0)
    monkeypatch.setattr(module,'GmailMailboxProvider',FakeProvider)
    monkeypatch.setattr(module,'decode_gmail_message',lambda raw,email:{'provider_message_id':'m1','provider_thread_id':'t1','sender_email':'jobs'+'@'+'test.invalid','recipient_email':email,'subject':'Interview','sent_at':None,'body':'Scheduled'})
    calls=[]
    monkeypatch.setattr(module,'process_message',lambda mailbox,message,attachments,**kwargs:calls.append(kwargs) or None)
    RecruitmentMailWorker().process_job({'id':'j1','mailbox_id':'mb1','attempts':1})
    assert finished[-1]['status']=='COMPLETED'
    assert finished[-1]['counts']=={'fetched':1,'processed':1,'events':0}
    assert calls == [{'reprocess':False,'defer_ai':True}]
    assert any(v.get('last_successful_sync_at') for v in updates)


def test_worker_reprocesses_historical_messages_without_duplicate_download(monkeypatch):
    import workers.recruitment_mail_worker as module
    mailbox={'id':'mb1','candidate_id':'c1','email_address':'candidate@test.invalid','credential_ciphertext':'encrypted','provider_history_id':'history-1','failed_sync_count':0}
    finished=[];calls=[]
    monkeypatch.setattr(module.store,'mailbox_by_id',lambda _:mailbox)
    monkeypatch.setattr(module.store,'update_mailbox',lambda mid,values:mailbox)
    monkeypatch.setattr(module.store,'finish_job',lambda jid,**values:finished.append(values))
    monkeypatch.setattr(module.store,'pending_gmail_ingestion_count',lambda mid:0)
    monkeypatch.setattr(module.store,'stored_message',lambda *args:{'id':'stored','provider_message_id':'m1','provider_thread_id':None,'sender_name':'HR','sender_email':'hr@test.invalid','recipient_email':'candidate@test.invalid','subject':'Joining','sent_at':None,'body_text':'Your joining date is 15 July 2026.','html_body_text':'','processing_status':'IGNORED_NOT_OFFER_RELATED'})
    monkeypatch.setattr(module.store,'attachments_for_message',lambda *args,**kwargs:[])
    monkeypatch.setattr(module,'GmailMailboxProvider',FakeProvider)
    monkeypatch.setattr(module,'process_message',lambda mailbox,message,attachments,**kwargs:calls.append(kwargs) or {'id':'event1'})
    RecruitmentMailWorker().process_job({'id':'j2','mailbox_id':'mb1','attempts':1,'job_type':'HISTORICAL_RESCAN','range_start':__import__('datetime').date(2026,7,1),'range_end':__import__('datetime').date(2026,7,14)})
    assert calls == [{'reprocess':True,'defer_ai':True}]
    assert finished[-1]['status']=='COMPLETED'
    assert finished[-1]['counts']=={'fetched':1,'processed':1,'events':1}


def test_worker_skips_deleted_gmail_message_instead_of_failing_batch(monkeypatch):
    import workers.recruitment_mail_worker as module
    class DeletedProvider(FakeProvider):
        def fetch_message(self,message_id):raise urllib.error.HTTPError('gmail',404,'deleted',{},None)
    mailbox={'id':'mb1','candidate_id':'c1','email_address':'candidate@test.invalid','credential_ciphertext':'encrypted','provider_history_id':'history-1','failed_sync_count':0}
    finished=[]
    monkeypatch.setattr(module.store,'mailbox_by_id',lambda _:mailbox)
    monkeypatch.setattr(module.store,'update_mailbox',lambda mid,values:mailbox)
    monkeypatch.setattr(module.store,'finish_job',lambda jid,**values:finished.append(values))
    monkeypatch.setattr(module.store,'stage_gmail_messages_and_advance_cursor',lambda mid,refs,cursor:len(refs))
    monkeypatch.setattr(module.store,'claim_gmail_ingestion',lambda mid,limit:[{'id':'q1','provider_message_id':'m1'}])
    monkeypatch.setattr(module.store,'finish_gmail_ingestion',lambda *args,**kwargs:'DELETED')
    monkeypatch.setattr(module.store,'pending_gmail_ingestion_count',lambda mid:0)
    monkeypatch.setattr(module,'GmailMailboxProvider',DeletedProvider)
    RecruitmentMailWorker().process_job({'id':'j3','mailbox_id':'mb1','attempts':1})
    assert finished[-1]['status']=='COMPLETED'
    assert finished[-1]['counts']=={'fetched':1,'processed':0,'events':0}


def test_worker_does_not_advance_cursor_when_durable_staging_fails(monkeypatch):
    import workers.recruitment_mail_worker as module
    mailbox={'id':'mb1','candidate_id':'c1','email_address':'candidate@test.invalid','credential_ciphertext':'encrypted','provider_history_id':'history-1','failed_sync_count':0}
    updates=[];finished=[]
    monkeypatch.setattr(module.store,'mailbox_by_id',lambda _:mailbox)
    monkeypatch.setattr(module.store,'update_mailbox',lambda mid,values:updates.append(values) or mailbox)
    monkeypatch.setattr(module.store,'finish_job',lambda jid,**values:finished.append(values))
    monkeypatch.setattr(module.store,'retry_job',lambda *args,**kwargs:'QUEUED')
    monkeypatch.setattr(module.store,'stage_gmail_messages_and_advance_cursor',lambda *_args,**_kwargs:(_ for _ in ()).throw(RuntimeError('database unavailable')))
    monkeypatch.setattr(module,'GmailMailboxProvider',FakeProvider)
    RecruitmentMailWorker().process_job({'id':'j4','mailbox_id':'mb1','attempts':1})
    assert finished[-1]['status']=='FAILED'
    assert all('provider_history_id' not in values and 'sync_cursor' not in values for values in updates)


def test_worker_claims_mailbox_job_while_ai_recovery_is_blocked(monkeypatch):
    import workers.recruitment_mail_worker as module

    async def scenario():
        # GitHub runners can begin this test with less than 60 seconds of
        # system uptime.  Keep that fresh-host condition deterministic.
        monkeypatch.setattr(module,'_monotonic',lambda:1.0)
        worker=RecruitmentMailWorker()
        recovery_started=threading.Event();recovery_release=threading.Event()
        claimed=[];pending=[{'id':'j-blocked','mailbox_id':'mb1','attempts':1}]

        monkeypatch.setenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','true')
        monkeypatch.setenv('AI_MAILBOX_SYNC_ENABLED','true')
        monkeypatch.setattr(module.store,'recover_interrupted_jobs',lambda:None)
        monkeypatch.setattr(module.store,'claim_job',lambda:pending.pop(0) if pending else None)
        monkeypatch.setattr(worker,'schedule_due',lambda:None)
        monkeypatch.setattr(worker,'renew_due_watches',lambda:None)
        monkeypatch.setattr(worker,'process_job',lambda job:claimed.append(job['id']))
        monkeypatch.setattr(worker,'process_ai_recovery',lambda:(recovery_started.set(),recovery_release.wait(2)))
        worker._last_watch_renewal=module._monotonic()

        worker.start()
        try:
            for _ in range(50):
                if claimed and recovery_started.is_set():break
                await asyncio.sleep(0.02)
            assert recovery_started.is_set()
            assert claimed == ['j-blocked']
        finally:
            recovery_release.set()
            await worker.stop()

    asyncio.run(scenario())
