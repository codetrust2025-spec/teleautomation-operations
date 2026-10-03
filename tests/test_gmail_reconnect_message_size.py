"""A reconnect message never exceeds what the receiving service accepts.

Marketing validates `whatsapp_text` at 4000 characters. The digest was split by
account count (12), and a real signed link is ~280 characters, so a full message
was ~3,800 and, once the names and the "Links work" line were added, over the
limit: Marketing answered 422, the outbox retried 12 times and marked it dead.
Three such events went dead on 1, 2 and 3 Oct while the second half of the same
split was delivered, so 12 accounts' holders were never messaged. The unit tests
used 40-character links, which is why none of this showed.

Names and addresses are invented.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from services import gmail_reconnect as gr
from tests.test_gmail_reconnect import Sink, candidates, mailbox

DAY = datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc)
MARKETING_LIMIT = 4000
REAL_LINK = "https://ops.example.test/api/candidate-mailboxes/reconnect/" + "A" * 250  # production-length token (a real line is ~319 chars)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(gr, "_FILE", str(tmp_path / "notices.json"))
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "unit-test-signing-secret")
    monkeypatch.setattr(gr, "link_for", lambda row: REAL_LINK + str(row.get("id")))


def items(n, *, name_len=12, owner="Owner A"):
    return [{"name": ("Person" + str(i)).ljust(name_len, "x"), "owner": owner, "email": "x@example.test",
             "link": REAL_LINK + str(i), "reminder": 0} for i in range(n)]


def run(n, sink=None, now=DAY):
    sink = sink or Sink()
    rows = [mailbox(i) for i in range(1, n + 1)]
    result = asyncio.run(gr.run_cycle(now=now, rows=rows, lookup=candidates, send=sink,
                                      recheck=lambda mid: next(r for r in rows if r["id"] == mid)))
    return result, sink


def test_a_full_message_of_production_length_links_fits_the_limit():
    for group in gr.split_for_messages(items(30)):
        digest = gr.build_digest(group, still_open_elsewhere=0, now=DAY, part=(1, 3))
        assert len(digest["whatsapp_text"]) <= MARKETING_LIMIT, len(group)


def test_twelve_real_links_no_longer_travel_in_one_message():
    """The exact production shape: 12 accounts in one chunk was over the limit."""
    one = gr.build_digest(items(12), still_open_elsewhere=0, now=DAY)
    assert len(one["whatsapp_text"]) > MARKETING_LIMIT, "premise: 12 of these is too long for one message"
    assert max(len(g) for g in gr.split_for_messages(items(12))) < 12


def test_no_account_is_dropped_or_split_across_messages():
    groups = gr.split_for_messages(items(22))
    flat = [i["link"] for g in groups for i in g]
    assert flat == [i["link"] for i in items(22)], "every account, once, in order"


def test_short_links_still_fill_to_the_account_cap():
    short = [{"name": f"P{i}", "owner": "", "email": "", "link": f"https://o.test/{i}", "reminder": 0} for i in range(22)]
    assert [len(g) for g in gr.split_for_messages(short)] == [gr.MAX_LINES_IN_TEXT, 10]


def test_long_names_and_owners_are_counted_not_just_links():
    groups = gr.split_for_messages(items(20, name_len=60, owner="An Owner With A Long Display Name"))
    for group in groups:
        digest = gr.build_digest(group, still_open_elsewhere=0, now=DAY)
        assert len(digest["whatsapp_text"]) <= MARKETING_LIMIT


def test_one_oversized_account_still_gets_its_own_message():
    huge = items(1)
    huge[0]["name"] = "N" * 300
    groups = gr.split_for_messages(items(2) + huge)
    assert sum(len(g) for g in groups) == 3 and all(g for g in groups)


def test_an_empty_list_makes_no_message():
    assert gr.split_for_messages([]) == []


def test_a_real_cycle_over_twenty_two_mailboxes_sends_only_messages_the_service_accepts():
    result, sink = run(22)
    assert result["sent"] and result["accounts_notified"] == 22
    assert all(len(m["whatsapp_text"]) <= MARKETING_LIMIT for m in sink.sent)
    text = "\n".join(m["whatsapp_text"] for m in sink.sent)
    assert text.count("/reconnect/") == 22, "every account's link is in some message"
    assert len(sink.sent) >= 2 and sink.sent[0]["title"].endswith(f"(1 of {len(sink.sent)})")


def test_parts_are_numbered_by_what_was_actually_sent():
    _, sink = run(22)
    n = len(sink.sent)
    assert [m["title"].rsplit("(", 1)[-1].split(")")[0] for m in sink.sent] == [f"{i} of {n}" for i in range(1, n + 1)]


def test_more_accounts_than_one_cycle_can_carry_stay_due_for_the_next():
    """Never silently lost: the remainder is retried, not marked notified."""
    result, sink = run(40)
    assert result["messages"] == gr.MAX_MESSAGES_PER_CYCLE
    assert result["accounts_notified"] < 40
    result2, sink2 = run(40, now=DAY + timedelta(hours=3))
    assert result2["sent"], "the accounts that did not fit are announced in the next cycle"
