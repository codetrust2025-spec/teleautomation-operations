#!/usr/bin/env python3
"""Fill `interview_company` on interview records that still have none, from the emails that booked them.

A record auto-booked from an email whose AI analysis named no company shows "—" in Daily Ops even when the email
plainly comes from somewhere (12 Oct: a technical discussion sent from a mercedes-benz.com address, booking evidence
"Unknown company"). This reads those emails again. A company is written only when it is corroborated:

  1. the email names it, in words quoted verbatim from the email's subject, body or signature (never an address), as
     the organisation the interview is for -- the end client when a staffing firm or platform arranges it;
  2. a second, different model reads the same email and names a compatible company, also quoted; and
  3. something outside the email's wording agrees: the sender's own corporate domain, or an invite screenshot on the
     record that both screenshot models read and quoted.

Anything less is not written. It is listed for review with its reason: the sender's domain is the only evidence
(sender-only), only the email's text names it (a platform or a vendor writing), the two models or two emails
disagree, the email also names another known company (a vendor and its client), or a screenshot names another one.
A company already recorded is never replaced; a recorded value that is not a company ("Contract") may be corrected.

Sub-commands ([container] = inside the operations-api container, where the database is):

  collect  [container]  every interview record with no company and the emails linked to it (no email text leaves)
  read     [container]  ONE email, ONE stage (primary or audit model) -> one JSON line (no writes)
  plan     [container]  records + readings (+ the screenshot backfill's readings) -> what would change and why
  review   [container]  the plan's records left for a person, as TSV
  report   [container]  the plan's totals
  drive    [host]       the resumable loop over every email, both stages, then the plan

Writes, their check and their undo are the screenshot backfill's (`interview_company_backfill.py apply`, `verify`,
`rollback`), which take this plan unchanged.
"""
from __future__ import annotations

import argparse
import collections
import html as html_lib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())  # `docker exec` starts in the application directory

_CORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interview_company_backfill.py")
_spec = importlib.util.spec_from_file_location("interview_company_backfill", _CORE_PATH)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)

IN_CONTAINER = "/tmp/interview_company_email_backfill.py"
CORE_IN_CONTAINER = "/tmp/interview_company_backfill.py"
# Text models, kept off the RTX node while another node can serve them (it holds the live invite reader's model).
AUDIT_MODEL = "qwen3:8b"
ORDER = ("jagadeesh", "our_machine", "rtx4060")
MAX_EMAIL_CHARS = 6000
TIME_WINDOW_MINUTES = 15     # an email is linked to a hand-booked record only at the same date and start, give or take this
ACTIONS_THAT_WRITE = core.ACTIONS_THAT_WRITE

# A sender at one of these is not the employer: personal mail, and the platforms and applicant-tracking systems that
# send invitations on a company's behalf.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.co.in", "yahoo.in",
    "rediffmail.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "zoho.com", "zohomail.in",
})
PLATFORMS = frozenset({
    "flocareer.com", "hirepro.in", "kekamail.com", "keka.com", "barraiser.com", "naukri.com", "linkedin.com",
    "calendly.com", "zoom.us", "microsoft.com", "google.com", "greenhouse.io", "lever.co", "myworkday.com",
    "workday.com", "smartrecruiters.com", "icims.com", "darwinbox.in", "darwinbox.com", "zohorecruit.com",
    "zohorecruit.in", "hackerrank.com", "hackerearth.com", "codility.com", "mettl.com", "talview.com", "hirevue.com",
    "intervue.io", "incruiter.com", "interviewvector.com", "superset.com", "instahyre.com", "foundit.in",
    "shine.com", "indeed.com", "glassdoor.com", "ripplehire.com", "skillate.com", "teamtailor.com",
})
_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "ac", "gov", "edu"})


# ---------------------------------------------------------------------------------------------
# Pure logic (unit-tested; no application imports)
# ---------------------------------------------------------------------------------------------

def domain_of(address: str) -> str:
    address = str(address or "").strip().lower().rstrip(">").split("<")[-1]
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def org_label(domain: str) -> str:
    """The organisation part of a domain: mercedes-benz.com -> mercedes-benz, primepathway.co.in -> primepathway."""
    parts = [p for p in str(domain or "").lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        parts = parts[:-2]
    elif len(parts) >= 2:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def is_employer_domain(domain: str) -> bool:
    domain = str(domain or "").lower()
    if not domain or domain in FREE_MAIL:
        return False
    return not any(domain == p or domain.endswith("." + p) for p in PLATFORMS)


def domain_supports(company: str, domain: str) -> bool:
    """The company is the domain's own organisation: 'Tiger Analytics' / tigeranalytics.com, 'DXC Technology' /
    dxc.com, 'Fractal Analytics' / fractal.ai. Never for personal mail or a platform."""
    if not is_employer_domain(domain):
        return False
    label = core.alnum(org_label(domain))
    key = core.alnum(company)
    if len(label) < 3 or len(key) < 3:
        return False
    return key.startswith(label) or (len(key) >= 4 and label.startswith(key))


def strip_html(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", str(markup or ""))
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", text))


def email_text(subject: str, body: str, html: str) -> str:
    """What the models read and what a quote must come from: the subject and the body as words, with every email and
    web address taken out -- a company may not be read off an address."""
    plain = str(body or "")
    stripped = strip_html(html)
    text = plain if len(plain.strip()) >= 0.5 * len(stripped.strip()) else stripped
    text = f"Subject: {subject or ''}\n\n{text}"
    text = re.sub(r"\S+@\S+", " ", text)
    text = re.sub(r"(?i)\b(?:https?://|www\.)\S+", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:MAX_EMAIL_CHARS]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def companies_named_in(text: str, known: list[str]) -> list[str]:
    """The known companies an email's text names, as whole words ('Evoke' is not found in 'revoked'), with or without
    their legal suffix and their spaces ('Bahwan Cybertek' / 'BahwanCybertek')."""
    tokens = _words(text)
    haystack = " " + " ".join(tokens) + " "
    joined = {"".join(tokens[i:i + n]) for n in range(1, 6) for i in range(len(tokens) - n + 1)}
    found = []
    for name in known:
        words = _words(name)
        while len(words) > 1 and words[-1] in core._LEGAL:
            words.pop()
        if not words or len("".join(words)) < 4:
            continue
        if f" {' '.join(words)} " in haystack or "".join(words) in joined:
            found.append(name)
    return found


def quoted_in(name: str, quote: str, text: str) -> bool:
    """The model's quote is really in the email and really contains the name (case, spacing and punctuation aside)."""
    key, q = core.alnum(name), core.alnum(quote)
    return len(key) >= 3 and bool(q) and key in q and q in core.alnum(text)


EMAIL_PROMPT = (
    "Read this email. It was sent to a job candidate. Return ONLY valid JSON, no markdown:\n"
    '{{"is_interview_invitation": true, "employer": "", "employer_quote": "", "intermediary": "", "intermediary_quote": ""}}\n'
    "- is_interview_invitation: true only if the email invites the candidate to, schedules, reschedules or confirms a job "
    "interview, or forwards such an invitation. false for delivery failures, job alerts, newsletters, background "
    "checks, offers, career consultations and anything else.\n"
    "- employer: the organisation the interview is for, the company the candidate would work for. When a staffing firm, "
    "consultancy, recruiter or interview platform arranges the interview for a client and the email names that client, "
    "employer is the client. Exactly as the email writes it. Empty if the email does not name it. Do not guess, and "
    "never take it from an email address, a web address or the meeting platform (Teams, Zoom, Google Meet, Webex).\n"
    "- employer_quote: the exact words from the email that name the employer, at most 120 characters. Empty if employer "
    "is empty.\n"
    "- intermediary: a staffing firm, consultancy, recruiter or interview platform the email names as arranging the "
    "interview for someone else. Empty if none.\n"
    "- intermediary_quote: the exact words that name it. Empty if intermediary is empty.\n\n"
    "EMAIL:\n<<<\n{text}\n>>>"
)


def read_email(text: str, *, model: str, order: list[str], ask=None, clean=None) -> dict:
    """One model's reading of one email. Nothing of the email is kept but the names and whether they are quoted."""
    ask = ask or core.ask_nodes
    if clean is None:
        from features.ollama_invite_extract import clean_company_name as clean
    started = time.time()
    got = ask(model, None, EMAIL_PROMPT.format(text=text), list(order))
    seconds = round(time.time() - started, 1)
    if got["unavailable"]:
        return {"status": "unavailable", "error": got["error"]}
    if got["error"]:
        return {"status": "failed", "error": ("read: " + got["error"])[:240], "node": got["node"], "seconds": seconds}
    answer = got["answer"]
    base = {"model": model, "node": got["node"], "seconds": seconds, "text_len": len(text)}
    if answer.get("is_interview_invitation") is False:
        return {"status": "not_an_invite", "company": "", "verified": None, **base}
    employer = clean(answer.get("employer"))
    intermediary = clean(answer.get("intermediary"))
    return {
        "status": "ok",
        "company": employer,
        "verified": quoted_in(employer, str(answer.get("employer_quote") or ""), text) if employer else None,
        "intermediary": intermediary,
        "intermediary_verified": quoted_in(intermediary, str(answer.get("intermediary_quote") or ""), text) if intermediary else None,
        **base,
    }


def _distinct(names: list[str]) -> list[str]:
    kept: list[str] = []
    for name in names:
        if not any(core.compatible(name, other) for other in kept):
            kept.append(name)
    return kept


def decide_row(row: dict, primary: dict[str, dict], audit: dict[str, dict], *, clean, screenshot_names=(),
               other_companies_in: dict[str, list[str]] | None = None) -> dict:
    """The outcome for one record.

    `row`: {"before", "links": [{"key", "domains"}]}. `clean(value)` is clean_company_name with the record's people.
    `screenshot_names`: companies both screenshot models read and quoted off this record's own invite screenshots.
    `other_companies_in[key]`: known companies (from other records) that the email's text also names.
    """
    before = str(row.get("before") or "").strip()
    links = row.get("links") or []
    base = {"before": before, "after": "", "evidence": []}
    if not links:
        return {**base, "action": "no_email"}
    all_domains = sorted({d for link in links for d in link.get("domains") or [] if is_employer_domain(d)})
    # A hand-booked record is tied to its email only by date and start. When the closest emails come from two
    # organisations (an interview at 15:00 with one, at 15:15 with another), which one it is would be a guess.
    if all(link.get("link") == "date_time" for link in links) and len({org_label(d) for d in all_domains}) > 1:
        return {**base, "action": "review_ambiguous_email_link", "sender_domains": all_domains,
                "suggestion": ", ".join(all_domains)}
    claims, invitations, domains, unread, pending = [], 0, set(), 0, 0
    for link in links:
        key = link["key"]
        first = primary.get(key)
        if first is None or core._due(first):
            pending += 1
            continue
        if first.get("status") == "failed":
            unread += 1
            continue
        if first.get("status") != "ok":
            continue
        invitations += 1
        employer_domains = [d for d in link.get("domains") or [] if is_employer_domain(d)]
        domains.update(employer_domains)
        name = clean(first.get("company") or "")
        if not name or first.get("verified") is not True:
            continue
        second = audit.get(key)
        if second is None or core._due(second):
            pending += 1
            continue
        second_name = clean(second.get("company") or "") if second.get("status") == "ok" else ""
        same = bool(second_name) and core.compatible(second_name, name)
        claims.append({
            "key": key, "name": name,
            # The same name it cannot quote is not a confirmation; a different name is a disagreement.
            "audit": ("confirmed" if same and second.get("verified") is True
                      else "disagrees" if second_name and not same else "not_confirmed"),
            "domain": any(domain_supports(name, d) for d in employer_domains),
            "others": [o for o in (other_companies_in or {}).get(key, []) if not core.compatible(o, name)],
        })
    base["evidence"] = [{"key": c["key"][:8], "company": c["name"], "audit": c["audit"], "domain": c["domain"]} for c in claims]
    base["sender_domains"] = sorted(domains)
    if pending:
        return {**base, "action": "pending"}
    if not invitations:
        if unread:
            return {**base, "action": "unreadable"}
        # The record was booked from this email, yet it does not read as an invitation (a bare calendar invite, a
        # consultation, a background check). With a company's own domain on it, a person should look.
        if all_domains:
            return {**base, "action": "review_not_an_invitation", "sender_domains": all_domains,
                    "suggestion": ", ".join(all_domains)}
        return {**base, "action": "not_an_invite"}
    names = _distinct([c["name"] for c in claims])
    if len(names) > 1:
        return {**base, "action": "review_emails_disagree"}
    if not names:
        return {**base, "action": "review_sender_only" if domains else "no_company_in_email",
                "suggestion": ", ".join(sorted(domains))}
    name = max((c["name"] for c in claims), key=lambda n: len(core.company_key(n)))
    out = {**base, "suggestion": name}
    if not any(c["audit"] == "confirmed" for c in claims):
        return {**out, "action": "review_models_disagree" if any(c["audit"] == "disagrees" for c in claims)
                else "review_second_model_not_confirmed"}
    if any(c["others"] for c in claims):
        return {**out, "action": "review_other_company_named"}
    shots = list(screenshot_names or ())
    if shots and not any(core.compatible(s, name) for s in shots):
        return {**out, "action": "review_conflicts_with_screenshot"}
    by_domain = any(c["domain"] for c in claims if c["audit"] == "confirmed")
    by_screenshot = any(core.compatible(s, name) for s in shots)
    if not (by_domain or by_screenshot):
        return {**out, "action": "review_named_in_email_only"}
    basis = "+".join(b for b, ok in (("sender_domain", by_domain), ("screenshot", by_screenshot)) if ok)
    action = "correct_invalid" if before else "fill"
    return {**out, "action": action, "after": name, "basis": f"email_text+second_model+{basis}"}


BUCKETS = {
    "to_update": ("fill", "correct_invalid"),
    "skipped": ("no_company_in_email", "not_an_invite"),
    "review": ("review_sender_only", "review_named_in_email_only", "review_emails_disagree", "review_models_disagree",
               "review_second_model_not_confirmed", "review_other_company_named", "review_conflicts_with_screenshot",
               "review_ambiguous_email_link", "review_not_an_invitation"),
    "failed": ("unreadable",),
    "pending": ("pending",),
    "not_eligible": ("no_email",),
}
BUCKET_OF = {action: bucket for bucket, actions in BUCKETS.items() for action in actions}

REVIEW_REASONS = {
    "review_sender_only": "only the sender's domain points to a company; the email never names one",
    "review_named_in_email_only": "the email names the company but nothing independent confirms it (platform or vendor sender)",
    "review_emails_disagree": "the emails name different companies",
    "review_models_disagree": "the second model read a different company",
    "review_second_model_not_confirmed": "the second model could not read and quote the same company",
    "review_other_company_named": "the email also names another company (a vendor and its client?)",
    "review_conflicts_with_screenshot": "the invite screenshot names a different company",
    "review_ambiguous_email_link": "the closest emails at that date and time come from different organisations",
    "review_not_an_invitation": "the booking's email does not read as an interview invitation",
    "unreadable": "every reading of the email failed",
}


def summarise_plan(plan: dict) -> dict:
    buckets: collections.Counter = collections.Counter(BUCKET_OF.get(i.get("action"), "unknown") for i in plan.get("items") or [])
    eligible = sum(n for bucket, n in buckets.items() if bucket != "not_eligible")
    out = {"records_without_company": sum(buckets.values()), "with_linked_email": eligible,
           "scanned": eligible - buckets["pending"], **{b: buckets[b] for b in ("to_update", "skipped", "review", "failed", "pending", "not_eligible")}}
    if buckets["unknown"]:
        out["unknown"] = buckets["unknown"]
    return out


def closest_in_time(near: list[tuple[int, str, object]]) -> list[tuple[str, object]]:
    """(key, payload) of the candidates at the smallest time gap: an email exactly at the booked start outranks one a
    quarter-hour away, which is a different interview more often than the same one."""
    best = min((gap for gap, _, _ in near), default=None)
    return [(key, payload) for gap, key, payload in near if gap == best]


def minutes_of(value) -> int | None:
    text = str(value or "").strip().upper()
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            moment = datetime.strptime(text, pattern)
            return moment.hour * 60 + moment.minute
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------------------------
# Container side
# ---------------------------------------------------------------------------------------------

def _store():
    from features import candidate_store as cs
    from features.ollama_invite_extract import clean_company_name
    return cs, clean_company_name


def _people(row: dict) -> tuple[str, ...]:
    return tuple(str(row.get(k) or "") for k in ("name", "interview_attendee") if row.get(k))


def _read_only(cur):
    cur.execute("SET TRANSACTION READ ONLY")


def cmd_collect(_args) -> int:
    from core.db.connection import get_connection
    cs, clean = _store()
    rows = [r for r in cs._load(force=True)["candidates"] if r.get("date") and r.get("time")]
    out_rows, emails = {}, {}
    with get_connection() as conn, conn.cursor() as cur:
        _read_only(cur)
        cur.execute("SELECT candidate_id FROM candidate_mailboxes")
        mailbox_owners = {r[0] for r in cur.fetchall()}
        for row in rows:
            if clean(row.get("interview_company") or "", people=_people(row)):
                continue
            cid = str(row["id"])
            links: dict[str, dict] = {}
            mid, tid = str(row.get("interview_source_message_id") or ""), str(row.get("interview_source_thread_id") or "")
            if mid or tid:
                cur.execute("SELECT id, provider_message_id, sender_email, return_path_email, reply_to_email FROM mailbox_messages "
                            "WHERE provider_message_id=%s OR (%s<>'' AND provider_thread_id=%s)", (mid, tid, tid))
                for key, pmid, *addresses in cur.fetchall():
                    links[key] = {"key": key, "link": "source" if pmid == mid else "thread", "addresses": addresses}
            else:
                owners = (set(cs.candidate_identity_ids(cid, include_name_matches=False)) | {cid}) & mailbox_owners
                start = minutes_of(row.get("time"))
                if owners and start is not None:
                    cur.execute("SELECT m.id, m.sender_email, m.return_path_email, m.reply_to_email, a.interview_time "
                                "FROM interview_mail_analyses a JOIN mailbox_messages m ON m.id=a.mailbox_message_id "
                                "WHERE m.candidate_id = ANY(%s) AND a.interview_date=%s", (list(owners), str(row["date"])[:10]))
                    near = []
                    for key, *rest in cur.fetchall():
                        when = minutes_of(rest[3])
                        if when is not None and abs(when - start) <= TIME_WINDOW_MINUTES:
                            near.append((abs(when - start), key, rest[:3]))
                    for key, addresses in closest_in_time(near):
                        links[key] = {"key": key, "link": "date_time", "addresses": addresses}
            entries = []
            for link in links.values():
                domains = sorted({domain_of(a) for a in link.pop("addresses") if domain_of(a)})
                entries.append({**link, "domains": domains})
                emails[link["key"]] = {"sha": link["key"], "domains": domains}
            out_rows[cid] = {"before": str(row.get("interview_company") or ""), "date": str(row.get("date") or ""),
                             "time": str(row.get("time") or ""), "links": entries}
    print(json.dumps({"generated_at": core._now(), "rows": out_rows, "emails": emails}))
    return 0


def _email_text_by_key(cur, key: str) -> str | None:
    cur.execute("SELECT subject, body_text, html_body_text FROM mailbox_messages WHERE id=%s", (key,))
    found = cur.fetchone()
    return email_text(*found) if found else None


def cmd_read(args) -> int:
    from core import ai_gateway, ollama_nodes
    from core.db.connection import get_connection
    out = {"sha": args.key, "stage": args.stage, "at": core._now()}
    with get_connection() as conn, conn.cursor() as cur:
        _read_only(cur)
        text = _email_text_by_key(cur, args.key)
    if text is None:
        out.update(status="failed", error="email not found")
    else:
        model = args.model or ai_gateway.configured_models()["text"]      # production's own text model
        known = [node["id"] for node in ollama_nodes.configured_nodes()]
        order = [n for n in (args.order.split(",") if args.order else ORDER) if n in known]
        order += [n for n in known if n not in order]
        out.update(read_email(text, model=model, order=order))
    print(json.dumps(out))
    sys.stdout.flush()
    os._exit(0)


def _screenshot_names(cs, clean, cid: str, people, inventory: dict, primary: dict, audit: dict,
                      misdated: frozenset = frozenset()) -> list[str]:
    """Companies both screenshot models read and quoted off this record's own invite screenshots. The image the
    auto-booking renders from the email is the email again, not a second source, and is left out, as is a screenshot
    whose date is not the booked date (`misdated`: short hashes the screenshot backfill marked date_mismatch)."""
    names = []
    for shot in (inventory.get("rows", {}).get(cid) or {}).get("shots") or []:
        if shot["sha"][:8] in misdated:
            continue
        got = cs.get_attachment(cid, shot["pid"], cs.AttachmentType.SLOT_SCREENSHOT_PROOF)
        original = str((got[1] if got else {}).get("original_name") or "").lower()
        if original.startswith("auto-booking-evidence"):
            continue
        first, second = primary.get(shot["sha"]) or {}, audit.get(shot["sha"]) or {}
        name = clean(first.get("company") or "", people=people)
        other = clean(second.get("company") or "", people=people)
        if name and first.get("verified") is True and other and second.get("verified") is True and core.compatible(name, other):
            names.append(name)
    return names


def _known_companies(cs, clean) -> list[str]:
    """Every company a record already carries, as a list of names an email may name besides its employer."""
    seen = {}
    for row in cs._load(force=True)["candidates"]:
        name = clean(row.get("interview_company") or "", people=_people(row))
        if name and len(core.alnum(name)) >= 4:
            seen.setdefault(core.company_key(name), name)
    return sorted(seen.values())


def cmd_plan(args) -> int:
    from core.db.connection import get_connection
    cs, clean = _store()
    collected = core._load_json(args.collected)
    primary, audit = core._load_results(args.results, core.PRIMARY), core._load_results(args.results, core.AUDIT)
    shots_inventory = core._load_json(args.shots_inventory) if args.shots_inventory else {}
    shots_plan = core._load_json(args.shots_plan) if args.shots_plan else {"items": []}
    misdated = {cid: frozenset(e["sha"] for e in item.get("evidence") or [] if e.get("kind") == "date_mismatch")
                for cid, item in ((i["cid"], i) for i in shots_plan["items"])}
    shots_primary = core._load_results(args.shots_results, core.PRIMARY) if args.shots_results else {}
    shots_audit = core._load_results(args.shots_results, core.AUDIT) if args.shots_results else {}
    live = {str(r.get("id") or ""): r for r in cs._load(force=True)["candidates"]}
    known = _known_companies(cs, clean)
    items = []
    with get_connection() as conn, conn.cursor() as cur:
        _read_only(cur)
        named = {key: companies_named_in(_email_text_by_key(cur, key) or "", known) for key in collected["emails"]}
    for cid, row in collected["rows"].items():
        people = _people(live.get(cid) or {})
        row_clean = lambda value, people=people: clean(value, people=people)   # noqa: E731
        others = {link["key"]: named.get(link["key"], []) for link in row["links"]}
        decision = decide_row(row, primary, audit, clean=row_clean,
                              screenshot_names=_screenshot_names(cs, clean, cid, people, shots_inventory, shots_primary, shots_audit,
                                                                 misdated.get(cid, frozenset())),
                              other_companies_in=others)
        items.append({"cid": cid, **decision})
    plan = {"generated_at": core._now(), "source": "email", "items": items,
            "summary": dict(collections.Counter(i["action"] for i in items))}
    json.dump(plan, open(args.out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({"summary": plan["summary"], "totals": summarise_plan(plan)}))
    return 0


def cmd_review(args) -> int:
    from core.db.connection import get_connection
    cs, _ = _store()
    plan = core._load_json(args.plan)
    collected = core._load_json(args.collected)
    rows = {str(r.get("id") or ""): r for r in cs._load(force=True)["candidates"]}
    print("\t".join(("candidate_id", "candidate", "date", "time", "recorded_company", "suggestion", "reason",
                     "sender_domains", "email_subjects")))
    with get_connection() as conn, conn.cursor() as cur:
        _read_only(cur)
        for item in plan["items"]:
            if BUCKET_OF.get(item["action"]) not in ("review", "failed"):
                continue
            row = rows.get(item["cid"]) or {}
            subjects = []
            for link in collected["rows"][item["cid"]]["links"]:
                cur.execute("SELECT subject FROM mailbox_messages WHERE id=%s", (link["key"],))
                found = cur.fetchone()
                if found and found[0]:
                    subjects.append(found[0])
            print("\t".join(core._norm(v) for v in (
                item["cid"], row.get("name"), row.get("date"), row.get("time"), item["before"], item.get("suggestion"),
                REVIEW_REASONS.get(item["action"], item["action"]), ", ".join(item.get("sender_domains") or []),
                " | ".join(dict.fromkeys(subjects)))))
    return 0


def cmd_report(args) -> int:
    print(json.dumps(summarise_plan(core._load_json(args.plan))))
    return 0


# ---------------------------------------------------------------------------------------------
# Host side: the resumable loop
# ---------------------------------------------------------------------------------------------

def _plan_in_container(args, log) -> int:
    work = args.work
    inputs = {"collected.json": "/tmp/bfm-collected.json", args.results: "/tmp/bfm-results.jsonl"}
    if args.shots_work:
        inputs[os.path.join(args.shots_work, "inventory.json")] = "/tmp/bfm-shots-inventory.json"
        inputs[os.path.join(args.shots_work, "results.jsonl")] = "/tmp/bfm-shots-results.jsonl"
        inputs[os.path.join(args.shots_work, "plan.json")] = "/tmp/bfm-shots-plan.json"
    try:
        for local, inside in inputs.items():
            core._docker("cp", "-L", os.path.join(work, local), f"{core.CONTAINER}:{inside}", timeout=60).check_returncode()
        argv = ["exec", "-w", "/app", core.CONTAINER, "python", IN_CONTAINER, "plan", "--collected", inputs["collected.json"],
                "--results", inputs[args.results], "--out", "/tmp/bfm-plan.json"]
        if args.shots_work:
            argv += ["--shots-inventory", "/tmp/bfm-shots-inventory.json", "--shots-results", "/tmp/bfm-shots-results.jsonl",
                     "--shots-plan", "/tmp/bfm-shots-plan.json"]
        run = core._docker(*argv, timeout=900)
        if run.returncode != 0:
            log(f"plan failed: {run.stderr[-300:]}")
            return 1
        core._docker("cp", f"{core.CONTAINER}:/tmp/bfm-plan.json", os.path.join(work, "plan.json"), timeout=60).check_returncode()
        log("plan written: " + run.stdout.strip().splitlines()[-1])
        return 0
    finally:
        core._docker("exec", core.CONTAINER, "rm", "-f", *inputs.values(), "/tmp/bfm-plan.json", timeout=60)


def cmd_drive(args) -> int:
    work = args.work
    collected = core._load_json(os.path.join(work, "collected.json"))
    results_path = os.path.join(work, args.results)
    log_path = os.path.join(work, "driver.log")
    stop_file = os.path.join(work, "STOP")
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00")) if args.until else None
    here = os.path.abspath(__file__)

    def log(message: str):
        line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {message}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    queue = sorted(collected["emails"].values(), key=lambda e: e["sha"])
    per_reading = 3 * core.CALL_TIMEOUT + 120
    outages = 0
    log(f"driving {len(queue)} emails of {len(collected['rows'])} records; audit model {args.audit_model} on {args.order}")
    while True:
        if os.path.exists(stop_file):
            log("STOP file present: stopping cleanly (run drive again to resume)")
            return 0
        if until and datetime.now(timezone.utc) >= until:
            log(f"reached {args.until}: stopping cleanly (run drive again to resume)")
            return 0
        primary, audit = core._load_results(results_path, core.PRIMARY), core._load_results(results_path, core.AUDIT)
        tasks = core.pending_tasks(queue, primary, audit)
        if not tasks:
            log(f"every email has its outcome: {len(queue)} read, {len(audit)} audited")
            return _plan_in_container(args, log) if args.then_plan else 0
        item, stage = tasks[0]
        waited = 0
        while core.live_ai_requests_in_last(args.courtesy_seconds) and waited < 1800:
            if waited == 0:
                log("a person is using the AI right now: waiting for it to go quiet")
            time.sleep(30)
            waited += 30
        copied = [core._docker("cp", src, f"{core.CONTAINER}:{dst}", timeout=60).returncode
                  for src, dst in ((core.__file__, CORE_IN_CONTAINER), (here, IN_CONTAINER))]
        if any(copied):
            log("the container is not there (a deploy or a restart): waiting 60 s; not an attempt")
            time.sleep(60)
            continue
        try:
            run = core._docker("exec", "-w", "/app", core.CONTAINER, "python", IN_CONTAINER, "read", "--key", item["sha"],
                               "--stage", stage, "--model", args.audit_model if stage == core.AUDIT else args.model,
                               "--order", args.order, timeout=per_reading)
        except subprocess.TimeoutExpired:
            line = {"sha": item["sha"], "stage": stage, "at": core._now(), "status": "failed", "error": "driver timeout"}
        else:
            last = next((out for out in reversed(run.stdout.splitlines()) if out.startswith("{")), "")
            if not last:
                log(f"no answer from the container ({run.returncode}): waiting 60 s; not an attempt")
                time.sleep(60)
                continue
            line = json.loads(last)
        line["attempt_at_driver"] = core._now()
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if line.get("status") == "unavailable":
            pause = core.OUTAGE_BACKOFF[min(outages, len(core.OUTAGE_BACKOFF) - 1)]
            outages += 1
            log(f"no AI node can be reached for the {stage} model ({line.get('error', '')[:160]}): waiting {pause} s; not an attempt")
            time.sleep(pause)
            continue
        outages = 0
        note = (f"company={'yes' if line.get('company') else 'no'} quoted={line.get('verified')} node={line.get('node')}"
                if line.get("status") == "ok" else line.get("error", line.get("status")))
        log(f"{stage} {item['sha'][:8]} {line.get('status')} {line.get('seconds')}s {note} | {len(tasks) - 1} readings left")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    p = sub.add_parser("read")
    p.add_argument("--key", required=True); p.add_argument("--stage", choices=(core.PRIMARY, core.AUDIT), default=core.PRIMARY)
    p.add_argument("--model", default="", help="empty = production's configured text model")
    p.add_argument("--order", default=",".join(ORDER))
    p = sub.add_parser("plan")
    p.add_argument("--collected", required=True); p.add_argument("--results", required=True); p.add_argument("--out", required=True)
    p.add_argument("--shots-inventory", default=""); p.add_argument("--shots-results", default="")
    p.add_argument("--shots-plan", default="", help="the screenshot backfill's plan: its date_mismatch screenshots support nothing")
    p = sub.add_parser("review")
    p.add_argument("--plan", required=True); p.add_argument("--collected", required=True)
    p = sub.add_parser("report")
    p.add_argument("--plan", required=True)
    p = sub.add_parser("drive")
    p.add_argument("--work", required=True); p.add_argument("--until", default="")
    p.add_argument("--courtesy-seconds", type=int, default=150); p.add_argument("--results", default="results.jsonl")
    p.add_argument("--model", default=""); p.add_argument("--audit-model", default=AUDIT_MODEL)
    p.add_argument("--order", default=",".join(ORDER))
    p.add_argument("--shots-work", default="", help="the screenshot backfill's work directory, for screenshot corroboration")
    p.add_argument("--then-plan", action="store_true")
    args = parser.parse_args(argv)
    return {"collect": cmd_collect, "read": cmd_read, "plan": cmd_plan, "review": cmd_review, "report": cmd_report,
            "drive": cmd_drive}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
