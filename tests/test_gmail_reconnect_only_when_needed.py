""""Gmail connection expired" must mean the grant is actually dead.

The dashboard renders that banner from one field: `connection_status == 'ERROR'`
(see `needsReconnect` in dashboard/src/utils/mailboxStatus.js). The worker set
that field on *every* exception, so any failure at all told an operator to
reconnect an account whose credentials were fine.

Production had recorded 374 sync failures when this was traced: 270
RuntimeError -- the genuine expiries -- but also **28 HTTPError and 2
AttributeError**. Each of those 30 raised the banner on a healthy mailbox.

Worse, it stuck. `schedule_due` only queues mailboxes whose connection_status is
'CONNECTED', so a mailbox knocked into ERROR by one HTTP blip was never
scheduled again and could not recover on its own. That is why the banner
"appears repeatedly" and returns after a reconnect: reconnect restores
CONNECTED, the next transient error knocks it out again, and it stays out.

All twenty-one mailboxes were checked and every one holds a refresh token,
including both that were showing the banner -- so nothing loses or overwrites
refresh tokens on reconnect. Those two are genuine: Google answered
invalid_grant.
"""

from __future__ import annotations

import urllib.error

import pytest

from services.gmail_mailbox_provider import GmailAuthorizationExpired


def _http_error(code: int, body: bytes):
    import io as _io

    return urllib.error.HTTPError(
        "https://oauth2.googleapis.com/token", code, "err", {}, _io.BytesIO(body),
    )


class _Provider:
    """A provider with credentials, without touching Fernet or the network."""

    def __new__(cls, credentials, on_refresh=None):
        from services.gmail_mailbox_provider import GmailMailboxProvider

        provider = object.__new__(GmailMailboxProvider)
        provider.credentials = dict(credentials)
        provider._status = "CONNECTED"
        provider._cursor = None
        provider._on_credentials_refreshed = on_refresh
        return provider


class TestOnlyADeadGrantCountsAsExpired:
    def test_invalid_grant_is_an_expiry(self, monkeypatch):
        provider = _Provider({"refresh_token": "r", "access_token": "a"})
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")

        def refuse(*_a, **_k):
            raise _http_error(400, b'{"error":"invalid_grant"}')

        monkeypatch.setattr("urllib.request.urlopen", refuse)
        with pytest.raises(GmailAuthorizationExpired):
            provider.refresh_connection()

    def test_no_refresh_token_is_an_expiry(self):
        """Nothing but a human can fix this one."""
        provider = _Provider({"access_token": "a"})
        with pytest.raises(GmailAuthorizationExpired):
            provider.refresh_connection()

    @pytest.mark.parametrize("code", [429, 500, 502, 503])
    def test_google_being_briefly_unavailable_is_not_an_expiry(self, code, monkeypatch):
        provider = _Provider({"refresh_token": "r", "access_token": "a"})
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")

        def unavailable(*_a, **_k):
            raise _http_error(code, b"upstream unavailable")

        monkeypatch.setattr("urllib.request.urlopen", unavailable)
        with pytest.raises(urllib.error.HTTPError):
            provider.refresh_connection()

    def test_a_400_that_is_not_invalid_grant_is_not_an_expiry(self, monkeypatch):
        provider = _Provider({"refresh_token": "r", "access_token": "a"})
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")

        def refuse(*_a, **_k):
            raise _http_error(400, b'{"error":"temporarily_unavailable"}')

        monkeypatch.setattr("urllib.request.urlopen", refuse)
        with pytest.raises(urllib.error.HTTPError):
            provider.refresh_connection()

    def test_an_expiry_is_still_a_runtime_error_for_older_handlers(self):
        assert issubclass(GmailAuthorizationExpired, RuntimeError)


class TestARefreshedTokenIsKept:
    @staticmethod
    def _succeed(monkeypatch, payload: bytes):
        import contextlib

        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")

        class _Response:
            def read(self):
                return payload

        @contextlib.contextmanager
        def ok(*_a, **_k):
            yield _Response()

        monkeypatch.setattr("urllib.request.urlopen", ok)

    def test_the_new_access_token_is_handed_back_for_storage(self, monkeypatch):
        """Without this the token lives only in this instance: every later sync
        starts from the stale stored one and spends a 401 to find out."""
        stored = []
        provider = _Provider({"refresh_token": "r", "access_token": "old"}, stored.append)
        self._succeed(monkeypatch, b'{"access_token":"new"}')

        provider.refresh_connection()

        assert provider.credentials["access_token"] == "new"
        assert stored and stored[0]["access_token"] == "new"

    def test_the_refresh_token_survives_a_reissue_that_omits_it(self, monkeypatch):
        """Google does not resend it. Replacing rather than merging would drop
        the only credential that can recover the mailbox without a human."""
        stored = []
        provider = _Provider({"refresh_token": "keep-me", "access_token": "old"}, stored.append)
        self._succeed(monkeypatch, b'{"access_token":"new"}')

        provider.refresh_connection()

        assert provider.credentials["refresh_token"] == "keep-me"
        assert stored[0]["refresh_token"] == "keep-me"

    def test_a_failure_to_store_does_not_fail_the_refresh(self, monkeypatch):
        def explode(_creds):
            raise RuntimeError("database is down")

        provider = _Provider({"refresh_token": "r", "access_token": "old"}, explode)
        self._succeed(monkeypatch, b'{"access_token":"new"}')

        provider.refresh_connection()  # must not raise

        assert provider.credentials["access_token"] == "new"


class _Fake:
    """A provider that fails the way production actually failed."""

    def __init__(self, _cipher, on_credentials_refreshed=None):
        self.on_credentials_refreshed = on_credentials_refreshed

    def fetch_new_messages(self, cursor, *, batch_size, since=None):
        raise _Fake.failure

    def fetch_messages_by_date(self, start, end, *, limit=500):
        raise _Fake.failure


def _run_failing_sync(monkeypatch, failure):
    """Drive one sync whose fetch raises `failure`; return the mailbox update."""
    import workers.recruitment_mail_worker as module

    _Fake.failure = failure
    mailbox = {
        "id": "mb1", "candidate_id": "c1", "email_address": "c" + "@" + "test.invalid",
        "credential_ciphertext": "encrypted", "provider_history_id": "h1",
        "failed_sync_count": 0, "connection_status": "CONNECTED",
    }
    updates = []
    monkeypatch.setattr(module.store, "mailbox_by_id", lambda _: mailbox)
    monkeypatch.setattr(module.store, "update_mailbox",
                        lambda mid, values: updates.append(values) or mailbox)
    monkeypatch.setattr(module.store, "finish_job", lambda jid, **values: None)
    monkeypatch.setattr(module.store, "retry_job", lambda jid, **values: "QUEUED")
    monkeypatch.setattr(module.store, "pending_gmail_ingestion_count", lambda mid: 0)
    monkeypatch.setattr(module, "GmailMailboxProvider", _Fake)
    module.RecruitmentMailWorker().process_job({"id": "j1", "mailbox_id": "mb1", "attempts": 1})
    return updates[-1]


class TestWhatASyncFailureDoesToTheBanner:
    def test_a_transient_http_error_does_not_ask_for_a_reconnect(self, monkeypatch):
        """28 of these were recorded in production, every one showing the
        banner on a mailbox whose credentials were valid."""
        values = _run_failing_sync(monkeypatch, _http_error(503, b"unavailable"))

        assert "connection_status" not in values
        assert values["last_error_code"] == "HTTPError"
        assert values["failed_sync_count"] == 1

    def test_a_bug_of_ours_does_not_ask_for_a_reconnect(self, monkeypatch):
        """And 2 of these -- an AttributeError in our own code told an operator
        their Gmail authorization had expired."""
        values = _run_failing_sync(monkeypatch, AttributeError("'NoneType' has no attribute"))

        assert "connection_status" not in values
        assert values["last_error_code"] == "AttributeError"

    @pytest.mark.parametrize("failure", [
        TimeoutError("read timed out"),
        ConnectionResetError("connection reset by peer"),
        OSError("network unreachable"),
    ])
    def test_no_network_failure_asks_for_a_reconnect(self, monkeypatch, failure):
        assert "connection_status" not in _run_failing_sync(monkeypatch, failure)

    def test_a_dead_grant_does_ask_for_a_reconnect(self, monkeypatch):
        values = _run_failing_sync(
            monkeypatch,
            GmailAuthorizationExpired(
                "Gmail authorization expired or was revoked. Reconnect Gmail to resume "
                "automatic monitoring and historical rescans.",
            ),
        )

        assert values["connection_status"] == "ERROR"
        assert "expired or was revoked" in values["last_error_message"]

    def test_a_transient_failure_leaves_the_mailbox_schedulable(self, monkeypatch):
        """schedule_due only queues connection_status='CONNECTED', so setting
        ERROR on a blip stranded the mailbox: never scheduled, never recovered,
        banner until a human reconnected it."""
        import inspect

        import workers.recruitment_mail_worker as module

        assert "connection_status='CONNECTED'" in inspect.getsource(module.RecruitmentMailWorker.schedule_due)
        assert "connection_status" not in _run_failing_sync(monkeypatch, _http_error(500, b"x"))

    def test_a_successful_sync_still_clears_everything(self):
        import inspect

        import workers.recruitment_mail_worker as module

        source = inspect.getsource(module.RecruitmentMailWorker.process_job)
        assert "'connection_status':'CONNECTED'" in source
        assert "'last_error_message':None" in source


class TestTheBannerContract:
    """What the dashboard reads, so the two cannot drift apart."""

    @staticmethod
    def _status_source():
        from pathlib import Path

        return Path("dashboard/src/utils/mailboxStatus.js").read_text(encoding="utf-8")

    def test_the_banner_is_driven_by_the_error_status(self):
        source = self._status_source()
        assert "connection_status === 'ERROR'" in source
        assert "RECONNECT_REQUIRED" in source

    def test_each_mailbox_persists_its_own_credentials(self, monkeypatch):
        """Multi-account isolation: the callback must close over the mailbox it
        was built for, not whichever one the loop is on."""
        import inspect

        import workers.recruitment_mail_worker as module

        source = inspect.getsource(module.RecruitmentMailWorker.process_job)
        assert "_mailbox_id=mailbox['id']" in source
        assert "'credential_ciphertext':encrypt_credentials(creds)" in source
