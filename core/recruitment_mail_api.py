"""Authenticated API for candidate mailbox tracking and AI review."""
from __future__ import annotations
import asyncio, base64, functools, hashlib, hmac, json, logging, os, time
from datetime import date, datetime, timedelta, timezone
from fastapi import HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from core import recruitment_mail_store as store
from core.dashboard_access import operator_profile, require_fleet_admin, assert_candidate_row_access
from core.ai_gateway import AIGatewayError, chat_structured, configured_models, health as ollama_health
from core.ollama_status import snapshot as ollama_status_snapshot
from core import ai_activity
from core import ollama_nodes
from core import gmail_watch
from features import candidate_store
from services.gmail_mailbox_provider import authorization_url, exchange_code, encrypt_credentials, GmailMailboxProvider

logger=logging.getLogger('teleautomation.recruitment_mail_api')

def enabled():return os.getenv('AI_INTERVIEW_OFFER_TRACKING_ENABLED','false').lower()=='true'
def offer_enabled():return os.getenv('AI_OFFER_VERIFICATION_ENABLED','false').lower()=='true'
def oauth_configured():
    return all((os.getenv(name) or '').strip() for name in ('GOOGLE_OAUTH_CLIENT_ID','GOOGLE_OAUTH_CLIENT_SECRET','MAILBOX_CREDENTIAL_ENCRYPTION_KEY'))
def _require_oauth_config():
    if not oauth_configured():raise HTTPException(503,'Google OAuth is not configured. Ask an administrator to configure Gmail read-only access before connecting a mailbox.')
def _guard():
    if not enabled():raise HTTPException(404,'AI selection and offer detection is disabled')
def _state(data:dict)->str:
    raw=base64.urlsafe_b64encode(json.dumps({**data,'exp':int(time.time())+600}).encode()).decode().rstrip('=');key=(os.getenv('DASHBOARD_AUTH_SECRET') or os.getenv('DASHBOARD_PASSWORD') or '').encode()
    if not key:raise HTTPException(503,'OAuth state signing is not configured')
    return raw+'.'+hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()
def _read_state(value:str)->dict:
    raw,sig=value.rsplit('.',1);key=(os.getenv('DASHBOARD_AUTH_SECRET') or os.getenv('DASHBOARD_PASSWORD') or '').encode()
    if not hmac.compare_digest(sig,hmac.new(key,raw.encode(),hashlib.sha256).hexdigest()):raise HTTPException(400,'Invalid OAuth state')
    data=json.loads(base64.urlsafe_b64decode(raw+'==='));
    if data['exp']<time.time():raise HTTPException(400,'OAuth request expired')
    return data

def _identity_ids(candidate_id:str)->list[str]:
    return candidate_store.candidate_identity_ids(candidate_id)
def _identity_mailbox(candidate_id:str):
    return store.mailbox_for_candidates(_identity_ids(candidate_id))
def _identity_events(candidate_id:str,*,limit:int=100,offset:int=0):
    events=[]
    for alias in _identity_ids(candidate_id):
        events.extend(store.list_events(candidate_id=alias,limit=limit,offset=0))
    unique={str(event.get('id')):event for event in events if event.get('id')}
    ordered=sorted(unique.values(),key=lambda event:str(event.get('created_at') or ''),reverse=True)
    return ordered[offset:offset+limit]

def _mailbox_overview_rows():
    """Build the complete overview off the event loop in bounded bulk reads."""
    rows=store.mailbox_overview_rows()
    candidate_ids=[
        str((entry.get('mailbox') or entry).get('candidate_id') or '')
        for entry in rows
    ]
    canonical_ids=candidate_store.canonical_candidate_identity_ids(candidate_ids)
    # Persisted identity links can point at an older canonical row after
    # profile candidates are collapsed again. Resolve against the current
    # Candidates API representation so connected Gmail accounts never
    # disappear from the mailbox roster.
    for entry in rows:
        mailbox=entry.get('mailbox') or entry
        candidate_id=str(mailbox.get('candidate_id') or '')
        mailbox['canonical_candidate_id']=canonical_ids.get(candidate_id,candidate_id)
    return rows

def _node_status(node_id:str)->dict:
    models=configured_models()
    required=list(dict.fromkeys(filter(None,(models.get('text'),models.get('validator'),models.get('vision')))))
    status=ollama_nodes.node_health(node_id,model=models['vision'])
    installed=[str(name) for name in status.get('installed_models',[])]
    availability={
        model:any(name.removesuffix(':latest')==model.removesuffix(':latest') for name in installed)
        for model in required
    }
    status['required_models']=availability
    status['endpoint']=ollama_nodes.base_url_for(node_id)
    status['ready']=bool(status.get('endpoint_reachable')) and all(availability.values())
    if status.get('endpoint_reachable') and not status['ready']:
        status['status']='degraded'
    return status

def _renew_gmail_watch(mailbox:dict)->dict|None:
    # The implementation moved to core.gmail_watch so the background renewal
    # loop can reuse it without importing this FastAPI module.  The three
    # request handlers below still call it exactly as before.
    return gmail_watch.renew_watch(mailbox)

def install_recruitment_mail_routes(app):
    if enabled():
        store.ensure_schema()
    @app.get('/api/ai-recruitment/config')
    async def feature_config(request:Request):
        require_fleet_admin(request)
        return {'status':'ok','enabled':enabled(),'mailbox_sync_enabled':os.getenv('AI_MAILBOX_SYNC_ENABLED','false').lower()=='true',
          'interview_auto_booking_enabled':os.getenv('AI_INTERVIEW_AUTO_BOOKING_ENABLED','false').lower()=='true',
          'gmail_pubsub_configured':bool((os.getenv('GMAIL_PUBSUB_TOPIC') or '').strip() and (os.getenv('GMAIL_PUBSUB_VERIFICATION_TOKEN') or '').strip()),
          'offer_verification_enabled':offer_enabled(),'oauth_configured':oauth_configured()}
    @app.get('/api/candidate-mailboxes/health')
    async def candidate_mailbox_health(request:Request):
        _guard();require_fleet_admin(request)
        return {'status':'ok','mailboxes':await asyncio.to_thread(store.mailbox_health_rows),'checked_at':datetime.now(timezone.utc)}
    @app.get('/api/candidate-mailboxes/overview')
    async def candidate_mailbox_overview(request:Request):
        _guard();require_fleet_admin(request)
        rows=await asyncio.to_thread(_mailbox_overview_rows)
        return {'status':'ok','mailboxes':rows,'checked_at':datetime.now(timezone.utc)}
    @app.post('/api/gmail/pubsub/push')
    async def gmail_pubsub_push(request:Request):
        """Acknowledge a Gmail mailbox-change envelope and queue History API sync."""
        if not enabled():raise HTTPException(404,'Mail monitoring is disabled')
        expected=(os.getenv('GMAIL_PUBSUB_VERIFICATION_TOKEN') or '').strip()
        supplied=(request.query_params.get('token') or request.headers.get('X-TeleAutomation-PubSub-Token') or '').strip()
        if not expected:raise HTTPException(503,'Gmail Pub/Sub verification is not configured')
        if not hmac.compare_digest(expected,supplied):raise HTTPException(403,'Invalid Pub/Sub verification token')
        try:
            envelope=await request.json();pubsub=envelope.get('message') or {}
            pubsub_id=str(pubsub.get('messageId') or pubsub.get('message_id') or '').strip()
            decoded=json.loads(base64.b64decode(str(pubsub.get('data') or '')+'===').decode('utf-8'))
            email=str(decoded.get('emailAddress') or '').strip().lower();history=str(decoded.get('historyId') or '').strip()
            if not pubsub_id or '@' not in email or not history:raise ValueError('missing Pub/Sub fields')
        except Exception as exc:
            logger.warning('Invalid Gmail Pub/Sub envelope code=%s',type(exc).__name__)
            raise HTTPException(400,'Invalid Gmail Pub/Sub envelope') from exc
        mailbox=await asyncio.to_thread(store.mailbox_by_email,email)
        inserted=await asyncio.to_thread(store.record_pubsub_delivery,pubsub_id,
            subscription=str(envelope.get('subscription') or ''),email_address=email,history_id=history,
            mailbox_id=mailbox.get('id') if mailbox else None,status='QUEUED' if mailbox else 'MAILBOX_NOT_FOUND')
        if not inserted:return {'status':'duplicate'}
        if mailbox:
            await asyncio.to_thread(store.update_mailbox,mailbox['id'],{'last_push_history_id':history,'next_sync_at':store.now()})
            job=await asyncio.to_thread(store.enqueue_sync,mailbox['id'],requested_by='gmail-pubsub')
            logger.info('Gmail mailbox change queued pubsub_message_id=%s mailbox_id=%s history_id=%s job_id=%s',pubsub_id,mailbox['id'],history,job.get('id'))
        else:
            logger.warning('Gmail Pub/Sub mailbox not found pubsub_message_id=%s email_hash=%s',pubsub_id,hashlib.sha256(email.encode()).hexdigest()[:12])
        return {'status':'accepted'}
    @app.get('/api/ai-recruitment/ollama/status')
    async def ollama_status(request:Request,refresh:bool=False):
        _guard();require_fleet_admin(request)
        status = await __import__('asyncio').to_thread(ollama_health) if refresh else ollama_status_snapshot()
        return {'status':'ok','ollama':status,'models':configured_models()}
    @app.get('/api/ai-recruitment/ollama/nodes')
    async def ollama_node_statuses(request:Request):
        _guard();require_fleet_admin(request)
        nodes=await asyncio.gather(*[
            asyncio.to_thread(_node_status,item['id'])
            for item in ollama_nodes.configured_nodes()
        ])
        # What each node is serving, from the gateway's own record: the same
        # source the booking page names its node from. Read once, after the
        # probes, so every node is described at the same moment.
        activity=ai_activity.node_snapshot()
        for node in nodes:
            node['activity']=activity['nodes'].get(node['id'],[])
        return {'status':'ok','primary_node':ollama_nodes.primary_node_id(),'nodes':nodes,'activity':activity}
    @app.get('/api/ai-recruitment/ollama/activity')
    async def ollama_node_activity(request:Request):
        # Cheap: no node is probed. The panel reads it when its live socket
        # (re)connects, since pushes sent while it was away are not replayed.
        _guard();require_fleet_admin(request)
        return {'status':'ok','activity':ai_activity.node_snapshot()}
    @app.post('/api/ai-recruitment/ollama/nodes/{node_id}/primary')
    async def ollama_set_primary(node_id:str,request:Request,override:bool=False):
        _guard();require_fleet_admin(request)
        try:status=await asyncio.to_thread(_node_status,node_id)
        except ValueError as exc:raise HTTPException(404,'Unknown Ollama node') from exc
        # A node that fails its model check stays unselectable unless an admin
        # says so deliberately. Kept as an explicit opt-in rather than a silent
        # allow, because a primary that cannot run the model takes every AI
        # feature down until someone notices.
        if not status.get('ready') and not override:
            raise HTTPException(409,'This node cannot become primary until it is online and all required models are installed.')
        await asyncio.to_thread(functools.partial(ollama_nodes.set_primary_node,node_id,force=bool(override)))
        status['primary']=True
        return {'status':'ok','primary_node':node_id,'node':status}
    @app.post('/api/ai-recruitment/ollama/nodes/{node_id}/unload')
    async def ollama_unload_node(node_id:str,request:Request):
        _guard();require_fleet_admin(request)
        try:
            health=await asyncio.to_thread(_node_status,node_id)
            if not health.get('endpoint_reachable'):raise HTTPException(409,'The selected Ollama node is offline.')
            configured=list(dict.fromkeys(filter(None,configured_models().values())))
            loaded=[str(model) for model in health.get('loaded_models',[])]
            models=[
                model for model in configured
                if any(name.removesuffix(':latest')==model.removesuffix(':latest') for name in loaded)
            ]
            results=[]
            for model in models:
                results.append(await asyncio.to_thread(ollama_nodes.unload_model,node_id,model=model))
            refreshed=await asyncio.to_thread(_node_status,node_id)
        except ValueError as exc:raise HTTPException(404,'Unknown Ollama node') from exc
        except RuntimeError as exc:raise HTTPException(409,str(exc)) from exc
        return {'status':'ok','node':refreshed,'models_unloaded':sum(1 for result in results if result.get('unloaded'))}
    @app.post('/api/ai-recruitment/ollama/test-connection')
    async def ollama_test_connection(request:Request):
        _guard();require_fleet_admin(request)
        status=await __import__('asyncio').to_thread(ollama_health)
        return {'status':'ok' if status.get('endpoint_reachable') else 'error','ollama':status}
    @app.post('/api/ai-recruitment/ollama/test-model')
    async def ollama_test_model(request:Request):
        _guard();require_fleet_admin(request)
        schema={'type':'object','required':['ok'],'properties':{'ok':{'type':'boolean'}}}
        try:
            result=await __import__('asyncio').to_thread(chat_structured,messages=[{'role':'user','content':'Return only JSON with ok set to true.'}],schema=schema,model=configured_models()['primary'],max_retries=0)
            return {'status':'ok','model':result.model,'response_time_ms':result.duration_ms}
        except AIGatewayError as exc:
            return {'status':'error','error_code':exc.code,'error_message':str(exc),'ollama':ollama_status_snapshot()}
    @app.get('/api/candidates/{candidate_id}/mailbox')
    async def mailbox(candidate_id:str,request:Request,response:Response):
        _guard(); row=candidate_store.get_candidate(candidate_id)
        if not row:raise HTTPException(404,'Candidate not found')
        assert_candidate_row_access(request,row)
        all_mbs=store.mailboxes_for_candidates(_identity_ids(candidate_id))
        # Strip credentials, attach stats
        result=[]
        for mb in all_mbs:
            if not mb.get('credential_ciphertext'):continue
            safe={k:v for k,v in mb.items() if k!='credential_ciphertext'}
            stats=store.mailbox_stats(mb['id'])
            result.append({'mailbox':safe,'stats':stats})
        # Backward compat: also expose single .mailbox field (first/best)
        best=result[0] if result else None
        response.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma']='no-cache'
        response.headers['Expires']='0'
        return {'status':'ok','mailbox':best['mailbox'] if best else None,'stats':best['stats'] if best else {},'mailboxes':result,'events':_identity_events(candidate_id,limit=20),'_timestamp':int(time.time())}
    @app.post('/api/candidates/{candidate_id}/mailbox/connect')
    async def connect(candidate_id:str,request:Request,body:dict):
        _guard();_require_oauth_config();profile=require_fleet_admin(request);row=candidate_store.get_candidate(candidate_id)
        if not row:raise HTTPException(404,'Candidate not found')
        candidate_id=candidate_store.canonical_candidate_identity_id(candidate_id)
        email=str(body.get('email_address') or '').strip().lower()
        if '@' not in email:raise HTTPException(400,'Valid Gmail address required')
        mb=store.upsert_mailbox(candidate_id,email,identity_ids=_identity_ids(candidate_id),connection_status='AUTHORIZING');store.audit(actor=profile.get('username') or 'admin',role='admin',action='MAILBOX_CONNECT_STARTED',candidate_id=candidate_id,source_id=mb['id'],new={'email_address':email},source_ip=request.client.host if request.client else None)
        redirect=str(body.get('redirect_uri') or os.getenv('GOOGLE_OAUTH_REDIRECT_URI') or '').strip()
        if not redirect:raise HTTPException(503,'GOOGLE_OAUTH_REDIRECT_URI is not configured')
        return {'status':'ok','authorization_url':authorization_url(_state({'candidate_id':candidate_id,'email':email,'actor':profile.get('username'),'redirect_uri':redirect}),redirect)}
    @app.get('/api/candidate-mailboxes/oauth/google/callback')
    async def callback(code:str,state:str):
        _guard();data=_read_state(state);tokens=exchange_code(code,data['redirect_uri'])
        if not tokens.get('refresh_token'):raise HTTPException(400,'Google did not provide offline mailbox authorization; reconnect and grant access')
        cipher=encrypt_credentials({'refresh_token':tokens.get('refresh_token'),'access_token':tokens.get('access_token')})
        gmail_profile=GmailMailboxProvider(cipher).verify_connection();authorized=str(gmail_profile.get('emailAddress') or '').strip().lower()
        if authorized!=str(data['email']).strip().lower():raise HTTPException(400,'The authorized Gmail account does not match the selected mailbox address')
        candidate_id=candidate_store.canonical_candidate_identity_id(data['candidate_id'])
        identity_ids=_identity_ids(candidate_id)
        mb=store.upsert_mailbox(candidate_id,data['email'],identity_ids=identity_ids,monitoring_enabled=True,connection_status='CONNECTED',credential_ciphertext=cipher)
        mb=store.update_mailbox(mb['id'],{'connection_status':'CONNECTED','monitoring_enabled':True,'failed_sync_count':0,'last_error_code':None,'last_error_message':None,'next_sync_at':datetime.now(timezone.utc)})
        store.supersede_duplicate_mailboxes(identity_ids,data['email'],mb['id'])
        actor=data.get('actor') or 'admin';store.audit(actor=actor,role='admin',action='MAILBOX_CONNECTED',candidate_id=candidate_id,source_id=mb['id'],new={'email_address':data['email']})
        if os.getenv('GMAIL_PUBSUB_TOPIC'):
            try:mb=await asyncio.to_thread(_renew_gmail_watch,mb) or mb
            except Exception:logger.exception('Gmail watch registration failed mailbox_id=%s; polling fallback remains active',mb['id'])
        end=date.today();start=end-timedelta(days=29);job=store.enqueue_historical_rescan(mb['id'],requested_by=actor,range_start=start,range_end=end)
        store.audit(actor=actor,role='admin',action='POST_CONNECT_HISTORICAL_RESCAN_QUEUED',candidate_id=data['candidate_id'],source_id=job['id'],new={'range_start':start.isoformat(),'range_end':end.isoformat(),'prompt_version':'v3'})
        return RedirectResponse('/?view=ai-recruitment&mailbox=connected')
    def _resolve_mailbox(candidate_id:str,body:dict|None=None)->dict|None:
        """Pick a specific mailbox by mailbox_id from body, else fall back to best."""
        mid=str((body or {}).get('mailbox_id') or '').strip()
        if mid:
            mb=store.mailbox_by_id(mid)
            return mb if mb and mb.get('candidate_id') in _identity_ids(candidate_id) else None
        return _identity_mailbox(candidate_id)
    @app.post('/api/candidates/{candidate_id}/mailbox/verify')
    async def verify(candidate_id:str,request:Request):
        _guard();require_fleet_admin(request);body=await request.json() if request.headers.get('content-length','0')!='0' else {}
        mb=_resolve_mailbox(candidate_id,body)
        if not mb or not mb.get('credential_ciphertext'):raise HTTPException(400,'Mailbox is not connected')
        gmail_profile=await __import__('asyncio').to_thread(GmailMailboxProvider(mb['credential_ciphertext']).verify_connection);store.update_mailbox(mb['id'],{'connection_status':'CONNECTED','last_error_message':None});profile=operator_profile(request);store.audit(actor=profile.get('username') or 'admin',role='admin',action='MAILBOX_VERIFIED',candidate_id=candidate_id,source_id=mb['id']);return {'status':'ok','email_address':gmail_profile.get('emailAddress')}
    @app.post('/api/candidates/{candidate_id}/mailbox/sync')
    async def sync(candidate_id:str,request:Request):
        _guard();profile=require_fleet_admin(request);body=await request.json() if request.headers.get('content-length','0')!='0' else {}
        mb=_resolve_mailbox(candidate_id,body)
        if not mb:raise HTTPException(404,'Mailbox not configured')
        job=store.enqueue_sync(mb['id'],requested_by=profile.get('username') or 'admin');store.audit(actor=profile.get('username') or 'admin',role='admin',action='MANUAL_SYNC_REQUESTED',candidate_id=candidate_id,source_id=job['id'],source_ip=request.client.host if request.client else None);return {'status':'ok','job':job}
    @app.post('/api/candidates/{candidate_id}/mailbox/rescan')
    async def historical_rescan(candidate_id:str,request:Request,body:dict):
        _guard();profile=require_fleet_admin(request);mb=_resolve_mailbox(candidate_id,body)
        if not mb or not mb.get('credential_ciphertext'):raise HTTPException(400,'Mailbox is not connected')
        try:
            end=date.fromisoformat(str(body.get('range_end') or date.today().isoformat()))
            start=date.fromisoformat(str(body.get('range_start') or (end-timedelta(days=29)).isoformat()))
        except ValueError:raise HTTPException(400,'Dates must use YYYY-MM-DD')
        if start>end or (end-start).days>365:raise HTTPException(400,'Choose a valid range of up to 365 days')
        actor=profile.get('username') or 'admin';job=store.enqueue_historical_rescan(mb['id'],requested_by=actor,range_start=start,range_end=end)
        store.audit(actor=actor,role='admin',action='HISTORICAL_EMAIL_RESCAN_REQUESTED',candidate_id=candidate_id,source_id=job['id'],new={'range_start':start.isoformat(),'range_end':end.isoformat(),'prompt_version':'v3'},source_ip=request.client.host if request.client else None)
        return {'status':'ok','job':job}
    @app.patch('/api/candidates/{candidate_id}/mailbox/settings')
    async def settings(candidate_id:str,request:Request,body:dict):
        _guard();profile=require_fleet_admin(request)
        mb=_resolve_mailbox(candidate_id,body)
        if not mb:raise HTTPException(404,'Mailbox not configured')
        value=bool(body.get('monitoring_enabled'));updated=store.update_mailbox(mb['id'],{'monitoring_enabled':value})
        if value and os.getenv('GMAIL_PUBSUB_TOPIC'):
            try:updated=await asyncio.to_thread(_renew_gmail_watch,{**mb,**updated}) or updated
            except Exception:logger.exception('Gmail watch renewal failed mailbox_id=%s; polling fallback remains active',mb['id'])
        updated.pop('credential_ciphertext',None);store.audit(actor=profile.get('username') or 'admin',role='admin',action='MONITORING_ENABLED' if value else 'MONITORING_DISABLED',candidate_id=candidate_id,source_id=mb['id'],previous={'monitoring_enabled':mb.get('monitoring_enabled')},new={'monitoring_enabled':value});return {'status':'ok','mailbox':updated}
    @app.post('/api/candidates/{candidate_id}/mailbox/renew-watch')
    async def renew_watch(candidate_id:str,request:Request):
        _guard();profile=require_fleet_admin(request);body=await request.json() if request.headers.get('content-length','0')!='0' else {}
        mb=_resolve_mailbox(candidate_id,body)
        if not mb or not mb.get('credential_ciphertext'):raise HTTPException(400,'Mailbox is not connected')
        if not os.getenv('GMAIL_PUBSUB_TOPIC'):raise HTTPException(503,'GMAIL_PUBSUB_TOPIC is not configured')
        try:updated=await asyncio.to_thread(_renew_gmail_watch,mb)
        except Exception as exc:raise HTTPException(502,'Gmail watch renewal failed') from exc
        store.audit(actor=profile.get('username') or 'admin',role='admin',action='GMAIL_WATCH_RENEWED',candidate_id=candidate_id,source_id=mb['id'])
        safe={k:v for k,v in (updated or {}).items() if k!='credential_ciphertext'}
        return {'status':'ok','mailbox':safe}
    @app.delete('/api/candidates/{candidate_id}/mailbox')
    async def disconnect(candidate_id:str,request:Request):
        _guard();profile=require_fleet_admin(request)
        # Support mailbox_id in query param for multi-mailbox DELETE
        mailbox_id=request.query_params.get('mailbox_id','').strip()
        if mailbox_id:
            mb=store.mailbox_by_id(mailbox_id)
            if not mb or mb.get('candidate_id') not in _identity_ids(candidate_id):return {'status':'ok'}
        else:
            mb=_identity_mailbox(candidate_id)
        if not mb:return {'status':'ok'}
        store.update_mailbox(mb['id'],{'credential_ciphertext':None,'monitoring_enabled':False,'connection_status':'DISCONNECTED','sync_cursor':None,'provider_history_id':None});store.audit(actor=profile.get('username') or 'admin',role='admin',action='MAILBOX_DISCONNECTED',candidate_id=candidate_id,source_id=mb['id']);return {'status':'ok'}
    @app.post('/api/candidate-mailboxes/bulk-import')
    async def bulk_import(request:Request,body:dict):
        _guard();require_fleet_admin(request);created=[]
        for row in (body.get('mailboxes') or [])[:500]:
            cid=str(row.get('candidate_id') or '');email=str(row.get('email_address') or '').strip().lower()
            if candidate_store.get_candidate(cid) and '@' in email:created.append(store.upsert_mailbox(candidate_store.canonical_candidate_identity_id(cid),email,monitoring_enabled=False,connection_status='PENDING'))
        profile=operator_profile(request);store.audit(actor=profile.get('username') or 'admin',role='admin',action='BULK_MAILBOX_IMPORT_COMPLETED',new={'count':len(created)});return {'status':'ok','count':len(created)}
    @app.get('/api/candidates/{candidate_id}/recruitment-timeline')
    async def timeline(candidate_id:str,request:Request):
        _guard();row=candidate_store.get_candidate(candidate_id)
        if not row:raise HTTPException(404,'Candidate not found')
        assert_candidate_row_access(request,row);return {'status':'ok','events':_identity_events(candidate_id,limit=100)}
    @app.get('/api/candidates/{candidate_id}/timeline')
    async def unified_timeline(candidate_id:str,request:Request):
        """Bookings, moves, attendance, payments and mail for one person, newest first."""
        _guard();row=candidate_store.get_candidate(candidate_id)
        if not row:raise HTTPException(404,'Candidate not found')
        assert_candidate_row_access(request,row)
        from services.candidate_timeline import candidate_timeline
        entries=await asyncio.to_thread(
            candidate_timeline,candidate_id,
            alerts_for=lambda cid:store.list_notifications(filters={'candidate_id':cid},limit=200)[0],
            events_for=lambda cid:store.list_events(candidate_id=cid,limit=100),
        )
        return {'status':'ok','entries':entries}
    @app.get('/api/candidates/{candidate_id}/recruitment-events')
    async def recruitment_events(candidate_id:str,request:Request,limit:int=50,offset:int=0):
        _guard();row=candidate_store.get_candidate(candidate_id)
        if not row:raise HTTPException(404,'Candidate not found')
        assert_candidate_row_access(request,row);return {'status':'ok','events':_identity_events(candidate_id,limit=min(max(limit,1),100),offset=max(offset,0))}
    @app.get('/api/ai-recruitment/review')
    async def review_list(request:Request,response:Response,status:str|None=None,limit:int=50,offset:int=0):
        _guard();require_fleet_admin(request)
        response.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma']='no-cache'
        response.headers['Expires']='0'
        response.headers['X-Offer-Review-Version']='offer_review_cleanup_v1'
        return {'status':'ok','events':store.list_events(review_status=status,limit=min(limit,100),offset=max(offset,0)),'_timestamp':int(time.time())}
    @app.get('/api/ai-recruitment/events/{event_id}')
    async def event_get(event_id:str,request:Request):
        _guard();require_fleet_admin(request);row=store.event_detail(event_id,include_evidence=True)
        if not row:raise HTTPException(404,'Event not found')
        return {'status':'ok','event':row}
    @app.patch('/api/ai-recruitment/events/{event_id}')
    async def event_edit(event_id:str,request:Request,body:dict):
        _guard();require_fleet_admin(request)
        raise HTTPException(410,'Recruitment mail events are managed automatically; manual edits are unavailable')
    @app.post('/api/ai-recruitment/events/{event_id}/{action}')
    async def review_action(event_id:str,action:str,request:Request,body:dict):
        _guard();require_fleet_admin(request)
        # Reprocessing is leased and backoff-controlled by the worker.  A
        # user-triggered decision could race the lifecycle guard, so every
        # legacy review action is deliberately retired rather than ignored.
        raise HTTPException(410,'Recruitment mail is fully automated; manual review actions are unavailable')
    @app.get('/api/ai-recruitment/dashboard')
    async def dashboard(request:Request,response:Response):
        _guard();require_fleet_admin(request)
        from core.db.connection import get_connection
        from core.recruitment_offer_visibility import qualified_event_sql
        truth=await asyncio.to_thread(store.selection_tracking_stats)
        predicate,params=qualified_event_sql('e')
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute(f"""SELECT e.created_at::date AS event_day,count(*) AS event_count FROM ai_recruitment_events e
              WHERE e.created_at>=current_date-30 AND e.validation_status IN('AUTO_VALIDATED','APPROVED')
                AND {predicate} GROUP BY 1 ORDER BY 1""",params);events_by_day=[{'day':str(r[0]),'count':r[1]} for r in cur.fetchall()]
            cur.execute(f"""SELECT e.primary_status,count(*) count FROM ai_recruitment_events e
              WHERE e.validation_status IN('AUTO_VALIDATED','APPROVED') AND {predicate}
              GROUP BY 1 ORDER BY 2 DESC""",params);status_distribution=[{'status':r[0],'count':r[1]} for r in cur.fetchall()]
        response.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma']='no-cache'
        response.headers['Expires']='0'
        response.headers['X-Offer-Review-Version']='offer_review_cleanup_v1'
        return {'status':'ok','metrics':truth['metrics'],'filters':truth['filters'],'charts':{'events_by_day':events_by_day,'status_distribution':status_distribution},'flags':store.list_flags(status='PENDING'),'_timestamp':int(time.time())}
    @app.get('/api/selection-tracking/stats')
    async def selection_tracking_stats(request:Request,response:Response):
        _guard();require_fleet_admin(request);response.headers['Cache-Control']='no-store, max-age=0'
        return {'status':'ok',**(await asyncio.to_thread(store.selection_tracking_stats)),'_timestamp':int(time.time())}
    @app.get('/api/offer-verification')
    async def offer_list(request:Request,status:str|None=None,limit:int=50,offset:int=0):
        _guard();require_fleet_admin(request)
        if not offer_enabled():return {'status':'ok','cases':[],'enabled':False}
        return {'status':'ok','cases':store.list_offer_cases(status=status,limit=min(limit,100),offset=max(offset,0))}
    @app.get('/api/offer-verification/{case_id}')
    async def offer_get(case_id:str,request:Request):
        _guard();require_fleet_admin(request)
        if not offer_enabled():raise HTTPException(404,'AI offer verification is disabled')
        rows=[r for r in store.list_offer_cases(limit=1000) if r['id']==case_id]
        if not rows:raise HTTPException(404,'Offer case not found')
        return {'status':'ok','case':rows[0]}
    @app.post('/api/offer-verification/{case_id}/{action}')
    async def offer_review(case_id:str,action:str,request:Request,body:dict):
        _guard();require_fleet_admin(request)
        raise HTTPException(410,'Offer verification is managed by automated mail processing; manual actions are unavailable')
    @app.get('/api/ai-recruitment/metrics')
    async def recruitment_metrics(request:Request):
        _guard();require_fleet_admin(request)
        from core.db.connection import get_connection
        from core.recruitment_offer_visibility import qualified_event_sql
        predicate,params=qualified_event_sql('e')
        with get_connection() as conn,conn.cursor() as cur:
            cur.execute(f"""SELECT
              (SELECT count(*) FROM mailbox_sync_jobs) mailbox_sync_total,
              (SELECT count(*) FROM mailbox_sync_jobs WHERE status='COMPLETED') mailbox_sync_success_total,
              (SELECT count(*) FROM mailbox_sync_jobs WHERE status IN('FAILED','DEAD_LETTER')) mailbox_sync_failure_total,
              (SELECT COALESCE(sum(messages_fetched),0) FROM mailbox_sync_jobs) mailbox_messages_fetched_total,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate}) ai_selection_offer_request_total,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate} AND e.confidence>=0.9) ai_selection_offer_high_confidence_total,
              (SELECT count(*) FROM mailbox_messages WHERE processing_status='AI_RETRY_PENDING') ai_retry_pending_total,
              (SELECT count(*) FROM mailbox_messages WHERE processing_status='AI_PROCESSING_FAILED') ai_processing_failure_total,
              (SELECT COALESCE(avg(processing_duration_ms),0) FROM ai_recruitment_events) ai_processing_duration_ms_avg,
              (SELECT count(*) FROM mailbox_sync_jobs WHERE status='QUEUED') queue_depth,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate} AND e.primary_status IN('SELECTED','FINAL_SELECTION_CONFIRMED')) selection_detected_total,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate} AND e.primary_status='OFFER_INDICATION') offer_indication_total,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate} AND e.primary_status IN('OFFER_IN_PROGRESS','OFFER_APPROVED')) offer_in_progress_total,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate} AND e.primary_status='OFFER_LETTER_RECEIVED') offer_letter_detected_total,
              (SELECT count(*) FROM ai_recruitment_events e WHERE {predicate} AND e.primary_status='APPOINTMENT_LETTER_RECEIVED') appointment_letter_detected_total,
              (SELECT count(*) FROM offer_verification_cases c JOIN ai_recruitment_events e ON e.id=c.ai_recruitment_event_id WHERE c.verification_status='VERIFIED' AND {predicate}) verified_offer_total,
              (SELECT count(*) FROM ai_recruitment_events WHERE review_status IN('FALSE_POSITIVE','IGNORED')) false_positive_total,
              (SELECT count(*) FROM mail_monitoring_notifications) notification_created_total,
              (SELECT count(*) FROM mail_realtime_events) notification_delivery_event_total,
              (SELECT count(*) FROM mailbox_messages WHERE processing_status LIKE 'DUPLICATE%%') duplicate_messages_prevented_total""",params*8);names=[d.name for d in cur.description];values=dict(zip(names,cur.fetchone()))
        from core.recruitment_realtime import connection_count
        values['websocket_connection_count']=connection_count()
        return {'status':'ok','metrics':values}

    @app.get('/api/mail-monitoring/notifications')
    async def mail_notifications(
        request:Request, response:Response, search:str|None=None,
        candidate_id:str|None=None, company:str|None=None,
        classification:str|None=None, classification_group:str|None=None,
        candidate_status:str|None=None,
        priority:str|None=None, is_read:bool|None=None, is_reviewed:bool|None=None,
        confidence_min:float|None=None, confidence_max:float|None=None,
        date_from:date|None=None, date_to:date|None=None, sort:str='newest',
        group_by:str|None=None, booking_result:str|None=None,
        limit:int=50, offset:int=0,
    ):
        _guard();require_fleet_admin(request)
        filters={
            'search':search,'candidate_id':candidate_id,'company':company,
            'classification':classification,'classification_group':classification_group,
            'candidate_status':candidate_status,
            'priority':priority,'is_read':is_read,'is_reviewed':is_reviewed,
            'confidence_min':confidence_min,'confidence_max':confidence_max,
            'date_from':date_from,'date_to':date_to,'sort':sort,
            'booking_result':booking_result,
        }
        # group_by=candidate makes the page unit the candidate, so the grouped
        # Selection view can show every mail a candidate has without one of
        # them falling onto the next page. Any other value is ignored rather
        # than rejected: an unknown grouping should degrade to the flat list,
        # not empty the screen.
        grouped=str(group_by or '').strip().lower()=='candidate'
        rows,total=await asyncio.to_thread(store.list_notifications,filters=filters,limit=limit,offset=offset,group_by_candidate=grouped)
        response.headers['Cache-Control']='no-store, max-age=0'
        return {'status':'ok','notifications':rows,'total':total,'grouped_by':'candidate' if grouped else None,'limit':min(max(limit,1),100),'offset':max(offset,0)}

    @app.get('/api/mail-monitoring/candidates')
    async def mail_notification_candidates(request:Request,response:Response):
        # Powers the Mail Alerts candidate filter. Read-only, and scoped to
        # candidates that actually have visible alerts so every option returns
        # rows when picked.
        _guard();require_fleet_admin(request);response.headers['Cache-Control']='no-store, max-age=0'
        return {'status':'ok','candidates':await asyncio.to_thread(store.list_notification_candidates)}

    @app.get('/api/mail-monitoring/summary')
    async def mail_notification_summary(request:Request,response:Response):
        _guard();require_fleet_admin(request);response.headers['Cache-Control']='no-store, max-age=0'
        return {'status':'ok','summary':await asyncio.to_thread(store.notification_summary)}

    @app.get('/api/mail-monitoring/events')
    async def mail_missed_events(request:Request,response:Response,after_event_id:str|None=None,limit:int=100):
        _guard();require_fleet_admin(request);response.headers['Cache-Control']='no-store, max-age=0'
        events=await asyncio.to_thread(store.list_realtime_events,after_id=after_event_id,limit=limit)
        return {'status':'ok','events':events}

    @app.get('/api/mail-monitoring/booking-audit')
    async def booking_audit(request:Request,candidate_id:str|None=None,booking_id:str|None=None,limit:int=100):
        _guard();require_fleet_admin(request)
        return {'status':'ok','audit':await asyncio.to_thread(store.list_booking_audit,candidate_id=candidate_id,booking_id=booking_id,limit=limit)}

    @app.get('/api/mail-monitoring/interview-reconciliation')
    async def interview_reconciliation_report(request:Request,candidate_id:str|None=None,limit:int=500):
        """Read-only consistency report; it never retries or repairs bookings."""
        _guard();require_fleet_admin(request)
        from services.interview_state_reconciliation import load_current_report
        return {'status':'ok','report':await asyncio.to_thread(load_current_report,candidate_id=candidate_id,limit=limit)}

    @app.get('/api/mail-monitoring/calendar-recovery-discovery')
    async def calendar_recovery_discovery(request:Request,limit:int=500):
        """Read-only inventory; it cannot enqueue or reprocess historical mail."""
        _guard();require_fleet_admin(request)
        return {'status':'ok','report':await asyncio.to_thread(
            store.calendar_invite_recovery_discovery, limit=limit,
        )}

    @app.post('/api/mail-monitoring/notifications/clear-all')
    async def clear_all_mail_notifications(request:Request):
        _guard();profile=require_fleet_admin(request)
        reviewer=profile.get('username') or 'admin'
        cleared=await asyncio.to_thread(store.clear_notifications,reviewer=reviewer)
        await asyncio.to_thread(store.audit,actor=reviewer,role='admin',action='MAIL_NOTIFICATIONS_CLEARED',source_id='mail-monitoring',new={'cleared_count':cleared})
        return {'status':'ok','cleared_count':cleared}

    @app.post('/api/mail-monitoring/notifications/{notification_id}/{action}')
    async def mail_notification_action(notification_id:str,action:str,request:Request,body:dict|None=None):
        _guard();profile=require_fleet_admin(request);body=body or {}
        if action in {'reviewed','false-detection','correct','rerun'}:
            raise HTTPException(410,'Mail decisions and retries are automated; operator decision actions are unavailable')
        if action not in {'read','unread','dismiss'}:
            raise HTTPException(404,'Unknown notification action')
        try:
            row=await asyncio.to_thread(store.update_notification,notification_id,action,
                reviewer=profile.get('username') or 'admin',notes=str(body.get('notes') or '')[:2000],changes=body.get('changes') or {})
        except ValueError as exc:
            raise HTTPException(400,str(exc))
        if not row:raise HTTPException(404,'Notification not found')
        store.audit(actor=profile.get('username') or 'admin',role='admin',action='MAIL_NOTIFICATION_'+action.upper().replace('-','_'),candidate_id=row.get('candidate_id'),source_id=row.get('id'),new={'notes':str(body.get('notes') or '')[:2000],'changes':body.get('changes') or {}})
        return {'status':'ok','notification':row}

    @app.websocket('/ws/mail-monitoring')
    async def mail_monitoring_websocket(websocket:WebSocket):
        from core import dashboard_auth_vps as dash_auth
        from core import recruitment_realtime
        profile=dash_auth.operator_profile_from_cookies(dict(websocket.cookies))
        if not enabled() or not dash_auth.is_admin_profile(profile):
            await websocket.close(code=4403,reason='Authentication required')
            return
        recruitment_realtime.configure_loop(asyncio.get_running_loop())
        await recruitment_realtime.connect(websocket,profile)
        last_id=(websocket.query_params.get('last_event_id') or '').strip() or None
        try:
            await websocket.send_json({'event':'connected','event_id':last_id,'connection_status':'Live','summary':await asyncio.to_thread(store.notification_summary)})
            if last_id:
                missed=await asyncio.to_thread(store.list_realtime_events,after_id=last_id,limit=200)
                for item in missed:
                    await websocket.send_json({'event':item['event_type'],'event_id':item['id'],**(item.get('payload') or {}),'created_at':str(item.get('created_at') or ''),'delivery_source':'replay'})
            while True:
                current_profile=dash_auth.operator_profile_from_cookies(dict(websocket.cookies))
                if not dash_auth.is_admin_profile(current_profile):
                    await websocket.close(code=4401,reason='Session expired')
                    break
                try:
                    raw=await asyncio.wait_for(websocket.receive_text(),timeout=30)
                    message=json.loads(raw) if raw else {}
                    if message.get('type')=='ping':
                        await websocket.send_json({'event':'pong','at':int(time.time())})
                except asyncio.TimeoutError:
                    await websocket.send_json({'event':'ping','at':int(time.time())})
                except json.JSONDecodeError:
                    continue
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            await recruitment_realtime.disconnect(websocket)
