"""Google Gmail read-only provider using OAuth2 REST APIs."""
from __future__ import annotations
import base64, html, json, os, re, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from services.mailbox_provider import MailboxProvider

GMAIL_SCOPE="https://www.googleapis.com/auth/gmail.readonly"

_REPLY_BOUNDARY = re.compile(
    r"(?im)^\s*(?:"
    r"on .{1,500} wrote:|"
    r"from:\s+|sent:\s+|"
    r"-{2,}\s*(?:original|forwarded) message\s*-{2,}"
    r")"
)
_COLLAPSED_REPLY_HEADER = re.compile(
    r"(?is)\b(?:"
    r"from:\s.{0,350}?\bsent:\s.{0,350}?\bto:\s.{0,350}?\bsubject:\s|"
    r"sent:\s.{0,350}?\bto:\s.{0,350}?\bsubject:\s"
    r")"
)
_QUOTED_HTML_MARKERS = (
    "divrplyfwdmsg", "gmail_quote", "gmail_attr", "yahoo_quoted",
    "protonmail_quote", "moz-cite-prefix",
)
_HTML_BREAK_TAGS = frozenset({
    "br", "div", "p", "li", "tr", "table", "section", "article",
    "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
})


class _CurrentHtmlTextParser(HTMLParser):
    """Render visible current-message text and stop at known reply containers.

    Multipart/alternative is supposed to carry equivalent plain and HTML
    representations. In practice Outlook/Gmail gateways sometimes put only a
    quoted reply in text/plain while the new message exists solely in the HTML
    alternative. Preserving the reply-container structure lets us compare the
    two current-message representations without lifecycle keywords.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.reached_quote = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.reached_quote:
            return
        values = " ".join(str(value or "").casefold() for key, value in attrs if key in {"id", "class"})
        if any(marker in values for marker in _QUOTED_HTML_MARKERS):
            self.reached_quote = True
            return
        if tag in {"head", "style", "script", "noscript", "svg"}:
            self.ignored_depth += 1
            return
        if not self.ignored_depth and tag in _HTML_BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.reached_quote:
            return
        if tag in {"head", "style", "script", "noscript", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if not self.ignored_depth and tag in _HTML_BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.reached_quote and not self.ignored_depth:
            self.parts.append(data)


def _current_text(value: str, *, is_html: bool = False) -> str:
    if not value:
        return ""
    if is_html:
        parser = _CurrentHtmlTextParser()
        try:
            parser.feed(value)
            parser.close()
            value = "".join(parser.parts)
        except Exception:
            value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    boundaries = [match for match in (
        _REPLY_BOUNDARY.search(value), _COLLAPSED_REPLY_HEADER.search(value),
    ) if match]
    if boundaries:
        value = value[:min(match.start() for match in boundaries)]
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _content_score(value: str) -> tuple[int, int]:
    words = re.findall(r"[a-z0-9][a-z0-9@._:/+-]*", value.casefold())
    return len(set(words)), len(value)


def select_message_body(plain: str | None, html_body: str | None) -> str:
    """Choose the more complete current-message MIME alternative.

    This is deliberately structural: it compares visible current content and
    never looks for recruitment terms. Equal alternatives keep text/plain;
    materially richer HTML wins, which recovers current replies omitted from a
    malformed/incomplete plain part without appending quoted history.
    """
    plain_current = _current_text(str(plain or ""))
    html_current = _current_text(str(html_body or ""), is_html=True)
    if not html_current:
        return plain_current
    if not plain_current:
        return html_current
    return html_current if _content_score(html_current) > _content_score(plain_current) else plain_current

def _secret_key() -> bytes:
    raw=(os.getenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY") or "").encode()
    if not raw: raise RuntimeError("MAILBOX_CREDENTIAL_ENCRYPTION_KEY is not configured")
    return raw

def encrypt_credentials(value: dict[str,Any]) -> str:
    from cryptography.fernet import Fernet
    return Fernet(_secret_key()).encrypt(json.dumps(value).encode()).decode()

def decrypt_credentials(value: str) -> dict[str,Any]:
    from cryptography.fernet import Fernet
    return json.loads(Fernet(_secret_key()).decrypt(value.encode()).decode())

def authorization_url(state: str, redirect_uri: str, login_hint: str = "") -> str:
    params={"client_id":os.environ["GOOGLE_OAUTH_CLIENT_ID"],"redirect_uri":redirect_uri,"response_type":"code","scope":GMAIL_SCOPE,"access_type":"offline","prompt":"consent","state":state,"include_granted_scopes":"false"}
    # A hint only preselects which Google account to sign in to. Consent is still
    # Google's own screen, and the callback still rejects any other account.
    if login_hint: params["login_hint"]=login_hint
    return "https://accounts.google.com/o/oauth2/v2/auth?"+urllib.parse.urlencode(params)

def exchange_code(code: str, redirect_uri: str) -> dict[str,Any]:
    data=urllib.parse.urlencode({"code":code,"client_id":os.environ["GOOGLE_OAUTH_CLIENT_ID"],"client_secret":os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],"redirect_uri":redirect_uri,"grant_type":"authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token",data=data,method="POST"),timeout=20) as r:return json.loads(r.read())

class GmailAuthorizationExpired(RuntimeError):
    """Google will not issue a new access token, so a human must reconnect.

    Raised only for the two cases re-authorisation actually fixes: Google
    answering ``invalid_grant`` (the grant is revoked or dead), and no refresh
    token being stored at all.

    Every other failure -- an HTTP 5xx, a timeout, a rate limit, a bug of ours
    -- is a different kind of problem and must not be reported as an expiry.
    Production had 28 HTTPError and 2 AttributeError sync failures recorded
    against 270 genuine expiries, and each one of those 30 put "Gmail
    connection expired. Reconnect to continue monitoring." on a mailbox whose
    credentials were entirely valid.
    """


class GmailMailboxProvider(MailboxProvider):
    def __init__(self, credential_ciphertext: str, on_credentials_refreshed=None):
        self.credentials=decrypt_credentials(credential_ciphertext); self._status="CONNECTED"; self._cursor=None
        # Called with the full credential dict whenever Google issues a new
        # access token, so the caller can store it. Without this the refreshed
        # token lives only in this instance and is thrown away with it: every
        # later sync starts from the stale stored token, spends a 401 and a
        # refresh round trip to get going, and each of those extra calls is
        # another chance to fail transiently.
        self._on_credentials_refreshed=on_credentials_refreshed
    def connect(self)->None:self.verify_connection()
    def disconnect(self)->None:self.credentials={};self._status="DISCONNECTED"
    def get_sync_cursor(self)->str|None:return self._cursor
    def save_sync_cursor(self,cursor:str|None)->None:self._cursor=cursor
    def refresh_connection(self)->None:
        refresh=self.credentials.get("refresh_token")
        if not refresh: raise GmailAuthorizationExpired("Gmail authorization has expired; reconnect the mailbox")
        data=urllib.parse.urlencode({"client_id":os.environ["GOOGLE_OAUTH_CLIENT_ID"],"client_secret":os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],"refresh_token":refresh,"grant_type":"refresh_token"}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token",data=data,method="POST"),timeout=20) as r:refreshed=json.loads(r.read())
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode("utf-8","replace")
            if exc.code==400 and "invalid_grant" in detail:
                raise GmailAuthorizationExpired("Gmail authorization expired or was revoked. Reconnect Gmail to resume automatic monitoring and historical rescans.") from exc
            # Anything else -- 429, 500, 503 -- is Google being briefly
            # unavailable, not the grant being dead. Let it propagate as the
            # transient failure it is.
            raise
        # Google omits refresh_token when reissuing, so update rather than
        # replace: overwriting here would drop the only credential that can
        # ever recover this mailbox without a human.
        self.credentials.update(refreshed)
        if self._on_credentials_refreshed:
            try:
                self._on_credentials_refreshed(dict(self.credentials))
            except Exception:
                # Persisting is an optimisation; failing to store a token that
                # already works must not fail the sync that just got one.
                pass
    def _request(self,path:str,*,method:str="GET",payload:dict[str,Any]|None=None)->dict[str,Any]:
        if not self.credentials.get("access_token"):self.refresh_connection()
        url="https://gmail.googleapis.com/gmail/v1/users/me/"+path
        for attempt in range(2):
            data=json.dumps(payload).encode() if payload is not None else None
            headers={"Authorization":"Bearer "+self.credentials["access_token"]}
            if data is not None:headers["Content-Type"]="application/json"
            req=urllib.request.Request(url,headers=headers,data=data,method=method)
            try:
                with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read())
            except urllib.error.HTTPError as exc:
                if exc.code==401 and attempt==0:
                    self.refresh_connection();continue
                self._status="ERROR";raise
            except Exception:
                self._status="ERROR";raise
        raise RuntimeError('Gmail request failed')
    def verify_connection(self)->dict[str,Any]: return self._request("profile")
    def start_watch(self,topic_name:str)->dict[str,Any]:
        """Register/renew Gmail push delivery; Gmail returns historyId + expiration."""
        topic=str(topic_name or "").strip()
        if not topic:raise ValueError("Gmail Pub/Sub topic is required")
        return self._request("watch",method="POST",payload={"topicName":topic,"labelIds":["INBOX"],"labelFilterBehavior":"include"})
    def stop_watch(self)->None:
        self._request("stop",method="POST",payload={})
    def get_connection_status(self)->str:return self._status
    def fetch_new_messages(self,cursor:str|None,*,batch_size:int)->tuple[list[dict[str,Any]],str|None]:
        if cursor:
            try:
                ids=[];page_token=None;latest=str(cursor)
                # Drain every history page before returning the new cursor.  A
                # Gmail historyId checkpoints the whole response, not merely the
                # first maxResults messages.  Truncating here and saving `latest`
                # permanently skips every ID on the remaining pages.
                while True:
                    params={"startHistoryId":cursor,"historyTypes":"messageAdded","maxResults":max(1,min(100,batch_size))}
                    if page_token:params["pageToken"]=page_token
                    data=self._request("history?"+urllib.parse.urlencode(params))
                    latest=str(data.get("historyId") or latest)
                    for h in data.get("history",[]):
                        ids.extend(x.get("message",{}).get("id") for x in h.get("messagesAdded",[]) if x.get("message",{}).get("id"))
                    page_token=data.get("nextPageToken")
                    if not page_token:break
            except urllib.error.HTTPError as exc:
                # Gmail returns 404 when a history cursor has expired. Recover
                # by fully enumerating the reconciliation window before taking
                # a fresh cursor.  The worker durably stages these IDs first.
                if exc.code!=404:raise
                refs=self._list_recent_messages(batch_size=batch_size)
                profile=self.verify_connection()
                self._status="CONNECTED"
                return refs,str(profile.get("historyId") or "")
            return ([{"id":x} for x in dict.fromkeys(ids)],latest)
        refs=self._list_recent_messages(batch_size=batch_size)
        profile=self.verify_connection(); return refs,str(profile.get("historyId") or "")
    def _list_recent_messages(self,*,batch_size:int)->list[dict[str,Any]]:
        maximum=max(1,int(os.getenv("AI_MAIL_RECONCILIATION_MAX_MESSAGES","5000")))
        messages=[];page_token=None
        while len(messages)<maximum:
            params={"maxResults":min(100,max(1,batch_size),maximum-len(messages)),"q":"newer_than:30d"}
            if page_token:params["pageToken"]=page_token
            data=self._request("messages?"+urllib.parse.urlencode(params))
            messages.extend(data.get("messages",[]));page_token=data.get("nextPageToken")
            if not page_token:break
        return messages
    def fetch_messages_by_date(self,range_start,range_end,*,limit:int=500)->list[dict[str,Any]]:
        # Gmail's `before` boundary is exclusive; include the selected end day.
        from datetime import timedelta
        query=f"after:{range_start:%Y/%m/%d} before:{(range_end+timedelta(days=1)):%Y/%m/%d}"
        messages=[];token=None
        while len(messages)<limit:
            params={"maxResults":min(100,limit-len(messages)),"q":query}
            if token:params["pageToken"]=token
            data=self._request("messages?"+urllib.parse.urlencode(params));messages.extend(data.get("messages",[]));token=data.get("nextPageToken")
            if not token:break
        return messages
    def fetch_message(self,message_id:str)->dict[str,Any]:return self._request(f"messages/{urllib.parse.quote(message_id)}?format=full")
    def fetch_thread(self,thread_id:str)->list[dict[str,Any]]:
        # Full bodies are required for semantic stage progression across a
        # conversation; the read-only Gmail scope still applies.
        return self._request(f"threads/{urllib.parse.quote(thread_id)}?format=full").get("messages",[])
    def fetch_attachments(self,message:dict[str,Any])->list[dict[str,Any]]:
        out=[]
        def walk(part):
            body=part.get("body") or {}; filename=part.get("filename") or ""
            if filename and body.get("attachmentId"):
                raw=self._request(f"messages/{message['id']}/attachments/{body['attachmentId']}")
                data=base64.urlsafe_b64decode((raw.get('data') or '')+'===')
                out.append({"filename":filename,"mime_type":part.get("mimeType"),"size":len(data),"data":data})
            for child in part.get("parts") or []:walk(child)
        walk(message.get("payload") or {});return out

def decode_gmail_message(raw:dict[str,Any],recipient:str)->dict[str,Any]:
    headers={h.get('name','').lower():h.get('value','') for h in raw.get('payload',{}).get('headers',[])}
    def bodies(part):
        found=[];kind=part.get('mimeType');encoded=(part.get('body') or {}).get('data')
        if kind in ('text/plain','text/html') and encoded:
            found.append((kind,base64.urlsafe_b64decode(encoded+'===').decode('utf-8','replace')))
        for child in part.get('parts') or []:found.extend(bodies(child))
        return found
    sender_name,sender_email=parseaddr(headers.get('from','')); sent=None
    try:sent=parsedate_to_datetime(headers.get('date','')).astimezone(timezone.utc)
    except Exception:sent=datetime.now(timezone.utc)
    subject=str(make_header(decode_header(headers.get('subject',''))))
    content=bodies(raw.get('payload') or {});plain='\n'.join(v for k,v in content if k=='text/plain');html_body='\n'.join(v for k,v in content if k=='text/html')
    to_addresses=[address.lower() for _name,address in getaddresses([headers.get('to','')]) if address]
    cc=[address.lower() for _name,address in getaddresses([headers.get('cc','')]) if address]
    labels=[str(value).upper() for value in raw.get('labelIds') or []]
    mailbox=str(recipient or '').strip().lower()
    direction=('OUTBOUND' if ('SENT' in labels and 'INBOX' not in labels) or (sender_email.lower()==mailbox and mailbox not in to_addresses) else 'INBOUND')
    # Reply-To and Return-Path are the two headers that expose a reply path
    # pointing away from the apparent sender. Ingestion writes both on every
    # message alongside the SPF/DKIM/DMARC verdicts, so mailbox_messages depends
    # on them being captured here. The feature that originally asked for them is
    # decommissioned; the columns are live ingestion data, not a leftover, and
    # removing either breaks the message insert.
    _reply_name,reply_to=parseaddr(headers.get('reply-to',''))
    _return_name,return_path=parseaddr(headers.get('return-path',''))
    return {"provider_message_id":raw['id'],"provider_thread_id":raw.get('threadId'),"sender_name":sender_name,"sender_email":sender_email,"recipient_email":recipient,"to_metadata":to_addresses,"cc_metadata":cc,"gmail_label_ids":labels,"message_direction":direction,"subject":subject,"sent_at":sent,"body":select_message_body(plain,html_body),"html_body":html_body,
            "rfc_message_id":headers.get('message-id'),"authentication_results":headers.get('authentication-results'),"received_spf":headers.get('received-spf'),
            "reply_to_email":(reply_to or '').lower() or None,"return_path_email":(return_path or '').lower() or None}
