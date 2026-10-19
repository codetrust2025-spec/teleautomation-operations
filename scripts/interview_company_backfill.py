#!/usr/bin/env python3
"""Fill or correct `interview_company` on historical interview records from their stored invite screenshots.

Records booked before the booking form read the company off the invite (0ff815e) have an invite
screenshot attached and no company, or a value that is not a company ("Contract"). This tool reads
every stored screenshot, twice over with two different vision models, and records the company only
where both read and quote it. Nothing else on a record is touched, nothing is deleted, a company
already recorded is never replaced, and ambiguity is left for review rather than guessed.

Steps (each a sub-command; the ones marked [container] run inside the operations-api container):

  inventory  [container]  every row, its attached screenshots, which files exist, sha256 per file
  read       [container]  ONE screenshot, ONE stage (primary or audit model) -> one JSON line (no writes)
  plan       [container]  inventory + readings -> what would change and why (no writes)
  review     [container]  the plan's uncertain records, with who/when and the screenshot links (no writes)
  report     [container]  scanned / updated / skipped / failed / pending / review totals (no writes)
  snapshot   [container]  the before-state of the whole candidate store, as JSON lines
  apply      [container]  write the planned companies, one targeted field per row, read back (needs --apply)
  verify     [container]  live rows against the before-state: only the intended fields changed
  rollback   [container]  put back what apply wrote, where nobody has changed it since (needs --apply)
  drive      [host]       the resumable loop: every file through both stages, a checkpoint line per reading

Reading. A question goes to the first AI node in its order that carries the model and answers; a node
that is down or fails mid-answer is passed over for the next one. When no node can be reached at all
that is an outage, not a reading: it is recorded, never counted as one of the file's attempts, and the
driver waits and tries again. A file whose readings fail MAX_ATTEMPTS times is unreadable and goes to
review. The driver survives deploys (it re-copies itself into the new container) and restarts of its
own (every reading is a checkpoint line), so running it again resumes where it stopped.

Privacy: terminal output carries counts, company names and short file hashes. Candidate ids, names
and payloads are written only to the root-only work directory on the host.
"""
from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.getcwd())  # `docker exec` starts in the application directory

CONTAINER = "teleautomation-production-operations-api-1"
IN_CONTAINER = "/tmp/bf.py"
MAX_ATTEMPTS = 3          # readings per file that ended in failure before it is called unreadable
# Seconds for one question on one node. The slowest node reads on the CPU in about 140 s; a hung node is given up
# after this and the next node in the order is asked.
CALL_TIMEOUT = 240
IMAGE_TOKEN_BUDGET = 1200  # the image is scaled DOWN (never up) to about this many visual tokens; 800 invented companies, 1200 did not
BOOKKEEPING = {"updated_at", "_store_updated_at"}   # keys a targeted row write refreshes by itself
ACTIONS_THAT_WRITE = ("fill", "correct_invalid")
PRIMARY, AUDIT = "primary", "audit"
# The second model, a different one from production's, so agreement is two readings and not one read twice. It is
# kept off the RTX node while another node can serve it: that node holds production's vision model in its 8 GB of
# VRAM, and loading this one there would evict it from under the next candidate's invite read.
AUDIT_MODEL = "qwen2.5vl:7b"
AUDIT_ORDER = ("jagadeesh", "our_machine", "rtx4060")
OUTAGE_BACKOFF = (60, 120, 300, 600)  # seconds the driver waits after successive "no node can be reached" readings


# ---------------------------------------------------------------------------------------------
# Pure logic (unit-tested; no application imports)
# ---------------------------------------------------------------------------------------------

_LEGAL = {"ltd", "limited", "pvt", "private", "inc", "incorporated", "llp", "llc", "corp", "corporation", "co", "company"}


def alnum(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def company_key(text: str) -> str:
    """The name without case, spacing, punctuation or legal suffixes: 'Wipro Ltd.' == 'WIPRO'."""
    words = [w for w in re.split(r"[^a-z0-9]+", str(text or "").lower()) if w]
    while words and words[-1] in _LEGAL:
        words.pop()
    return "".join(words)


def compatible(a: str, b: str) -> bool:
    """Same company, or one is the fuller form of the other ('Capgemini' / 'Capgemini Technology Services')."""
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= 5 and long_.startswith(short)


def grounded(company: str, raw_text: str) -> bool:
    """The company is written in the reader's own transcription of the screenshot (spacing and case aside)."""
    key = alnum(company)
    return len(key) >= 3 and key in alnum(raw_text)


VERIFY_PROMPT = (
    "Look at this screenshot of an interview invite.\n"
    "Question: is the organisation named \"{company}\" written anywhere in the image, as the company the interview is "
    "with or for (the hiring company, the end client or the interviewing organisation)?\n"
    "Return ONLY valid JSON: {{\"found\": true or false, \"quote\": \"the exact words from the image that name it, at most "
    "120 characters, empty if not found\", \"role\": \"employer, client, vendor, sender or other\"}}\n"
    "Rules: do not guess. If the name is not actually written in the image, found is false and quote is empty. "
    "A name that appears only inside an email address or a web address does not count."
)


# The company is asked for AFTER the context fields on purpose. A prompt that asked for the company first read like the
# original method on only 12 of 21 invites; with the platform, round and technology read first it matched the original on
# 17 of the 18 it found, with no conflict. (Benchmarked 10 Oct on the same files; a list-every-organisation prompt was worse.)
SLIM_PROMPT = (
    "Read this interview invite screenshot carefully. Return ONLY valid JSON, no markdown:\n"
    '{"is_interview_invite": true, "meeting_platform": "", "interview_round": "", "technology": "", "company": "", '
    '"company_quote": "", "interview_date": ""}\n'
    "- is_interview_invite: false if the image is not an invitation to an interview (for example a payment receipt or an "
    "unrelated chat), otherwise true.\n"
    "- meeting_platform, interview_round, technology: as written in the invite, empty if not written.\n"
    "- company: the organisation the interview is for (the hiring company or end client), exactly as the invite writes it. "
    "NOT the meeting platform (Teams, Zoom, HirePro, FloCareer...), a person, a job role, an email address or a URL. "
    "Empty if the invite does not name one. Do not guess, and do not infer it from an email domain or a logo.\n"
    "- company_quote: the exact words from the image that name the company (at most 100 characters). Empty if company is empty.\n"
    "- interview_date: YYYY-MM-DD, only if a date is written in the invite; otherwise empty."
)


def slim_result_fields(first: dict | None, answer: dict | None, *, scale: float, seconds_first: float, seconds_second: float) -> dict:
    """What is kept of a faster-method file: the claim, whether the SECOND question confirmed it with a quote, the date.

    The first call's own quote is recorded (`quote1`) but does not count: a model that invents a company also invents
    the words it quotes for it. Only the second, separate question on the same image can confirm.
    """
    first = first or {}
    company = str(first.get("company") or "").strip()
    invite = first.get("is_interview_invite")
    out = {
        "status": "not_an_invite" if invite is False else "ok",
        "company": company if invite is not False else "",
        "grounded": False,
        "quote1": bool(company) and quote_supports(company, {"found": True, "quote": first.get("company_quote")}),
        "verified": quote_supports(company, answer) if answer is not None else None,
        "verify_role": str((answer or {}).get("role") or "")[:20],
        "quote_len": len(str((answer or {}).get("quote") or "")),
        "raw_text_len": 0,
        "date_raw": str(first.get("interview_date") or ""),
        "confidence_score": 0,
        "method": "slim+quote",
        "model": "",
        "node": "",
        "scale": round(scale, 2),
        "seconds_first": round(seconds_first, 1),
        "seconds_second": round(seconds_second, 1),
    }
    return out


def quote_supports(company: str, answer: dict | None) -> bool:
    """The model found the name AND the words it quotes from the image contain it (it cannot just say yes)."""
    if not isinstance(answer, dict) or answer.get("found") is not True:
        return False
    key = alnum(company)
    return len(key) >= 3 and key in alnum(str(answer.get("quote") or ""))


def date_relation(raw_date: str, row_date: str) -> str:
    """'match', 'match_day_month' (right day and month, guessed year), 'mismatch' or 'unknown' against the booked date."""
    def parse(value: str):
        try:
            return date.fromisoformat(str(value or "").strip()[:10])
        except ValueError:
            return None
    seen, booked = parse(raw_date), parse(row_date)
    if seen is None or booked is None:
        return "unknown"
    # Invites usually omit the year and the model guesses one (on 10 Oct, 18 of 25 "mismatches" were the right day
    # and month with the year 3 out). The day and month are what the invite actually shows.
    if seen == booked:
        return "match"
    return "match_day_month" if (seen.month, seen.day) == (booked.month, booked.day) else "mismatch"


def prepare_image(data: bytes, budget: int = IMAGE_TOKEN_BUDGET) -> tuple[bytes, float]:
    """The image to send: scaled DOWN to about `budget` visual tokens (one per 32x32 px), never up. The stored file is not touched.

    The node reads an image at roughly 37 tokens a second on the CPU, so a 2,900-token screenshot costs a minute
    before a word is generated. 800 tokens made the model invent companies; 1,200 read like the original.
    """
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        tokens = math.ceil(image.width / 32) * math.ceil(image.height / 32)
        if tokens <= budget:
            return data, 1.0
        scale = math.sqrt(budget * 1024 / (image.width * image.height))
        small = image.convert("RGB").resize((max(64, int(image.width * scale)), max(64, int(image.height * scale))), Image.LANCZOS)
        buffer = io.BytesIO()
        small.save(buffer, "PNG", optimize=True)
        return buffer.getvalue(), scale


def collapse_results(lines: list[dict]) -> dict[str, dict]:
    """The checkpoint file holds every attempt; this is the standing outcome per file.

    A reading that succeeded wins over any earlier failure; otherwise the latest failure stands with
    the number of failures so far, so a file is retried until MAX_ATTEMPTS and then called unreadable.
    An outage ("unavailable": no node could be reached) is not a reading of the file and changes nothing.
    """
    standing: dict[str, dict] = {}
    for line in lines:
        sha = line.get("sha")
        if not sha or line.get("status") == "unavailable":
            continue
        previous = standing.get(sha)
        if line.get("status") == "failed":
            attempts = (previous or {}).get("failures", 0) + 1
            if previous and previous.get("status") not in (None, "failed"):
                continue
            standing[sha] = {**line, "failures": attempts}
        else:
            standing[sha] = {**line, "failures": (previous or {}).get("failures", 0)}
    return standing


def evidence_for(row: dict, shot: dict, result: dict | None, *, grounding: str = "required", cleaner=None,
                 require_quote: bool = False) -> tuple[str, str]:
    """(kind, company) for one screenshot of one row. kind 'usable' is the only one that can write.

    `cleaner` re-checks the claim whoever made the reading: four early readings said "Unknown company" and a
    quote check alone confirmed them, because the model quoted its own placeholder."""
    if result is None:
        return "pending", ""
    status = result.get("status")
    if status == "failed":
        return ("unreadable" if result.get("failures", 0) >= MAX_ATTEMPTS else "pending"), ""
    if status == "payment_screenshot":
        return "payment_screenshot", ""
    if status == "not_an_invite":
        return "not_an_invite", ""
    company = str(result.get("company") or "").strip()
    if company and cleaner is not None:
        company = cleaner(company)
    if not company:
        return "no_company", ""
    # require_quote: only a separate question answered with words quoted from the image counts; the reader's own
    # transcription containing the name (grounded) is not enough on its own.
    supported = result.get("verified") if require_quote else (result.get("grounded") or result.get("verified"))
    if grounding == "required" and not supported:
        return "not_confirmed", company
    if date_relation(result.get("date_raw", ""), row.get("date", "")) == "mismatch":
        return "date_mismatch", company
    return "usable", company


def audit_verdict(row: dict, candidate: str, audit: dict[str, dict], *, cleaner) -> str:
    """What a second, different model says about a company about to be written: 'confirmed', 'conflict' or 'unconfirmed'.

    The audit model reads the same screenshots independently (its own read, then its own quote check). A write stays only
    when it names a compatible company; a different company is a conflict; nothing found, or not yet read, is unconfirmed.
    """
    seen_other = False
    for shot in row.get("shots") or []:
        result = audit.get(shot["sha"])
        if not result or result.get("status") != "ok":
            continue
        company = cleaner(result.get("company") or "")
        if not company or result.get("verified") is not True:     # the second model must quote the words too
            continue
        if compatible(company, candidate):
            return "confirmed"
        seen_other = True
    return "conflict" if seen_other else "unconfirmed"


def decide_row(row: dict, results: dict[str, dict], *, cleaner, grounding: str = "required", audit: dict[str, dict] | None = None,
               require_quote: bool = False) -> dict:
    """The outcome for one record. `cleaner` is features.ollama_invite_extract.clean_company_name.

    With `audit` (the second model's readings) a fill or correction is only kept when that model independently agrees.
    """
    decided = _decide_row(row, results, cleaner=cleaner, grounding=grounding, require_quote=require_quote)
    if decided["action"] in ACTIONS_THAT_WRITE:
        decided["date_basis"] = date_basis(row, decided["after"], results, cleaner=cleaner)
    if audit is not None and decided["action"] in ACTIONS_THAT_WRITE:
        verdict = audit_verdict(row, decided["after"], audit, cleaner=cleaner)
        decided["audit"] = verdict
        if verdict != "confirmed":
            decided["action"] = "review_second_model_disagrees" if verdict == "conflict" else "review_second_model_not_confirmed"
        elif not year_guess_confirmed(row, decided["after"], results, audit, cleaner=cleaner):
            decided["action"] = "review_year_not_confirmed"
    return decided


def date_basis(row: dict, candidate: str, results: dict[str, dict], *, cleaner) -> str:
    """How the screenshot is tied to this booking by date. An inferred year is never called screenshot-verified.

    exact_date: the screenshot shows the booked date. day_month_year_from_record: it shows the day and month; the year is
    the record's own booked date, not something read off the image. no_date_on_screenshot: no date was read; the link is
    the attachment alone.
    """
    relations = set()
    for shot in row.get("shots") or []:
        result = results.get(shot["sha"])
        if not result or result.get("status") != "ok":
            continue
        company = cleaner(result.get("company") or "")
        if company and compatible(company, candidate):
            relations.add(date_relation(result.get("date_raw", ""), row.get("date", "")))
    if "match" in relations:
        return "exact_date"
    if "match_day_month" in relations:
        return "day_month_year_from_record"
    return "no_date_on_screenshot"


def year_guess_confirmed(row: dict, candidate: str, results: dict[str, dict], audit: dict[str, dict], *, cleaner) -> bool:
    """A record accepted on a guessed year needs the second model to read the same day and month off the screenshot.

    Only applies when none of the screenshots behind the company matched the booked date exactly and at least one matched
    only on day and month. The second model's own transcription of the date must then match on day and month too;
    a different day, or no date at all, is not a confirmation.
    """
    relations = {}
    for shot in row.get("shots") or []:
        result = results.get(shot["sha"])
        if not result or result.get("status") != "ok":
            continue
        company = cleaner(result.get("company") or "")
        if company and compatible(company, candidate):
            relations[shot["sha"]] = date_relation(result.get("date_raw", ""), row.get("date", ""))
    if "match" in relations.values() or "match_day_month" not in relations.values():
        return True
    for sha, relation in relations.items():
        if relation == "match_day_month":
            second = audit.get(sha) or {}
            if date_relation(second.get("date_raw", ""), row.get("date", "")) in ("match", "match_day_month"):
                return True
    return False


def _decide_row(row: dict, results: dict[str, dict], *, cleaner, grounding: str = "required", require_quote: bool = False) -> dict:
    before = str(row.get("company") or "").strip()
    shots = row.get("shots") or []
    evidence = []
    for shot in shots:
        kind, company = evidence_for(row, shot, results.get(shot["sha"]), grounding=grounding, cleaner=cleaner, require_quote=require_quote)
        evidence.append({"sha": shot["sha"][:8], "kind": kind, "company": company})
    base = {"before": before, "after": "", "evidence": evidence}
    if not shots:
        reason = "file_missing" if row.get("n_entries") else "no_screenshot"
        return {**base, "action": reason}
    kinds = [e["kind"] for e in evidence]
    if "pending" in kinds:
        return {**base, "action": "pending"}
    usable = [e["company"] for e in evidence if e["kind"] == "usable"]
    distinct: list[str] = []
    for name in usable:
        if not any(compatible(name, kept) for kept in distinct):
            distinct.append(name)
    if len(distinct) > 1:
        return {**base, "action": "review_conflict_between_screenshots"}
    candidate = ""
    if usable:  # the fullest of the compatible spellings, as the screenshot wrote it
        candidate = max(usable, key=lambda name: len(company_key(name)))
    before_valid = bool(cleaner(before)) if before else False
    if not candidate:
        for kind, action in (("date_mismatch", "review_date_mismatch"), ("not_confirmed", "review_company_not_confirmed")):
            if kind in kinds:
                return {**base, "action": action}
        if "unreadable" in kinds:
            return {**base, "action": "unreadable"}
        if "payment_screenshot" in kinds or "not_an_invite" in kinds:
            return {**base, "action": "not_an_invite"}
        if before and not before_valid:
            return {**base, "action": "invalid_unresolved"}
        return {**base, "action": "kept_existing" if before else "no_company_visible"}
    if not before:
        return {**base, "action": "fill", "after": candidate}
    if not before_valid:
        return {**base, "action": "correct_invalid", "after": candidate}
    if compatible(before, candidate):
        return {**base, "action": "already_correct"}
    return {**base, "action": "review_conflict_with_existing", "after": candidate}


def build_plan(inventory: dict, results: dict[str, dict], *, cleaner, grounding: str = "required", audit: dict[str, dict] | None = None,
               require_quote: bool = False, people: dict[str, tuple[str, ...]] | None = None) -> dict:
    """`people`, per record, are the names its company must not be (the candidate's, the attendee's)."""
    items = []
    for cid, row in inventory["rows"].items():
        names = tuple((people or {}).get(cid) or ())
        row_cleaner = (lambda value, names=names: cleaner(value, people=names)) if names else cleaner
        decision = decide_row(row, results, cleaner=row_cleaner, grounding=grounding, audit=audit, require_quote=require_quote)
        items.append({"cid": cid, **decision})
    summary = collections.Counter(item["action"] for item in items)
    return {"grounding": grounding, "audited": audit is not None, "require_quote": require_quote, "generated_at": _now(),
            "items": items, "summary": dict(summary)}


# What each decision means for the report. Every action decide_row can return is in exactly one bucket.
BUCKETS = {
    "to_update": ("fill", "correct_invalid"),
    "skipped": ("already_correct", "kept_existing", "no_company_visible", "not_an_invite"),
    "review": ("review_conflict_between_screenshots", "review_conflict_with_existing", "review_date_mismatch",
               "review_company_not_confirmed", "review_second_model_disagrees", "review_second_model_not_confirmed",
               "review_year_not_confirmed", "invalid_unresolved"),
    "failed": ("unreadable",),
    "pending": ("pending",),
    "not_eligible": ("no_screenshot", "file_missing"),
}
BUCKET_OF = {action: bucket for bucket, actions in BUCKETS.items() for action in actions}


def summarise_plan(plan: dict, written: int | None = None) -> dict:
    """The totals a backfill is reported by. `scanned` is every record with a screenshot that could be read."""
    buckets: collections.Counter = collections.Counter()
    for item in plan.get("items") or []:
        buckets[BUCKET_OF.get(item.get("action"), "unknown")] += 1
    eligible = sum(n for bucket, n in buckets.items() if bucket != "not_eligible")
    out = {
        "records": sum(buckets.values()),
        "eligible_with_screenshot": eligible,
        "scanned": eligible - buckets["pending"],
        "to_update": buckets["to_update"],
        "skipped": buckets["skipped"],
        "review": buckets["review"],
        "failed": buckets["failed"],
        "pending": buckets["pending"],
        "not_eligible": buckets["not_eligible"],
    }
    if buckets["unknown"]:
        out["unknown"] = buckets["unknown"]
    if written is not None:
        out["updated"] = written
    return out


def needs_audit(primary: dict | None) -> bool:
    """Only a company the production model read AND quoted is put to the second model; nothing else can be written."""
    return bool(primary) and primary.get("status") == "ok" and bool(str(primary.get("company") or "").strip()) \
        and primary.get("verified") is True


def _due(standing: dict | None) -> bool:
    return standing is None or (standing.get("status") == "failed" and standing.get("failures", 0) < MAX_ATTEMPTS)


def pending_tasks(files: list[dict], primary: dict[str, dict], audit: dict[str, dict]) -> list[tuple[dict, str]]:
    """What is left to read, in the order to read it: every file's first primary reading, then primary retries,
    then the audit readings (first attempts, then retries). An empty list means every file has its outcome."""
    first = [(f, PRIMARY) for f in files if f["sha"] not in primary]
    retry = [(f, PRIMARY) for f in files if f["sha"] in primary and _due(primary[f["sha"]])]
    audits = [f for f in files if needs_audit(primary.get(f["sha"])) and _due(audit.get(f["sha"]))]
    audits.sort(key=lambda f: f["sha"] in audit)
    return first + retry + [(f, AUDIT) for f in audits]


# ---------------------------------------------------------------------------------------------
# Container side
# ---------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _app():
    from features import candidate_store as cs
    from features import ollama_invite_extract as extractor
    return cs, extractor


def _sniff_mime(data: bytes, declared: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return declared or "image/jpeg"


def cmd_inventory(_args) -> int:
    cs, _ = _app()
    rows = cs._load(force=True)["candidates"]   # not list_candidates(): it applies filters and may write
    files: dict[str, dict] = {}
    out_rows: dict[str, dict] = {}
    missing = []
    for row in rows:
        cid = str(row.get("id") or "").strip()
        if not cid:
            continue
        entries = cs.partition_candidate_attachments(row)["slot_screenshot_proofs"]
        primary = str(row.get("slot_screenshot_proof_id") or "")
        shots = []
        for entry in entries:
            pid = str(entry.get("id") or "")
            got = cs.get_attachment(cid, pid, cs.AttachmentType.SLOT_SCREENSHOT_PROOF)
            path = got[0] if got else ""
            if not (path and os.path.isfile(path)):
                missing.append({"cid": cid, "pid": pid, "uploaded_at": str(entry.get("uploaded_at") or ""),
                                "legacy": bool(entry.get("legacy_storage"))})
                continue
            data = open(path, "rb").read()
            sha = hashlib.sha256(data).hexdigest()
            info = files.setdefault(sha, {"sha": sha, "path": path, "size": len(data),
                                          "mime": _sniff_mime(data, str(entry.get("mime_type") or "")), "uploads": []})
            info["uploads"].append({"cid": cid, "pid": pid, "uploaded_at": str(entry.get("uploaded_at") or "")})
            shots.append({"sha": sha, "pid": pid, "primary": pid == primary, "uploaded_at": str(entry.get("uploaded_at") or "")})
        out_rows[cid] = {"company": str(row.get("interview_company") or ""), "date": str(row.get("date") or ""),
                         "time": str(row.get("time") or ""), "slot_confirmed": bool(row.get("slot_confirmed")),
                         "n_entries": len(entries), "shots": shots}
    print(json.dumps({"generated_at": _now(), "rows": out_rows, "files": files, "missing": missing}))
    return 0


def _model_on(model: str, installed: list[str]) -> bool:
    wanted = model.removesuffix(":latest")
    return any(str(name or "").removesuffix(":latest") == wanted for name in installed)


def ask_nodes(model: str, b64: str | None, prompt: str, order: list[str], *, timeout: int = CALL_TIMEOUT,
              request=None, parse=None) -> dict:
    """One question (about one image, or about text alone when `b64` is None) on the first node in `order` that
    carries `model` and answers it.

    -> {"answer", "node", "error", "unavailable", "tried"}. A node that is unreachable, lacks the model, or fails while
    answering (timeout, HTTP 500) is passed over for the next one. `unavailable` is True only when no node could even
    be reached with the model: an outage, not a reading. When a node took the question and every one failed, the
    reading failed. An answer that is not JSON is a failed reading too: asking elsewhere would be shopping for one.

    The request carries production's own context window, so a model already loaded for live reads is not reloaded.
    """
    from core import ai_gateway, ollama_nodes
    if request is None:
        request = ollama_nodes._request
    if parse is None:
        from features.ollama_invite_extract import parse_strict_json_response as parse
    tried: list[str] = []
    reached = False
    for node in order:
        try:
            tags = request(node, "/api/tags", timeout=10)
        except Exception as exc:  # noqa: BLE001 - the next node is asked
            tried.append(f"{node}: unreachable ({type(exc).__name__})")
            continue
        if not _model_on(model, [m.get("name") for m in (tags or {}).get("models") or []]):
            tried.append(f"{node}: no {model}")
            continue
        reached = True
        message = {"role": "user", "content": prompt, **({"images": [b64]} if b64 else {})}
        payload = {"model": model, "stream": False, "keep_alive": (os.getenv("OLLAMA_KEEP_ALIVE") or "30m").strip(),
                   "format": "json", "messages": [message],
                   "options": {"temperature": 0.1, "num_predict": 200, "num_ctx": ai_gateway.resolved_num_ctx()}}
        if model.startswith("qwen3"):
            payload["think"] = False      # only models that know the option are sent it
        try:
            out = request(node, "/api/chat", method="POST", payload=payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - the next node is asked
            tried.append(f"{node}: {type(exc).__name__}: {str(exc)[:60]}")
            continue
        text = ((out or {}).get("message") or {}).get("content") or ""
        answer = parse(text) if text else None
        if not isinstance(answer, dict):
            return {"answer": None, "node": node, "error": "no parseable answer", "unavailable": False, "tried": tried}
        return {"answer": answer, "node": node, "error": "", "unavailable": False, "tried": tried}
    return {"answer": None, "node": "", "error": "; ".join(tried)[:240] or "no node configured",
            "unavailable": not reached, "tried": tried}


def read_stage(data: bytes, *, model: str, order: list[str], ask=ask_nodes, clean=None) -> dict:
    """One stage of one file. Question one reads the company (and says whether this is an invite at all); question
    two, asked about the very same image bytes and first of the node that answered question one (it reuses the image it
    has just read), must quote the words that name it."""
    if clean is None:
        from features.ollama_invite_extract import clean_company_name as clean
    prepared, scale = prepare_image(data)
    b64 = base64.b64encode(prepared).decode("ascii")
    started = time.time()
    first = ask(model, b64, SLIM_PROMPT, list(order))
    seconds_first = time.time() - started
    if first["unavailable"]:
        return {"status": "unavailable", "error": first["error"]}
    if first["error"]:
        return {"status": "failed", "error": ("read: " + first["error"])[:240], "node": first["node"]}
    reading = dict(first["answer"])
    reading["company"] = clean(reading.get("company"))
    second, seconds_second = None, 0.0
    if reading["company"] and reading.get("is_interview_invite") is not False:
        again = [first["node"]] + [node for node in order if node != first["node"]]
        started = time.time()
        second = ask(model, b64, VERIFY_PROMPT.format(company=reading["company"].replace('"', "'")), again)
        seconds_second = time.time() - started
        if second["unavailable"]:
            return {"status": "unavailable", "error": second["error"]}
        if second["error"]:
            return {"status": "failed", "error": ("verification: " + second["error"])[:240], "node": second["node"]}
    out = slim_result_fields(reading, second["answer"] if second else None, scale=scale,
                             seconds_first=seconds_first, seconds_second=seconds_second)
    out.update(model=model, node=first["node"], node2=second["node"] if second else "")
    return out


def cmd_read(args) -> int:
    from core import ai_gateway, ollama_nodes
    data = open(args.path, "rb").read()
    out = {"sha": args.sha, "stage": args.stage, "at": _now()}
    if hashlib.sha256(data).hexdigest() != args.sha:
        out.update(status="failed", error="file does not match its recorded hash", seconds=0.0)
        print(json.dumps(out))
        return 0
    model = args.model or ai_gateway.configured_models()["vision"]       # production's own vision model
    order = [n for n in args.order.split(",") if n] if args.order else ollama_nodes.candidate_order(model)
    known = [node["id"] for node in ollama_nodes.configured_nodes()]
    order = [n for n in order if n in known] + [n for n in known if n not in order]   # every node is a fallback
    started = time.time()
    out.update(read_stage(data, model=model, order=order))
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out))
    sys.stdout.flush()
    os._exit(0)   # nothing a stalled connection left behind may keep the process alive


def _load_json(path: str):
    return json.load(open(path, encoding="utf-8"))


def _read_lines(path: str) -> list[dict]:
    lines = []
    if os.path.exists(path):
        for raw in open(path, encoding="utf-8"):
            raw = raw.strip()
            if raw:
                lines.append(json.loads(raw))
    return lines


def _load_results(path: str, stage: str | None = None) -> dict[str, dict]:
    """Standing outcome per file; with `stage`, of that stage only (a line without one is a primary reading)."""
    lines = _read_lines(path)
    if stage is not None:
        lines = [line for line in lines if line.get("stage", PRIMARY) == stage]
    return collapse_results(lines)


def _people_by_cid(cs) -> dict[str, tuple[str, ...]]:
    out = {}
    for row in cs._load(force=True)["candidates"]:     # not list_candidates(): it applies filters and may write
        cid = str(row.get("id") or "")
        if cid:
            out[cid] = tuple(str(row.get(key) or "") for key in ("name", "interview_attendee") if row.get(key))
    return out


def cmd_plan(args) -> int:
    """Both models must read and quote the company, and a company already recorded is never replaced."""
    cs, extractor = _app()
    inventory = _load_json(args.inventory)
    plan = build_plan(inventory, _load_results(args.results, PRIMARY), cleaner=extractor.clean_company_name,
                      grounding="required", audit=_load_results(args.results, AUDIT), require_quote=True,
                      people=_people_by_cid(cs))
    plan["missing_entries"] = len(inventory.get("missing") or [])
    plan["unique_files"] = len(inventory.get("files") or {})
    json.dump(plan, open(args.out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({"summary": plan["summary"], "totals": summarise_plan(plan),
                      "missing_entries": plan["missing_entries"], "unique_files": plan["unique_files"]}))
    return 0


REVIEW_REASONS = {
    "review_conflict_between_screenshots": "the screenshots name different companies",
    "review_conflict_with_existing": "the screenshot names a different company from the one recorded (kept)",
    "review_date_mismatch": "the screenshot's date is not the booked date",
    "review_company_not_confirmed": "the production model named a company but could not quote it",
    "review_second_model_disagrees": "the second model read a different company",
    "review_second_model_not_confirmed": "the second model could not read and quote the same company",
    "review_year_not_confirmed": "only day and month matched, and the second model did not read them",
    "invalid_unresolved": "the recorded value is not a company and no screenshot names one",
    "unreadable": "every reading of the screenshot failed",
}


def cmd_review(args) -> int:
    """The records left for a person: one TSV line each, with the screenshot links to open in Operations."""
    cs, _ = _app()
    plan = _load_json(args.plan)
    rows = {str(r.get("id") or ""): r for r in cs._load(force=True)["candidates"]}
    inventory = _load_json(args.inventory)
    print("\t".join(("candidate_id", "candidate", "date", "time", "recorded_company", "proposed_company",
                     "companies_read", "reason", "screenshots")))
    for item in plan["items"]:
        if BUCKET_OF.get(item["action"]) not in ("review", "failed"):
            continue
        row = rows.get(item["cid"]) or {}
        shots = (inventory["rows"].get(item["cid"]) or {}).get("shots") or []
        seen = sorted({e["company"] for e in item.get("evidence") or [] if e.get("company")})
        links = " ".join(f"/candidates/{item['cid']}/attachments/slot_screenshot_proof/{s['pid']}" for s in shots)
        print("\t".join(_norm(v) for v in (item["cid"], row.get("name"), row.get("date"), row.get("time"), item["before"],
                                           item.get("after"), "; ".join(seen), REVIEW_REASONS.get(item["action"], item["action"]),
                                           links)))
    return 0


def cmd_report(args) -> int:
    plan = _load_json(args.plan)
    written = None
    if args.applied:
        written = sum(1 for line in _read_lines(args.applied) if line.get("result") == "written")
    print(json.dumps(summarise_plan(plan, written)))
    return 0


def cmd_compare(args) -> int:
    from features.ollama_invite_extract import clean_company_name as clean
    inventory = _load_json(args.inventory)
    old, new = _load_results(args.old, PRIMARY), _load_results(args.new, PRIMARY)
    rows, files = inventory["rows"], inventory["files"]

    def accepted(result):
        company = clean(result.get("company") or "")
        return company if company and result.get("status") == "ok" and (result.get("grounded") or result.get("verified")) else ""

    def known(sha):
        names = sorted({rows[u["cid"]]["company"] for u in files[sha]["uploads"] if rows[u["cid"]]["company"]})
        return names[0] if names else ""

    cats: collections.Counter = collections.Counter()
    notes: dict = collections.defaultdict(list)
    seconds_old, seconds_new = [], []
    vs_record: collections.Counter = collections.Counter()
    for sha, result in new.items():
        if sha not in old or result.get("status") == "failed":
            continue
        a_old, a_new = accepted(old[sha]), accepted(result)
        seconds_old.append(old[sha].get("seconds") or 0)
        seconds_new.append(result.get("seconds") or 0)
        if a_old and a_new:
            cat = "same_company" if compatible(a_old, a_new) else "CONFLICT"
        elif a_old:
            cat = "original_found_one_new_did_not"
        elif a_new:
            cat = "new_found_one_original_did_not"
        else:
            cat = "both_name_nothing"
        cats[cat] += 1
        if cat not in ("same_company", "both_name_nothing"):
            notes[cat].append(f"{sha[:8]}: original {a_old!r} / new {a_new!r}")
        record = known(sha)
        if record:
            for label, value in (("original", a_old), ("new", a_new)):
                vs_record[f"{label}:" + ("matches_record" if value and compatible(record, value) else "names_nothing" if not value else "differs_from_record")] += 1
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0
    print(json.dumps({"files_compared": sum(cats.values()), "outcome": dict(cats), "against_existing_records": dict(vs_record),
                      "mean_seconds_original": mean(seconds_old), "mean_seconds_new": mean(seconds_new), "details": dict(notes)}, indent=1))
    return 0


def cmd_snapshot(_args) -> int:
    cs, _ = _app()
    rows = cs._load(force=True)["candidates"]
    digest = hashlib.sha256()
    for row in rows:
        line = json.dumps({"cid": str(row.get("id") or ""), "payload": row}, sort_keys=True, ensure_ascii=False)
        digest.update(line.encode("utf-8"))
        print(line)
    print(json.dumps({"_snapshot_footer": True, "count": len(rows), "sha256": digest.hexdigest(), "taken_at": _now()}))
    return 0


def _norm(value) -> str:
    return " ".join(str(value or "").split())


def cmd_apply(args) -> int:
    cs, _ = _app()
    raw = open(args.plan, "rb").read()
    if hashlib.sha256(raw).hexdigest() != args.expect_plan_sha256:
        print("REFUSED: the plan is not the one that was reviewed (sha256 differs). Nothing written.")
        return 2
    plan = json.loads(raw)
    items = [i for i in plan["items"] if i["action"] in ACTIONS_THAT_WRITE and i.get("after")]
    if len(items) != args.confirm_count:
        print(f"REFUSED: the plan has {len(items)} writes, --confirm-count says {args.confirm_count}. Nothing written.")
        return 2
    counts: collections.Counter = collections.Counter()
    for item in items:
        rows = cs._load(force=True)["candidates"]
        row = next((r for r in rows if str(r.get("id") or "") == item["cid"]), None)
        if row is None:
            counts["row_gone"] += 1
            print(json.dumps({"cid8": item["cid"][:8], "result": "row_gone"}))
            continue
        current = _norm(row.get("interview_company"))
        wanted = _norm(cs.normalise_interview_company(item["after"]))
        if current == wanted:
            counts["already_set"] += 1
            print(json.dumps({"cid8": item["cid"][:8], "result": "already_set"}))
            continue
        if current != _norm(item["before"]):
            counts["skipped_changed_since_plan"] += 1
            print(json.dumps({"cid8": item["cid"][:8], "result": "skipped_changed_since_plan"}))
            continue
        if not args.apply:
            counts["would_write"] += 1
            continue
        before_row = json.loads(json.dumps(row))
        cs._patch_row_fields(item["cid"], {"interview_company": wanted})
        after_row = next((r for r in cs._load(force=True)["candidates"] if str(r.get("id") or "") == item["cid"]), None)
        other_diffs = sorted(
            k for k in set(before_row) | set(after_row or {})
            if k not in BOOKKEEPING and k != "interview_company" and before_row.get(k) != (after_row or {}).get(k)
        )
        if after_row is None or _norm(after_row.get("interview_company")) != wanted or other_diffs:
            print(json.dumps({"cid8": item["cid"][:8], "result": "WRITE_MISMATCH", "other_keys_changed": other_diffs}))
            print("STOPPED at the first mismatch. Nothing further written.")
            return 1
        counts["written"] += 1
        # The whole line, id included, is what `rollback` reads back; the host keeps it in its root-only work directory.
        print(json.dumps({"cid": item["cid"], "result": "written", "before": row.get("interview_company") or "",
                          "after": wanted, "action": item["action"], "at": _now()}))
        time.sleep(0.2)
    print(json.dumps({"apply": bool(args.apply), "counts": dict(counts)}))
    return 0


def cmd_verify(args) -> int:
    cs, _ = _app()
    before = {}
    footer = None
    for raw in open(args.before, encoding="utf-8"):
        entry = json.loads(raw)
        if entry.get("_snapshot_footer"):
            footer = entry
            continue
        before[entry["cid"]] = entry["payload"]
    plan = _load_json(args.plan)
    intended = {i["cid"]: i["after"] for i in plan["items"] if i["action"] in ACTIONS_THAT_WRITE and i.get("after")}
    live = {str(r.get("id") or ""): r for r in cs._load(force=True)["candidates"]}
    outcome: collections.Counter = collections.Counter()
    other_keys: collections.Counter = collections.Counter()
    for cid, then in before.items():
        now = live.get(cid)
        if now is None:
            outcome["DELETED"] += 1
            continue
        diff = sorted(k for k in set(then) | set(now) if then.get(k) != now.get(k) and k not in BOOKKEEPING)
        if not diff:
            outcome["unchanged" if cid not in intended else "intended_but_not_written"] += 1
        elif diff == ["interview_company"] and cid in intended and _norm(now.get("interview_company")) == _norm(cs.normalise_interview_company(intended[cid])):
            outcome["company_written_as_planned"] += 1
        elif diff == ["interview_company"]:
            outcome["company_changed_not_by_this_plan"] += 1
        else:
            outcome["other_fields_changed"] += 1
            for key in diff:
                other_keys[key] += 1
    outcome["new_rows_since_snapshot"] = len(set(live) - set(before))
    print(json.dumps({"snapshot_rows": footer and footer.get("count"), "live_rows": len(live), "outcome": dict(outcome),
                      "other_fields_changed_by_key": dict(other_keys), "intended_writes": len(intended)}))
    return 0


def cmd_rollback(args) -> int:
    """Put back what `apply` wrote, from its own log. A record whose company has changed since is left alone: that
    value is newer than the backfill's, and newer data is never overwritten."""
    cs, _ = _app()
    counts: collections.Counter = collections.Counter()
    for line in _read_lines(args.applied):
        if line.get("result") != "written":
            continue
        row = next((r for r in cs._load(force=True)["candidates"] if str(r.get("id") or "") == line["cid"]), None)
        if row is None:
            counts["row_gone"] += 1
            continue
        if _norm(row.get("interview_company")) != _norm(line["after"]):
            counts["changed_since_kept"] += 1
            continue
        if not args.apply:
            counts["would_restore"] += 1
            continue
        cs._patch_row_fields(line["cid"], {"interview_company": line["before"]})
        now = next((r for r in cs._load(force=True)["candidates"] if str(r.get("id") or "") == line["cid"]), {})
        if _norm(now.get("interview_company")) != _norm(line["before"]):
            print(json.dumps({"cid8": line["cid"][:8], "result": "RESTORE_MISMATCH"}))
            print("STOPPED at the first mismatch.")
            return 1
        counts["restored"] += 1
    print(json.dumps({"apply": bool(args.apply), "counts": dict(counts)}))
    return 0


# ---------------------------------------------------------------------------------------------
# Host side: the resumable loop
# ---------------------------------------------------------------------------------------------

LIVE_AI_PATHS = ("/public/slots/extract-invite-ai", "/public/slots/payment-proof", "/public/slots/extract-payment-ai",
                 "/public/slots/extract-resume-ai", "/handler-expenses/extract")


def live_ai_requests_in_last(seconds: int, logs=("/var/log/nginx/access.log",)) -> int:
    """Real people using the AI node right now (from outside; internal addresses ignored)."""
    now = datetime.now(timezone.utc)
    hits = 0
    pattern = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)')
    for path in logs:
        try:
            handle = open(path, "rb")
        except OSError:
            continue
        with handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 400_000))
            for raw in handle.read().decode("utf-8", "replace").splitlines()[1:]:
                match = pattern.match(raw)
                if not match or match.group(1).startswith(("127.", "172.", "10.")):
                    continue
                try:
                    when = datetime.strptime(match.group(2), "%d/%b/%Y:%H:%M:%S %z")
                except ValueError:
                    continue
                if (now - when).total_seconds() <= seconds and any(match.group(4).startswith(p) for p in LIVE_AI_PATHS):
                    hits += 1
    return hits


def _docker(*argv: str, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)


def _plan_in_container(work: str, results: str, log) -> int:
    """Write plan.json from the finished readings; the inputs are removed from the container afterwards."""
    names = {"inventory.json": "/tmp/bf-inventory.json", results: "/tmp/bf-results.jsonl"}
    try:
        for local, inside in names.items():
            _docker("cp", os.path.join(work, local), f"{CONTAINER}:{inside}", timeout=60).check_returncode()
        run = _docker("exec", "-w", "/app", CONTAINER, "python", IN_CONTAINER, "plan", "--inventory", names["inventory.json"],
                      "--results", names[results], "--out", "/tmp/bf-plan.json", timeout=600)
        if run.returncode != 0:
            log(f"plan failed: {run.stderr[-300:]}")
            return 1
        _docker("cp", f"{CONTAINER}:/tmp/bf-plan.json", os.path.join(work, "plan.json"), timeout=60).check_returncode()
        log("plan written: " + run.stdout.strip().splitlines()[-1])
        return 0
    finally:
        _docker("exec", CONTAINER, "rm", "-f", *names.values(), "/tmp/bf-plan.json", timeout=60)


def cmd_drive(args) -> int:
    work = args.work
    inventory = _load_json(os.path.join(work, "inventory.json"))
    results_path = os.path.join(work, args.results)
    log_path = os.path.join(work, "driver.log")
    stop_file = os.path.join(work, "STOP")
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00")) if args.until else None
    script = os.path.abspath(__file__)

    def log(message: str):
        line = f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}] {message}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # Records with no company first (what is most wanted), then the rest; oldest upload first.
    def priority(info):
        empty = any(not inventory["rows"][u["cid"]]["company"] for u in info["uploads"])
        return (0 if empty else 1, min(u["uploaded_at"] for u in info["uploads"]))

    queue = sorted(inventory["files"].values(), key=priority)
    if args.only:
        queue = [f for f in queue if f["sha"].startswith(tuple(args.only))]
    per_reading = 2 * 3 * CALL_TIMEOUT + 120        # two questions, each may walk all three nodes
    outages = 0
    primary_model = args.model or "production vision model"
    log(f"driving {len(queue)} files; primary: {primary_model}; audit: {args.audit_model} on {args.audit_order}")
    while True:
        if os.path.exists(stop_file):
            log("STOP file present: stopping cleanly (run drive again to resume)")
            return 0
        if until and datetime.now(timezone.utc) >= until:
            log(f"reached {args.until}: stopping cleanly (run drive again to resume)")
            return 0
        primary, audit = _load_results(results_path, PRIMARY), _load_results(results_path, AUDIT)
        tasks = pending_tasks(queue, primary, audit)
        if not tasks:
            log(f"every file has its outcome: {len(queue)} files read, {len(audit)} audited")
            return _plan_in_container(work, args.results, log) if args.then_plan else 0
        item, stage = tasks[0]
        waited = 0
        while live_ai_requests_in_last(args.courtesy_seconds) and waited < 1800:
            if waited == 0:
                log("a person is using the AI right now: waiting for it to go quiet")
            time.sleep(30)
            waited += 30
        # Copied before every reading: a deploy replaces the container and its /tmp with it.
        if _docker("cp", script, f"{CONTAINER}:{IN_CONTAINER}", timeout=60).returncode != 0:
            log("the container is not there (a deploy or a restart): waiting 60 s; not an attempt")
            time.sleep(60)
            continue
        audit_stage = stage == AUDIT
        try:
            run = _docker("exec", "-w", "/app", CONTAINER, "python", IN_CONTAINER, "read", "--sha", item["sha"],
                          "--path", item["path"], "--mime", item["mime"], "--stage", stage,
                          "--model", args.audit_model if audit_stage else args.model,
                          "--order", args.audit_order if audit_stage else args.order, timeout=per_reading)
        except subprocess.TimeoutExpired:
            line = {"sha": item["sha"], "stage": stage, "at": _now(), "status": "failed", "error": "driver timeout",
                    "seconds": per_reading}
        else:
            last = next((out for out in reversed(run.stdout.splitlines()) if out.startswith("{")), "")
            if not last:
                log(f"no answer from the container ({run.returncode}): waiting 60 s; not an attempt")
                time.sleep(60)
                continue
            line = json.loads(last)
        line["attempt_at_driver"] = _now()
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if line.get("status") == "unavailable":
            pause = OUTAGE_BACKOFF[min(outages, len(OUTAGE_BACKOFF) - 1)]
            outages += 1
            log(f"no AI node can be reached for the {stage} model ({line.get('error', '')[:160]}): "
                f"waiting {pause} s; not an attempt")
            time.sleep(pause)
            continue
        outages = 0
        if line.get("status") == "ok":
            note = f"company={'yes' if line.get('company') else 'no'} verified={line.get('verified')} node={line.get('node')}"
        else:
            note = line.get("error", "")
        done_primary = sum(1 for s in _load_results(results_path, PRIMARY).values() if not _due(s))
        log(f"{stage} {item['sha'][:8]} {line.get('status')} {line.get('seconds')}s {note} | primary {done_primary}/{len(queue)} "
            f"done, {len(tasks) - 1} readings left")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    sub.add_parser("snapshot")
    p = sub.add_parser("read")
    p.add_argument("--sha", required=True); p.add_argument("--path", required=True); p.add_argument("--mime", default="image/jpeg")
    p.add_argument("--stage", choices=(PRIMARY, AUDIT), default=PRIMARY)
    p.add_argument("--model", default="", help="empty = production's configured vision model")
    p.add_argument("--order", default="", help="nodes to ask, comma-separated; empty = production's routing order")
    p = sub.add_parser("plan")
    p.add_argument("--inventory", required=True); p.add_argument("--results", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("review")
    p.add_argument("--plan", required=True); p.add_argument("--inventory", required=True)
    p = sub.add_parser("report")
    p.add_argument("--plan", required=True); p.add_argument("--applied", default="")
    p = sub.add_parser("apply")
    p.add_argument("--plan", required=True); p.add_argument("--expect-plan-sha256", required=True)
    p.add_argument("--confirm-count", type=int, required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("--before", required=True); p.add_argument("--plan", required=True)
    p = sub.add_parser("rollback")
    p.add_argument("--applied", required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("drive")
    p.add_argument("--work", default="/root/company-backfill"); p.add_argument("--until", default="")
    p.add_argument("--courtesy-seconds", type=int, default=150); p.add_argument("--only", nargs="*")
    p.add_argument("--results", default="results.jsonl")
    p.add_argument("--model", default=""); p.add_argument("--order", default="")
    p.add_argument("--audit-model", default=AUDIT_MODEL); p.add_argument("--audit-order", default=",".join(AUDIT_ORDER))
    p.add_argument("--then-plan", action="store_true", help="write plan.json once every file has its outcome")
    p = sub.add_parser("compare")
    p.add_argument("--inventory", required=True); p.add_argument("--old", required=True); p.add_argument("--new", required=True)
    args = parser.parse_args(argv)
    return {"inventory": cmd_inventory, "read": cmd_read, "plan": cmd_plan, "review": cmd_review, "report": cmd_report,
            "snapshot": cmd_snapshot, "apply": cmd_apply, "verify": cmd_verify, "rollback": cmd_rollback,
            "drive": cmd_drive, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
