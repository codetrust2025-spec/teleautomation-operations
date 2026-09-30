"""Gmail reconnect: detection, signed link, deduplicated reminders, resolution.

Google ends a mailbox's authorisation and only the account holder can grant it
again. Everything around that one click is automated here; the consent itself
never is, and the tests that matter most pin exactly that line.

Routes are exercised through an app assembled the way main.py assembles the real
one, with the session middleware on: a public route that the middleware refuses
is a link that silently does nothing (the Pub/Sub push endpoint was once exactly
that).
"""
import asyncio
import json
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as dashboard_auth
from core import recruitment_mail_api
from core.dashboard_auth_api import install_dashboard_auth
from core.recruitment_mail_api import install_recruitment_mail_routes
from services import gmail_reconnect as gr

IST = gr.IST
# 11:00 IST: inside the send window.
DAY = datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(gr, "_FILE", str(tmp_path / "notices.json"))
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "unit-test-signing-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://ops.example.test/api/candidate-mailboxes/oauth/google/callback")


def mailbox(n, *, status="ERROR", excluded=False, error="Gmail authorization expired or was revoked."):
    return {
        "id": f"mb-{n:02d}", "candidate_id": f"cand-{n:02d}", "email_address": f"person{n:02d}@example.test",
        "connection_status": status, "monitoring_excluded": excluded,
        "last_error_message": error if status == "ERROR" else "", "last_error_code": "GmailAuthorizationExpired",
    }


def candidates(cid):
    n = int(cid.split("-")[1])
    return {"name": f"Test Person {n:02d}", "reference": ["Owner A", "Owner B"][n % 2]}


def production_like():
    """22 actionable mailboxes plus the 2 production excludes (dropped, completed)."""
    return [mailbox(n) for n in range(1, 23)] + [mailbox(23, excluded=True), mailbox(24, excluded=True)]


class Sink:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def __call__(self, **payload):
        if self.fail:
            raise RuntimeError("outbox unavailable")
        self.sent.append(payload)


def cycle(now, rows, sink, **kw):
    return asyncio.run(gr.run_cycle(
        now=now, rows=rows, lookup=candidates, send=sink,
        recheck=kw.pop("recheck", lambda mid: next(r for r in rows if r["id"] == mid)), **kw,
    ))


# --- detection --------------------------------------------------------------------

def test_an_errored_mailbox_needs_reconnect():
    assert gr.needs_reconnect(mailbox(1)) is True


def test_expiry_wording_counts_even_if_the_status_was_not_moved_to_error():
    assert gr.needs_reconnect(mailbox(1, status="CONNECTED") | {"last_error_message": "token has been revoked"}) is True


def test_a_healthy_mailbox_does_not():
    assert gr.needs_reconnect(mailbox(1, status="CONNECTED")) is False


def test_a_closed_candidates_mailbox_is_never_reconnect_work():
    assert gr.needs_reconnect(mailbox(1, excluded=True)) is False


# --- the signed link ----------------------------------------------------------------

def test_a_link_round_trips_to_its_mailbox_and_address():
    token = gr.make_token("mb-01", "Person01@Example.Test", now=1000)
    assert gr.read_token(token, now=1001) == ("mb-01", "person01@example.test")


def test_a_tampered_link_is_refused():
    token = gr.make_token("mb-01", "p@example.test", now=1000)
    raw, sig = token.rsplit(".", 1)
    with pytest.raises(ValueError):
        gr.read_token(raw + "." + sig[:-1] + ("0" if sig[-1] != "0" else "1"), now=1001)
    with pytest.raises(ValueError):
        gr.read_token("not-a-token", now=1001)


def test_a_link_expires():
    token = gr.make_token("mb-01", "p@example.test", now=1000)
    with pytest.raises(ValueError, match="expired"):
        gr.read_token(token, now=1000 + gr.LINK_TTL_SECONDS + 1)


def test_a_link_signed_with_another_secret_is_refused(monkeypatch):
    token = gr.make_token("mb-01", "p@example.test", now=1000)
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "a-different-secret")
    with pytest.raises(ValueError):
        gr.read_token(token, now=1001)


def test_an_oauth_state_is_not_a_reconnect_link(monkeypatch):
    """The two signed things share a secret; one must never pass for the other."""
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "unit-test-signing-secret")
    state = recruitment_mail_api._state({"candidate_id": "c", "email": "p@example.test"})
    with pytest.raises(ValueError):
        gr.read_token(state)


# --- the plan: dedupe, cadence, quiet hours ---------------------------------------

def test_detection_opens_one_episode_per_broken_mailbox_and_ignores_the_rest():
    state = gr._empty()
    due = gr.plan_cycle(state, production_like(), now=DAY)
    assert len(due) == 22
    assert set(state["notices"]) == {f"mb-{n:02d}" for n in range(1, 23)}


def test_nothing_is_due_outside_the_send_window():
    state = gr._empty()
    night = datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc)  # 00:30 IST
    assert gr.plan_cycle(state, production_like(), now=night) == []
    assert len(state["notices"]) == 22, "still detected, only not announced"


def test_a_mailbox_that_breaks_at_night_is_announced_when_the_window_opens():
    state = gr._empty()
    gr.plan_cycle(state, [mailbox(1)], now=datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc))
    morning = datetime(2026, 10, 2, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
    assert gr.plan_cycle(state, [mailbox(1)], now=morning) == ["mb-01"]


def test_the_cadence_is_twelve_hours_twice_then_daily():
    state = gr._empty()
    rows = [mailbox(1)]
    times = []
    now = DAY
    for _ in range(5):
        due = gr.plan_cycle(state, rows, now=now)
        assert due == ["mb-01"]
        gr.record_sent(state, due, now=now)
        times.append(state["notices"]["mb-01"]["next_notice_at"])
        now = gr._parse(times[-1])
        while not gr.in_send_window(now):
            now += timedelta(hours=1)
    gaps = [
        (gr._parse(times[i + 1]) - gr._parse(times[i])).total_seconds() / 3600 for i in range(len(times) - 1)
    ]
    assert gaps[0] >= 12 and gaps[-1] >= 24


def test_two_digests_are_never_closer_than_the_minimum_gap():
    state = gr._empty()
    gr.record_sent(state, [], now=DAY)
    assert gr.plan_cycle(state, [mailbox(1)], now=DAY + timedelta(minutes=30)) == []
    assert gr.plan_cycle(state, [mailbox(1)], now=DAY + gr.MIN_DIGEST_GAP) == ["mb-01"]


def test_a_reconnected_mailbox_is_resolved_and_never_due_again():
    state = gr._empty()
    gr.plan_cycle(state, [mailbox(1)], now=DAY)
    later = DAY + timedelta(hours=30)
    due = gr.plan_cycle(state, [mailbox(1, status="CONNECTED")], now=later)
    assert due == []
    notice = state["notices"]["mb-01"]
    assert notice["status"] == "RESOLVED" and notice["resolution"] == "reconnected"


def test_a_mailbox_that_becomes_a_closed_candidate_stops_without_claiming_a_reconnect():
    state = gr._empty()
    gr.plan_cycle(state, [mailbox(1)], now=DAY)
    gr.plan_cycle(state, [mailbox(1, excluded=True)], now=DAY + timedelta(hours=1))
    assert state["notices"]["mb-01"]["resolution"] == "no longer required"


def test_a_mailbox_that_breaks_again_starts_a_new_episode():
    state = gr._empty()
    gr.plan_cycle(state, [mailbox(1)], now=DAY)
    gr.plan_cycle(state, [mailbox(1, status="CONNECTED")], now=DAY + timedelta(days=1))
    gr.plan_cycle(state, [mailbox(1)], now=DAY + timedelta(days=8))
    assert state["notices"]["mb-01"]["status"] == "OPEN" and state["notices"]["mb-01"]["episodes"] == 2
    assert state["notices"]["mb-01"]["notice_count"] == 0


# --- the digest ----------------------------------------------------------------------

def test_one_digest_covers_all_twenty_two_and_caps_the_text():
    items = [
        {"name": f"Test Person {n:02d}", "owner": "Owner A", "email": "x@example.test",
         "link": f"https://ops.example.test/l/{n}", "reminder": 0}
        for n in range(1, 23)
    ]
    digest = gr.build_digest(items, still_open_elsewhere=0, now=DAY)
    assert digest["title"] == "Gmail reconnect needed — 22 accounts"
    assert digest["whatsapp_text"].count("https://ops.example.test/l/") == gr.MAX_LINES_IN_TEXT
    assert "and 10 more" in digest["whatsapp_text"]
    assert "and 16 more" in digest["body"]


def test_a_reminder_says_which_reminder_it_is_and_changes_its_tag_by_the_hour():
    item = [{"name": "A", "owner": "", "email": "a@example.test", "link": "", "reminder": 2}]
    first = gr.build_digest(item, still_open_elsewhere=0, now=DAY)
    later = gr.build_digest(item, still_open_elsewhere=0, now=DAY + timedelta(hours=12))
    assert "(reminder 3)" in first["title"]
    assert first["tag"] != later["tag"]


# --- end to end over the production-shaped set -------------------------------------

def all_text(sink):
    return json.dumps(sink.sent)


def test_the_twenty_four_current_error_mailboxes_are_announced_once_with_every_link_then_silence():
    rows, sink = production_like(), Sink()
    first = cycle(DAY, rows, sink)
    assert first["sent"] and first["due"] == 22 and first["accounts_notified"] == 22
    assert first["messages"] == len(sink.sent) == 2, "22 accounts split 12 + 10, never truncated"
    assert sink.sent[0]["title"].endswith("(1 of 2)") and sink.sent[1]["title"].endswith("(2 of 2)")
    text = chr(10).join(m["whatsapp_text"] for m in sink.sent)
    assert text.count("https://ops.example.test/api/candidate-mailboxes/reconnect/") == 22
    assert all(f"Test Person {n:02d}" in text for n in range(1, 23))
    assert "person23" not in all_text(sink), "a dropped candidate is never mentioned"
    assert "person24" not in all_text(sink), "a completed candidate is never mentioned"

    for minutes in (10, 20, 40, 60, 119):
        cycle(DAY + timedelta(minutes=minutes), rows, sink)
    assert len(sink.sent) == 2, "reminders must not repeat inside the cadence"

    # Twelve hours on is 23:00 IST: due, but outside the send window, so it waits.
    cycle(DAY + timedelta(hours=12, minutes=1), rows, sink)
    assert len(sink.sent) == 2, "no reminder in the small hours"
    # The window opens at 09:00 IST the next day.
    cycle(DAY + timedelta(hours=22, minutes=1), rows, sink)
    assert len(sink.sent) == 4
    assert "(reminder 2)" in sink.sent[2]["title"]


def test_a_newly_broken_mailbox_is_announced_alone_without_repeating_the_others():
    rows, sink = production_like(), Sink()
    cycle(DAY, rows, sink)
    rows = rows + [mailbox(25)]
    result = cycle(DAY + gr.MIN_DIGEST_GAP, rows, sink)
    assert result["due"] == 1 and len(sink.sent) == 3
    assert "Test Person 25" in sink.sent[2]["whatsapp_text"]
    assert "Test Person 01" not in sink.sent[2]["whatsapp_text"]
    assert "22 more are also waiting" in sink.sent[2]["body"]


def test_reminders_stop_for_a_mailbox_once_connected_and_go_quiet_when_all_are():
    rows, sink = production_like(), Sink()
    cycle(DAY, rows, sink)
    rows = [mailbox(n, status="CONNECTED") if n <= 22 else mailbox(n, excluded=True) for n in range(1, 25)]
    for hours in (13, 30, 60, 100):
        cycle(DAY + timedelta(hours=hours), rows, sink)
    assert len(sink.sent) == 2
    state = gr.load_state()
    assert all(n["status"] == "RESOLVED" for n in state["notices"].values())


def test_a_failed_send_advances_nothing_and_is_retried_next_cycle():
    rows, sink = production_like(), Sink(fail=True)
    assert cycle(DAY, rows, sink)["sent"] is False
    state = gr.load_state()
    assert all(n["notice_count"] == 0 for n in state["notices"].values())
    sink.fail = False
    retry = cycle(DAY + timedelta(minutes=10), rows, sink)
    assert retry["sent"] is True and len(sink.sent) == 2


def test_a_reconnect_that_lands_between_the_snapshot_and_the_send_is_not_reminded():
    rows, sink = production_like(), Sink()
    fresh = {r["id"]: dict(r) for r in rows}
    fresh["mb-05"]["connection_status"] = "CONNECTED"
    fresh["mb-05"]["last_error_message"] = ""
    result = cycle(DAY, rows, sink, recheck=lambda mid: fresh[mid])
    assert result["due"] == 21
    assert "Test Person 05" not in all_text(sink)
    assert gr.load_state()["notices"]["mb-05"]["status"] == "RESOLVED"


def test_the_callback_resolving_during_the_send_is_not_undone():
    rows, sink = production_like(), Sink()

    async def send_and_reconnect(**payload):
        sink.sent.append(payload)
        gr.mark_resolved("mb-07")  # the OAuth callback, running while we send

    asyncio.run(gr.run_cycle(now=DAY, rows=rows, lookup=candidates, send=send_and_reconnect,
                             recheck=lambda mid: next(r for r in rows if r["id"] == mid)))
    notice = gr.load_state()["notices"]["mb-07"]
    assert notice["status"] == "RESOLVED" and notice["resolution"] == "reconnected"
    assert notice["notice_count"] == 0


def test_a_dry_run_reports_the_digest_and_writes_nothing():
    rows, sink = production_like(), Sink()
    result = cycle(DAY, rows, sink, dry_run=True)
    assert result["due"] == 22 and "digest" in result and sink.sent == []
    assert gr.load_state()["notices"] == {}


# --- routes, through an app with the session middleware on --------------------------

FAKE_MB = mailbox(1)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AI_INTERVIEW_OFFER_TRACKING_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-operator-password")
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id.example.test")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MAILBOX_CREDENTIAL_ENCRYPTION_KEY", "k" * 44)
    monkeypatch.setattr(recruitment_mail_api.store, "mailbox_by_id",
                        lambda mid: dict(FAKE_MB) if mid == FAKE_MB["id"] else None)
    app = FastAPI()
    install_dashboard_auth(app)
    install_recruitment_mail_routes(app)
    dashboard_auth.register_api_roots(app)
    assert dashboard_auth.auth_enabled()
    return TestClient(app, follow_redirects=False)


def link_path(mailbox_row=FAKE_MB, **kw):
    return "/api/candidate-mailboxes/reconnect/" + gr.make_token(mailbox_row["id"], mailbox_row["email_address"], **kw)


def test_the_link_needs_no_session_and_goes_to_googles_consent_screen_for_that_address(client):
    response = client.get(link_path())
    assert response.status_code == 302
    target = urllib.parse.urlsplit(response.headers["location"])
    assert target.netloc == "accounts.google.com"
    query = urllib.parse.parse_qs(target.query)
    assert query["login_hint"] == ["person01@example.test"]
    assert query["prompt"] == ["consent"], "Google's own consent screen is always shown"
    assert query["redirect_uri"] == ["https://ops.example.test/api/candidate-mailboxes/oauth/google/callback"]
    state = recruitment_mail_api._read_state(query["state"][0])
    assert state["email"] == "person01@example.test" and state["via"] == "link"


def test_a_bad_or_expired_link_never_reaches_google(client):
    assert client.get("/api/candidate-mailboxes/reconnect/not-a-token").status_code == 400
    assert client.get(link_path(now=1000)).status_code == 400


def test_a_link_for_a_mailbox_that_is_no_longer_monitored_says_so(client):
    gone = {"id": "mb-99", "email_address": "gone@example.test"}
    assert client.get(link_path(gone)).status_code == 404


def test_a_link_for_an_already_connected_mailbox_does_nothing(client, monkeypatch):
    monkeypatch.setattr(recruitment_mail_api.store, "mailbox_by_id",
                        lambda mid: {**FAKE_MB, "connection_status": "CONNECTED", "last_error_message": ""})
    response = client.get(link_path())
    assert response.status_code == 200 and "Already connected" in response.text


def test_a_link_for_a_different_address_than_the_mailbox_is_refused(client):
    forged = gr.make_token(FAKE_MB["id"], "someone-else@example.test")
    assert client.get("/api/candidate-mailboxes/reconnect/" + forged).status_code == 404


def test_the_oauth_callback_is_reachable_without_a_session_but_rejects_unsigned_state(client):
    assert dashboard_auth.is_public_path("/api/candidate-mailboxes/oauth/google/callback")
    response = client.get("/api/candidate-mailboxes/oauth/google/callback", params={"code": "x", "state": "forged.state"})
    assert response.status_code != 401, "the middleware must let Google's redirect through"
    assert response.status_code >= 400, "and the handler must still refuse a state we did not sign"


def test_the_status_endpoint_still_needs_a_session(client):
    assert client.get("/api/candidate-mailboxes/reconnect-status").status_code == 401


def test_the_reconnect_path_is_public_but_only_that_path():
    assert dashboard_auth.is_public_path("/api/candidate-mailboxes/reconnect/anything")
    assert not dashboard_auth.is_public_path("/api/candidate-mailboxes/overview")
    assert not dashboard_auth.is_public_path("/api/candidate-mailboxes/reconnect-status")
    assert not dashboard_auth.is_public_path("/api/candidates/x/mailbox/connect")


def test_a_failure_part_way_keeps_what_was_delivered_and_retries_only_the_rest():
    rows = production_like()

    class FailsSecond(Sink):
        async def __call__(self, **payload):
            if len(self.sent) == 1:
                raise RuntimeError("outbox unavailable")
            await super().__call__(**payload)

    sink = FailsSecond()
    first = cycle(DAY, rows, sink)
    assert first["accounts_notified"] == 12 and first["sent"] is True
    state = gr.load_state()
    told = [mid for mid, n in state["notices"].items() if n["notice_count"] == 1]
    assert len(told) == 12

    retry_sink = Sink()
    later = cycle(DAY + gr.MIN_DIGEST_GAP, rows, retry_sink)
    assert later["due"] == 10, "only the ten that were never announced"
    assert not any(f"Test Person {n:02d}" in retry_sink.sent[0]["whatsapp_text"] for n in range(1, 13))


def test_a_very_large_backlog_is_capped_per_cycle_and_finishes_on_the_next():
    rows = [mailbox(n) for n in range(1, 41)]
    sink = Sink()
    first = cycle(DAY, rows, sink)
    assert first["messages"] == gr.MAX_MESSAGES_PER_CYCLE == 3
    assert first["accounts_notified"] == 36
    second = cycle(DAY + gr.MIN_DIGEST_GAP, rows, sink)
    assert second["due"] == 4 and second["accounts_notified"] == 4
