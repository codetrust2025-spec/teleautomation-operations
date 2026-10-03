"""Durable mailbox scheduler and worker."""
from __future__ import annotations
import asyncio, logging, os, time, urllib.error
from datetime import datetime, timedelta, timezone
from core import recruitment_mail_store as store
from services.gmail_mailbox_provider import (
    GmailAuthorizationExpired,
    GmailMailboxProvider,
    decode_gmail_message,
    encrypt_credentials,
)
from services.recruitment_mail_agent import process_message
from services.unread_interview_mail import surface_unread_interview_mail

logger=logging.getLogger('teleautomation.recruitment_mail_worker')

def _monotonic():
    return time.monotonic()

def _publish(event_type:str,**payload):
    try:
        from core.recruitment_realtime import publish
        publish(event_type,**payload)
    except Exception:
        logger.debug('Mail real-time event unavailable type=%s',event_type,exc_info=True)

class RecruitmentMailWorker:
    def __init__(self):
        self._task=None;self._stopping=False;self._jobs=set()
        self._watch_task=None;self._ai_recovery_task=None
        # Maintenance must be eligible on the first worker loop.  A zero
        # baseline accidentally delays it on freshly booted hosts where
        # time.monotonic() is still below the 60/900-second intervals.
        self._last_watch_renewal=float('-inf');self._last_ai_recovery=float('-inf')
    def start(self):
        if self._task is None or self._task.done():
            store.recover_interrupted_jobs()
            self._stopping=False;self._task=asyncio.create_task(self._run(),name='recruitment-mail-worker')
    async def stop(self):
        self._stopping=True
        if self._task:self._task.cancel();await asyncio.gather(self._task,return_exceptions=True);self._task=None
        for job in self._jobs:job.cancel()
        if self._jobs:await asyncio.gather(*self._jobs,return_exceptions=True)
        self._jobs.clear()
        maintenance=[task for task in (self._watch_task,self._ai_recovery_task) if task]
        for task in maintenance:task.cancel()
        if maintenance:await asyncio.gather(*maintenance,return_exceptions=True)
        self._watch_task=None;self._ai_recovery_task=None
    async def _run_maintenance(self,label,operation):
        try:await asyncio.to_thread(operation)
        except asyncio.CancelledError:raise
        except Exception:logger.exception('Mailbox maintenance failed task=%s',label)
    async def _run(self):
        from core.job_heartbeats import beat
        while not self._stopping:
            beat('mail_worker',interval_sec=5)
            try:
                # A mailbox shown as "Monitoring Active" must always consume
                # its durable sync queue.  Previously a separate environment
                # flag could leave the UI active while every queued mail stayed
                # unprocessed forever.  Keep one explicit feature switch, with
                # tracking enabled by default for connected mailboxes.
                if os.getenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED', 'true').lower() != 'false':
                    await asyncio.to_thread(self.schedule_due)
                    self._jobs={task for task in self._jobs if not task.done()}
                    maximum=max(1,min(20,int(os.getenv('AI_MAIL_SYNC_MAX_CONCURRENCY','5'))))
                    while len(self._jobs)<maximum:
                        job=await asyncio.to_thread(store.claim_job)
                        if not job:break
                        self._jobs.add(asyncio.create_task(asyncio.to_thread(self.process_job,job),name=f"mailbox-sync-{job['id']}"))
                    # Watch renewal and AI recovery can each spend minutes waiting
                    # on external services.  Keep them off the queue-consumer path
                    # so a slow Ollama response cannot leave Gmail jobs QUEUED.
                    if self._watch_task and self._watch_task.done():self._watch_task=None
                    if self._ai_recovery_task and self._ai_recovery_task.done():self._ai_recovery_task=None
                    now=_monotonic()
                    if self._watch_task is None and now-self._last_watch_renewal>=900:
                        self._last_watch_renewal=now
                        self._watch_task=asyncio.create_task(self._run_maintenance('watch-renewal',self.renew_due_watches),name='mailbox-watch-renewal')
                    if self._ai_recovery_task is None and now-self._last_ai_recovery>=60:
                        self._last_ai_recovery=now
                        self._ai_recovery_task=asyncio.create_task(self._run_maintenance('ai-recovery',self.process_ai_recovery),name='mailbox-ai-recovery')
                    active={*self._jobs,*(task for task in (self._watch_task,self._ai_recovery_task) if task)}
                    if active:
                        await asyncio.wait(active,timeout=1,return_when=asyncio.FIRST_COMPLETED);continue
            except asyncio.CancelledError:raise
            except Exception as exc:
                logger.exception('Mailbox worker loop failed')
                beat('mail_worker',interval_sec=5,ok=False,error=repr(exc))
            await asyncio.sleep(5)
    @staticmethod
    def _terminal_mailbox_ids(cur):
        """Mailboxes whose candidate is closed, rejected or dropped.

        Its own small query here rather than the overview's rows: this is the
        scheduler, not the polled endpoint, and it needs every mailbox rather
        than the ones a page happens to be showing.
        """
        cur.execute("""SELECT m.id,m.candidate_id,COALESCE(l.canonical_candidate_id,m.candidate_id)
             FROM candidate_mailboxes m
             LEFT JOIN candidate_identity_links l ON l.alias_candidate_id=m.candidate_id
            WHERE m.connection_status <> 'SUPERSEDED'""")
        rows=[{'id':r[0],'candidate_id':r[1],'canonical_candidate_id':r[2]} for r in cur.fetchall()]
        return store.terminal_mailbox_ids(rows)

    def schedule_due(self):
        from core.db.connection import get_connection
        with get_connection() as conn,conn.cursor() as cur:
            # A closed, rejected or dropped candidate's mailbox is history: it
            # keeps its linkage and its rows, but nothing new is fetched for it.
            # Derived from stage on every pass, so reopening a candidate puts
            # their mailbox straight back in the schedule.
            terminal=self._terminal_mailbox_ids(cur)
            cur.execute("""INSERT INTO mailbox_sync_jobs(id,mailbox_id,status,scheduled_for,requested_by,created_at)
              SELECT gen_random_uuid()::text,m.id,'QUEUED',now(),'scheduler',now() FROM candidate_mailboxes m
              WHERE m.monitoring_enabled=true AND m.connection_status='CONNECTED' AND COALESCE(m.next_sync_at,now())<=now()
              AND NOT (m.id = ANY(%s))
              AND NOT EXISTS(SELECT 1 FROM mailbox_sync_jobs j WHERE j.mailbox_id=m.id AND j.status IN('QUEUED','RUNNING'))""",
              (list(terminal),))
            mins=max(1,int(os.getenv('AI_MAIL_SYNC_INTERVAL_MINUTES','15')))
            cur.execute("UPDATE candidate_mailboxes SET next_sync_at=now()+(%s||' minutes')::interval WHERE monitoring_enabled=true AND COALESCE(next_sync_at,now())<=now()",(mins,))
    def renew_due_watches(self):
        topic=(os.getenv('GMAIL_PUBSUB_TOPIC') or '').strip()
        if not topic:return
        from core.db.connection import get_connection
        with get_connection() as conn,conn.cursor() as cur:
            # Same rule for push delivery: a terminal candidate's watch is not
            # renewed, so Gmail stops pushing for a mailbox nobody is working.
            terminal=self._terminal_mailbox_ids(cur)
            cur.execute("""SELECT id,credential_ciphertext,provider_history_id FROM candidate_mailboxes
              WHERE monitoring_enabled=true AND connection_status='CONNECTED'
              AND credential_ciphertext IS NOT NULL
              AND NOT (id = ANY(%s))
              AND (gmail_watch_expiration IS NULL OR gmail_watch_expiration<now()+interval '24 hours')
              ORDER BY gmail_watch_expiration NULLS FIRST LIMIT 20""",(list(terminal),))
            rows=cur.fetchall()
        for mailbox_id,cipher,history_id in rows:
            try:
                result=GmailMailboxProvider(cipher).start_watch(topic)
                expiration=None
                try:expiration=datetime.fromtimestamp(int(result.get('expiration'))/1000,tz=timezone.utc)
                except (TypeError,ValueError,OverflowError):pass
                store.update_mailbox(mailbox_id,{'gmail_watch_expiration':expiration,'gmail_watch_topic':topic,
                    'provider_history_id':str(history_id or result.get('historyId') or ''),
                    'sync_cursor':str(history_id or result.get('historyId') or '')})
                logger.info('Gmail watch renewed mailbox_id=%s',mailbox_id)
            except Exception:
                logger.exception('Gmail watch renewal failed mailbox_id=%s; polling fallback remains active',mailbox_id)
    def process_ai_recovery(self):
        """Process leased semantic work independently from Gmail ingestion."""
        # Legacy review rows are converted before checking Ollama health.  An
        # outage must leave them in the durable automatic queue, never in a
        # human action list; when the gateway recovers the same worker claims
        # them with normal backoff and leasing.
        promoted = store.promote_legacy_review_states()
        ignored = store.promote_ignored_messages()
        if promoted or ignored:
            _publish(
                'mail_automation_state_updated', processing_status='AI_RETRY_PENDING',
                promoted_retry_count=promoted, auto_ignore_count=ignored,
            )
        from core.ai_gateway import health
        status=health(timeout=5)
        if not status.get('endpoint_reachable') or not status.get('model_available'):
            return
        # Historical calendar recovery is deliberately target-bounded during
        # rollout.  A deployment may name the two verified production message
        # ids; no broad archive replay can create slots before its read-only
        # reconciliation report is reviewed.
        target_ids=[value.strip() for value in os.getenv('AI_CALENDAR_RECOVERY_MESSAGE_IDS','').split(',') if value.strip()]
        for row in store.claim_calendar_invite_recovery_messages(
            provider_message_ids=target_ids,
            limit=max(1,min(10,int(os.getenv('AI_CALENDAR_RECOVERY_BATCH_SIZE','2')))),
        ):
            mailbox={'id':row['mailbox_id'],'candidate_id':row.get('mailbox_candidate_id') or row.get('candidate_id'),'email_address':row.get('email_address')}
            decoded={'provider_message_id':row.get('provider_message_id'),'provider_thread_id':row.get('provider_thread_id'),
              'sender_name':row.get('sender_name'),'sender_email':row.get('sender_email'),'recipient_email':row.get('recipient_email'),
              'subject':row.get('subject'),'sent_at':row.get('sent_at'),'body':row.get('body_text') or '',
              'html_body':row.get('html_body_text') or '','authentication_results':row.get('authentication_results'),
              'received_spf':row.get('received_spf'),'rfc_message_id':row.get('rfc_message_id'),
              'message_direction':row.get('message_direction'),'gmail_label_ids':row.get('gmail_label_ids') or [],
              'to_metadata':row.get('to_metadata') or []}
            try:
                event=process_message(mailbox,decoded,row.get('attachments') or [],reprocess=True)
                refreshed=store.stored_message(row['mailbox_id'],row['provider_message_id']) or {}
                state=str((event or {}).get('auto_booking',{}).get('automation_state') or refreshed.get('processing_status') or 'AI_RETRY_PENDING').upper()
                store.complete_calendar_invite_recovery(row['id'],state=state,reason=str(refreshed.get('ignore_reason') or 'CALENDAR_RECOVERY'))
            except Exception as exc:
                store.complete_calendar_invite_recovery(row['id'],state='AI_RETRY_PENDING',reason=type(exc).__name__)
                logger.exception('Calendar invite recovery failed message_id=%s',row['id'])
        maximum=max(1,min(20,int(os.getenv('AI_MAIL_AI_RETRY_BATCH_SIZE','3'))))
        # The lease has to outlive a real analysis or the row is reclaimed
        # mid-flight and recycled forever. Production ran a 150s lease against
        # a ~154s run (primary plus validator), which recycled 40 of 63 queued
        # messages and drove retry counts past 100. Floor it at the job budget.
        job_timeout=int(os.getenv('AI_JOB_TIMEOUT',os.getenv('AI_RECRUITMENT_JOB_TIMEOUT_SECONDS','660')))
        configured=int(os.getenv('AI_MAIL_AI_LEASE_SECONDS','150'))
        lease=max(60,min(900,max(configured,job_timeout+60)))
        for _ in range(maximum):
            claimed=store.claim_ai_messages(limit=1,lease_seconds=lease)
            if not claimed:break
            row=claimed[0]
            mailbox={'id':row['mailbox_id'],'candidate_id':row.get('mailbox_candidate_id') or row.get('candidate_id'),'email_address':row.get('email_address')}
            decoded={'provider_message_id':row.get('provider_message_id'),'provider_thread_id':row.get('provider_thread_id'),
              'sender_name':row.get('sender_name'),'sender_email':row.get('sender_email'),'recipient_email':row.get('recipient_email'),
              'subject':row.get('subject'),'sent_at':row.get('sent_at'),'body':row.get('body_text') or '',
              'html_body':row.get('html_body_text') or '','authentication_results':row.get('authentication_results'),
              'received_spf':row.get('received_spf'),'rfc_message_id':row.get('rfc_message_id'),
              'message_direction':row.get('message_direction'),'gmail_label_ids':row.get('gmail_label_ids') or [],
              'to_metadata':row.get('to_metadata') or []}
            try:
                event=process_message(mailbox,decoded,row.get('attachments') or [],reprocess=True)
                # A failed semantic retry can intentionally return no visible
                # event while leaving the durable message in AI_RETRY_PENDING.
                # Verify persistence instead of treating `None` as success;
                # otherwise the same failed row spins every recovery cycle with
                # a retry count of zero and no backoff.
                refreshed=store.stored_message(row['mailbox_id'],row['provider_message_id'])
                pending=(
                    str((refreshed or {}).get('processing_status') or '').upper()=='AI_RETRY_PENDING'
                    or bool(event and str(event.get('validation_status') or '').upper()=='RETRY_PENDING')
                )
                store.schedule_ai_retry(row['id'],succeeded=not pending)
                if not pending:logger.info('AI retry completed message_id=%s',row['id'])
            except Exception:
                store.schedule_ai_retry(row['id'],succeeded=False)
                logger.exception('AI retry failed message_id=%s',row['id'])
        # Giving up on a mail is not the same as deciding it is nothing. A mail
        # parked here with a calendar event or a meeting room in it is an
        # interview nobody has been told about, so it is written into Mail
        # Alerts for a person to read. Runs after the retries, so a mail only
        # surfaces once its own attempts are actually spent.
        try:
            surfaced=surface_unread_interview_mail()
            if surfaced:
                logger.info('Surfaced %d unread interview mail(s) for review',len(surfaced))
        except Exception:
            logger.exception('Unable to surface unread interview mail')
    def process_job(self,job):
        mailbox=store.mailbox_by_id(job['mailbox_id']);counts={'fetched':0,'processed':0,'events':0}
        if not mailbox:store.finish_job(job['id'],status='FAILED',error='Mailbox not found');return
        active_ingestion=None
        try:
            _publish('mail_processing_started',candidate_id=mailbox.get('candidate_id'),mailbox_id=mailbox.get('id'),processing_status='Processing')
            store.update_mailbox(mailbox['id'],{'last_sync_attempt_at':store.now()})
            # Store each refreshed access token so the next sync starts with a
            # live one instead of spending a 401 and a refresh to discover the
            # stored one expired an hour ago. Survives restarts and deploys.
            def _persist_credentials(creds,_mailbox_id=mailbox['id']):
                store.update_mailbox(_mailbox_id,{'credential_ciphertext':encrypt_credentials(creds)})
            provider=GmailMailboxProvider(mailbox['credential_ciphertext'],on_credentials_refreshed=_persist_credentials); batch=max(1,min(100,int(os.getenv('AI_MAIL_SYNC_BATCH_SIZE','50'))))
            historical=job.get('job_type')=='HISTORICAL_RESCAN'
            if historical:
                refs=provider.fetch_messages_by_date(job['range_start'],job['range_end']);cursor=mailbox.get('provider_history_id') or mailbox.get('sync_cursor')
                ingestion_by_message={}
            else:
                refs,cursor=provider.fetch_new_messages(mailbox.get('provider_history_id') or mailbox.get('sync_cursor'),batch_size=batch)
                # This transaction is the loss-prevention boundary: all IDs are
                # durable before the Gmail cursor can move beyond them.
                store.stage_gmail_messages_and_advance_cursor(mailbox['id'],refs,cursor)
                claimed=store.claim_gmail_ingestion(mailbox['id'],limit=batch)
                refs=[{'id':row['provider_message_id']} for row in claimed]
                ingestion_by_message={row['provider_message_id']:row for row in claimed}
            counts['fetched']=len(refs)
            for ref in refs:
                ingestion=ingestion_by_message.get(ref['id'])
                active_ingestion=ingestion
                stored=store.stored_message(mailbox['id'],ref['id']) if historical else None
                if stored and stored.get('body_text'):
                    raw=None;decoded={"provider_message_id":stored['provider_message_id'],"provider_thread_id":stored.get('provider_thread_id'),"sender_name":stored.get('sender_name'),"sender_email":stored.get('sender_email'),"recipient_email":stored.get('recipient_email'),"subject":stored.get('subject'),"sent_at":stored.get('sent_at'),"body":stored.get('body_text'),"html_body":stored.get('html_body_text') or ''}
                else:
                    try:
                        raw=provider.fetch_message(ref['id'])
                    except urllib.error.HTTPError as exc:
                        if exc.code==404:
                            if ingestion:store.finish_gmail_ingestion(ingestion['id'],status='DELETED',error=exc)
                            active_ingestion=None
                            logger.info('Skipping deleted Gmail message mailbox=%s message=%s',mailbox['id'],ref['id']);continue
                        raise
                    decoded=decode_gmail_message(raw,mailbox['email_address'])
                if decoded.get('provider_thread_id'):
                    try:
                        thread=provider.fetch_thread(decoded['provider_thread_id'])[-5:]
                        decoded['thread_context']=[{k:v for k,v in decode_gmail_message(item,mailbox['email_address']).items() if k in ('subject','body','sender_name','sender_email','sent_at')} for item in thread]
                    except Exception:
                        logger.info('Thread context unavailable mailbox=%s message=%s',mailbox['id'],ref['id'])
                attachments=([{**item,'text':item.get('extracted_text')} for item in store.attachments_for_message(stored['id'],include_text=True)] if stored and stored.get('body_text') else provider.fetch_attachments(raw))
                try:
                    # Gmail ingestion remains independent of remote inference.
                    # Persist the message first, then let the bounded AI recovery
                    # worker classify it with durable retries.
                    event=process_message(mailbox,decoded,attachments,reprocess=historical,defer_ai=True)
                except Exception as exc:
                    if ingestion:
                        store.finish_gmail_ingestion(ingestion['id'],status='QUEUED',error=exc,max_attempts=max(1,int(os.getenv('AI_MAIL_SYNC_MAX_RETRIES','5'))))
                    raise
                if ingestion:store.finish_gmail_ingestion(ingestion['id'],status='COMPLETED')
                active_ingestion=None
                counts['processed']+=1;counts['events']+=1 if event else 0
            pending=store.pending_gmail_ingestion_count(mailbox['id']) if not historical else 0
            values={'connection_status':'CONNECTED','last_successful_sync_at':store.now(),'failed_sync_count':0,'last_error_code':None,'last_error_message':None}
            # A historical scan can overlap newly arriving mail.  Catch up as
            # soon as it ends; durable backlog batches also continue immediately.
            if historical or pending:values['next_sync_at']=store.now()
            store.update_mailbox(mailbox['id'],values)
            store.finish_job(job['id'],status='COMPLETED',counts=counts)
        except Exception as exc:
            if active_ingestion:
                store.finish_gmail_ingestion(active_ingestion['id'],status='QUEUED',error=exc,max_attempts=max(1,int(os.getenv('AI_MAIL_SYNC_MAX_RETRIES','5'))))
            failures=int(mailbox.get('failed_sync_count') or 0)+1;delay=min(240,2**min(failures,8))
            # ERROR is what the dashboard renders as "Gmail connection expired.
            # Reconnect to continue monitoring.", so only a failure a reconnect
            # would actually fix may set it. Everything else keeps the mailbox
            # connected and simply retries: production recorded 28 HTTPError and
            # 2 AttributeError sync failures, and each one told an operator to
            # reconnect an account whose credentials were fine.
            reauthorisation_required=isinstance(exc,GmailAuthorizationExpired)
            values={'failed_sync_count':failures,'last_error_code':type(exc).__name__,'last_error_message':str(exc)[:400],'next_sync_at':store.now()+timedelta(minutes=delay)}
            if reauthorisation_required:values['connection_status']='ERROR'
            store.update_mailbox(mailbox['id'],values)
            store.finish_job(job['id'],status='FAILED',counts=counts,error=str(exc)[:400]);final=store.retry_job(job['id'],delay_minutes=delay,error=str(exc)[:400],max_attempts=max(1,int(os.getenv('AI_MAIL_SYNC_MAX_RETRIES','5'))));logger.warning('Mailbox sync failed mailbox=%s code=%s',mailbox['id'],type(exc).__name__)
            _publish('mail_processing_failed',candidate_id=mailbox.get('candidate_id'),mailbox_id=mailbox.get('id'),processing_status='Processing Failed',error_code=type(exc).__name__)
            if failures>=3 or final=='DEAD_LETTER':
                from services.recruitment_notifications import notify_system
                notify_system('Mailbox synchronization failed',f"Mailbox {mailbox['id']} requires administrator attention.",f"mailbox-failure:{mailbox['id']}")

recruitment_mail_worker=RecruitmentMailWorker()
