import base64
import re
from cryptography.fernet import Fernet
from services.gmail_mailbox_provider import GmailMailboxProvider,decode_gmail_message,encrypt_credentials,decrypt_credentials

def test_credentials_are_encrypted(monkeypatch):
    monkeypatch.setenv('MAILBOX_CREDENTIAL_ENCRYPTION_KEY',Fernet.generate_key().decode())
    fake_value='unit'+'-test-value'
    cipher=encrypt_credentials({'refresh_token':fake_value})
    assert fake_value not in cipher
    assert decrypt_credentials(cipher)['refresh_token']==fake_value

def test_decode_gmail_message():
    sender='jobs'+'@'+'test.invalid';recipient='candidate'+'@'+'test.invalid'
    body=base64.urlsafe_b64encode(b'Your interview is scheduled.').decode().rstrip('=')
    raw={'id':'m1','threadId':'t1','payload':{'headers':[{'name':'From','value':f'Recruiter <{sender}>'},{'name':'Subject','value':'Interview scheduled'},{'name':'Date','value':'Sun, 12 Jul 2026 13:00:00 +0530'}],'mimeType':'text/plain','body':{'data':body}}}
    row=decode_gmail_message(raw,recipient)
    assert row['provider_message_id']=='m1'
    assert row['sender_email']==sender
    assert 'interview is scheduled' in row['body']


def test_decode_distinguishes_inbox_from_candidate_sent_reply():
    mailbox='candidate@test.invalid'
    sent={'id':'sent1','labelIds':['SENT'],'payload':{'headers':[
        {'name':'From','value':mailbox},{'name':'To','value':'recruiter@company.test'}
    ],'mimeType':'text/plain','body':{}}}
    incoming={'id':'in1','labelIds':['INBOX'],'payload':{'headers':[
        {'name':'From','value':'recruiter@company.test'},{'name':'To','value':mailbox}
    ],'mimeType':'text/plain','body':{}}}
    assert decode_gmail_message(sent,mailbox)['message_direction']=='OUTBOUND'
    decoded=decode_gmail_message(incoming,mailbox)
    assert decoded['message_direction']=='INBOUND'
    assert decoded['to_metadata']==[mailbox]

def test_decode_uses_html_when_plain_text_is_missing():
    encoded=base64.urlsafe_b64encode(b'<p>Interview <strong>confirmed</strong></p>').decode().rstrip('=')
    raw={'id':'m2','payload':{'headers':[{'name':'From','value':'Recruiter'},{'name':'Cc','value':'first@test.invalid, second@test.invalid'}],'mimeType':'text/html','body':{'data':encoded}}}
    row=decode_gmail_message(raw,'candidate'+'@'+'test.invalid')
    assert 'Interview' in row['body']
    assert len(row['cc_metadata'])==2


def test_decode_uses_complete_html_current_reply_when_plain_alternative_is_incomplete():
    """Exact Zealogics false-negative: plain had only the quoted candidate
    reply while HTML carried Suresh's new L1-interview instruction."""
    recipient='badrithiru73'+'@'+'example.com'
    plain=(
        'EXTERNAL EMAIL: DO NOT CLICK links or attachments unless you recognize the sender.\n'
        'Sent: Tuesday, September 1, 2026 12:53 PM\n'
        'To: Suresh Vishwambharan\n'
        'Subject: Re: New Job Opportunity Azure Engineer - Remote\n'
        'Yes I am interested in the following position looking forward for the interview'
    )
    html_body=(
        '<style>P {margin-top:0;margin-bottom:0;}</style>'
        '<p>EXTERNAL EMAIL: DO NOT CLICK links or attachments unless you recognize the sender.</p>'
        '<p>Hi Naveen,</p>'
        '<p>Your profile got shortlisted for L1 interview,</p>'
        '<p>Please find below AI test link 30min interview duration. Please complete it ASAP</p>'
        '<p><a href="https://example.test/interview">AI interview link</a></p>'
        '<p>Thanks,<br>Suresh</p>'
        '<div id="divRplyFwdMsg">From: naveen thiru &lt;naveen.prakash@example.com&gt;<br>'
        'Yes I am interested in the following position looking forward for the interview</div>'
    )
    encode=lambda value:base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')
    raw={'id':'1a05bdd0c0c30ac2','threadId':'1a05bdd0c0c30ac2','labelIds':['INBOX'],'payload':{
        'headers':[
            {'name':'From','value':'Suresh Vishwambharan <suresh@zealogics.com>'},
            {'name':'To','value':recipient},
            {'name':'Subject','value':'Re: New Job Opportunity Azure Engineer - Remote'},
            {'name':'Date','value':'Tue, 1 Sep 2026 12:56:43 +0530'},
        ],
        'mimeType':'multipart/alternative','parts':[
            {'mimeType':'text/plain','body':{'data':encode(plain)}},
            {'mimeType':'text/html','body':{'data':encode(html_body)}},
        ],
    }}

    row=decode_gmail_message(raw,recipient)

    assert row['provider_message_id']=='1a05bdd0c0c30ac2'
    assert 'Your profile got shortlisted for L1 interview' in row['body']
    assert '30min interview duration' in row['body']
    assert 'Please complete it ASAP' in row['body']
    assert 'P {margin-top' not in row['body']
    assert 'Yes I am interested' not in row['body']

    # Old rows already passed through clean_email, so their forwarded headers
    # are collapsed onto one line. A historical rescan must repair those rows
    # from the stored HTML without fetching Gmail again.
    from services.gmail_mailbox_provider import select_message_body
    repaired=select_message_body(re.sub(r'\s+',' ',plain),html_body)
    assert 'Your profile got shortlisted for L1 interview' in repaired
    assert 'Yes I am interested' not in repaired

    from services.recruitment_mail_agent import routing_decision
    route=routing_decision(row['subject'],row['body'],row['sender_name'],row['sender_email'])
    assert route['send_to_ai'] is True
    assert route['context']['status']=='INTERVIEW_SHORTLISTED'

    from services.recruitment_semantics import (
        classify_context, evidence_entails_transition, validate_lifecycle_event,
    )
    context=classify_context(row['subject'],row['body'],sender_email=row['sender_email'])
    assert context['lifecycle_event']=='INTERVIEW_SHORTLISTED'
    assert evidence_entails_transition('INTERVIEW_SHORTLISTED',row['body']) is True
    assert validate_lifecycle_event('INTERVIEW_SHORTLISTED',context)==('INTERVIEW_SHORTLISTED',None)


def test_history_sync_drains_every_page_before_returning_cursor():
    provider=GmailMailboxProvider.__new__(GmailMailboxProvider)
    provider._status='CONNECTED';calls=[]
    def request(path,**_kwargs):
        calls.append(path)
        if 'pageToken=page-2' in path:
            return {'historyId':'history-99','history':[{'messagesAdded':[{'message':{'id':'m21'}},{'message':{'id':'m22'}}]}]}
        return {'historyId':'history-99','history':[{'messagesAdded':[{'message':{'id':f'm{i}'}} for i in range(1,21)]}],'nextPageToken':'page-2'}
    provider._request=request
    refs,cursor=provider.fetch_new_messages('history-1',batch_size=20)
    assert len(refs)==22
    assert refs[-1]['id']=='m22'
    assert cursor=='history-99'
    assert len(calls)==2


def test_expired_cursor_reconciliation_drains_recent_message_pages(monkeypatch):
    import urllib.error
    provider=GmailMailboxProvider.__new__(GmailMailboxProvider)
    provider._status='CONNECTED';provider.credentials={};calls=[]
    def request(path,**_kwargs):
        calls.append(path)
        if path.startswith('history?'):
            raise urllib.error.HTTPError('gmail',404,'expired',{},None)
        if 'pageToken=next' in path:return {'messages':[{'id':'m2'}]}
        if path.startswith('messages?'):return {'messages':[{'id':'m1'}],'nextPageToken':'next'}
        if path=='profile':return {'historyId':'fresh'}
        raise AssertionError(path)
    provider._request=request
    provider.verify_connection=lambda:{'historyId':'fresh'}
    monkeypatch.setenv('AI_MAIL_RECONCILIATION_MAX_MESSAGES','10')
    refs,cursor=provider.fetch_new_messages('expired',batch_size=1)
    assert refs==[{'id':'m1'},{'id':'m2'}]
    assert cursor=='fresh'
