#!/usr/bin/env python3
"""Fill `interview_company` on historical interview records from their stored invite screenshots.

The booking form now reads the company off the invite (0ff815e). Records booked before that have an
invite screenshot attached and no company. This tool reads those screenshots with the deployed invite
reader and records the company only where the screenshot supports it. Nothing else on a record is
touched, nothing is deleted, and ambiguity is left for review rather than guessed.

Steps (each a sub-command; the ones marked [container] run inside the operations-api container):

  inventory  [container]  every row, its attached screenshots, which files exist, sha256 per file
  read       [container]  ONE screenshot through the deployed reader -> one JSON line (no writes)
  plan       [container]  inventory + readings -> what would change and why (no writes)
  snapshot   [container]  the before-state of the whole candidate store, as JSON lines
  apply      [container]  write the planned companies, one targeted field per row, read back (needs --apply)
  verify     [container]  live rows against the before-state: only the intended fields changed
  drive      [host]       the resumable loop: one reading at a time, a checkpoint line per file

Privacy: output carries counts, company names and short file hashes; never a candidate name or id
(ids and payloads exist only in the root-only files on the host).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.getcwd())  # `docker exec` starts in the application directory

CONTAINER = "teleautomation-production-operations-api-1"
IN_CONTAINER = "/tmp/bf.py"
MAX_ATTEMPTS = 3          # readings per file that ended in failure before it is called unreadable
READ_TIMEOUT = 330        # seconds for one file on the original method (the live endpoint answers in about 80-115 s)
VERIFY_TIMEOUT = 240      # seconds for the second, shorter read that quotes the name (original method)
CALL_TIMEOUT = 150        # seconds for each call of the faster method; a file is two calls, so a hung node costs 5 min, not 11
IMAGE_TOKEN_BUDGET = 1200  # the image is scaled DOWN (never up) to about this many visual tokens; 800 invented companies, 1200 did not
BOOKKEEPING = {"updated_at", "_store_updated_at"}   # keys a targeted row write refreshes by itself
ACTIONS_THAT_WRITE = ("fill", "correct_invalid")


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


SLIM_PROMPT = (
    "Read this interview invite screenshot. Return ONLY valid JSON, no markdown:\n"
    '{"is_interview_invite": true, "company": "", "company_quote": "", "interview_date": ""}\n'
    "- is_interview_invite: false if the image is not an invitation to an interview (for example a payment receipt or an "
    "unrelated chat), otherwise true.\n"
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
    """'match' / 'mismatch' / 'unknown' between the date the reader transcribed and the booked date."""
    def parse(value: str):
        try:
            return date.fromisoformat(str(value or "").strip()[:10])
        except ValueError:
            return None
    seen, booked = parse(raw_date), parse(row_date)
    if seen is None or booked is None:
        return "unknown"
    return "match" if seen == booked else "mismatch"


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


def result_fields(reading: dict, answer: dict | None = None) -> dict:
    """What is kept of one reading. Neither the transcription nor the quote is kept (they hold names and phone numbers).

    `answer` is the verification read: it is only made when the reading names a company that the reader's own
    transcription does not already contain.
    """
    company = str(reading.get("company") or "").strip()
    raw_text = str(reading.get("raw_detected_text") or "")
    if reading.get("is_payment_screenshot"):
        status = "payment_screenshot"
    elif reading.get("looks_like_interview_invite") is False:
        status = "not_an_invite"
    elif reading.get("failure_stage") in ("ollama_unavailable", "vision"):
        status = "failed"
    else:
        status = "ok"
    out = {
        "status": status,
        "company": company,
        "grounded": grounded(company, raw_text) if company else False,
        "verified": quote_supports(company, answer) if answer is not None else None,
        "verify_role": str((answer or {}).get("role") or "")[:20],
        "quote_len": len(str((answer or {}).get("quote") or "")),
        "raw_text_len": len(raw_text),
        "date_raw": str(reading.get("_model_raw_interview_date") or reading.get("interview_date") or ""),
        "confidence_score": int(reading.get("confidence_score") or 0),
        "method": str(reading.get("extraction_method") or ""),
        "model": str(reading.get("primary_model") or ""),
        "node": str(reading.get("inference_node_label") or reading.get("inference_node_id") or ""),
    }
    if status == "failed":
        out["error"] = str(reading.get("failure_reason") or reading.get("failure_stage") or "failed")[:160]
    return out


def collapse_results(lines: list[dict]) -> dict[str, dict]:
    """The checkpoint file holds every attempt; this is the standing outcome per file.

    A reading that succeeded wins over any earlier failure; otherwise the latest failure stands with
    the number of failures so far, so a file is retried until MAX_ATTEMPTS and then called unreadable.
    """
    standing: dict[str, dict] = {}
    for line in lines:
        sha = line.get("sha")
        if not sha:
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


def evidence_for(row: dict, shot: dict, result: dict | None, *, grounding: str = "required", cleaner=None) -> tuple[str, str]:
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
    if grounding == "required" and not (result.get("grounded") or result.get("verified")):
        return "not_confirmed", company
    if date_relation(result.get("date_raw", ""), row.get("date", "")) == "mismatch":
        return "date_mismatch", company
    return "usable", company


def decide_row(row: dict, results: dict[str, dict], *, cleaner, grounding: str = "required") -> dict:
    """The outcome for one record. `cleaner` is features.ollama_invite_extract.clean_company_name."""
    before = str(row.get("company") or "").strip()
    shots = row.get("shots") or []
    evidence = []
    for shot in shots:
        kind, company = evidence_for(row, shot, results.get(shot["sha"]), grounding=grounding, cleaner=cleaner)
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


def build_plan(inventory: dict, results: dict[str, dict], *, cleaner, grounding: str = "required") -> dict:
    items = []
    for cid, row in inventory["rows"].items():
        decision = decide_row(row, results, cleaner=cleaner, grounding=grounding)
        items.append({"cid": cid, **decision})
    summary = collections.Counter(item["action"] for item in items)
    return {"grounding": grounding, "generated_at": _now(), "items": items, "summary": dict(summary)}


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


def _ask(extractor, b64: str, prompt: str, timeout: int) -> tuple[dict | None, str]:
    """One question to the vision model about one image, in a thread so a hung node cannot hold the file. (answer, error)"""
    box: dict = {}

    def run():
        try:
            text = extractor.call_ollama_vision_model(extractor.OLLAMA_VISION_MODEL, b64, prompt, timeout=timeout)
            box["answer"] = extractor.parse_strict_json_response(text) if text else None
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"{type(exc).__name__}: {exc}"[:120]

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout + 20)
    if worker.is_alive():
        return None, "timeout"
    if "error" in box:
        return None, box["error"]
    if not isinstance(box.get("answer"), dict):
        return None, "no parseable answer"
    return box["answer"], ""


def read_slim(extractor, data: bytes) -> dict:
    """The faster method. Question one reads the company (and says if this is an invite at all); question two, asked
    about the very same image bytes so the node reuses the image it already read, must quote the words that name it."""
    import base64
    prepared, scale = prepare_image(data)
    b64 = base64.b64encode(prepared).decode("ascii")
    started = time.time()
    first, error = _ask(extractor, b64, SLIM_PROMPT, CALL_TIMEOUT)
    seconds_first = time.time() - started
    if error:
        return {"status": "failed", "error": "read: " + error}
    first["company"] = extractor.clean_company_name(first.get("company"))
    answer, seconds_second = None, 0.0
    if first["company"] and first.get("is_interview_invite") is not False:
        started = time.time()
        answer, error = _ask(extractor, b64, VERIFY_PROMPT.format(company=first["company"].replace('"', "'")), CALL_TIMEOUT)
        seconds_second = time.time() - started
        if error:
            return {"status": "failed", "error": "verification: " + error}
    return slim_result_fields(first, answer, scale=scale, seconds_first=seconds_first, seconds_second=seconds_second)


def cmd_read(args) -> int:
    _, extractor = _app()
    data = open(args.path, "rb").read()
    out = {"sha": args.sha, "at": _now()}
    if hashlib.sha256(data).hexdigest() != args.sha:
        out.update(status="failed", error="file does not match its recorded hash", seconds=0.0)
        print(json.dumps(out))
        return 0
    if args.method == "slim":
        started = time.time()
        out.update(read_slim(extractor, data))
        out["seconds"] = round(time.time() - started, 1)
        print(json.dumps(out))
        sys.stdout.flush()
        os._exit(0)
    box: dict = {}

    def run():
        try:
            box["reading"] = extractor.extract_interview_invite_with_ollama(data, args.mime)
        except Exception as exc:  # noqa: BLE001 - recorded, and the file is retried
            box["error"] = f"{type(exc).__name__}: {exc}"[:160]

    started = time.time()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(READ_TIMEOUT)
    if worker.is_alive():
        out.update(status="failed", error="timeout")
    elif "error" in box:
        out.update(status="failed", error=box["error"])
    else:
        reading = box["reading"]
        fields = result_fields(reading)
        company = fields["company"]
        if fields["status"] == "ok" and company and not fields["grounded"]:
            answer, error = _verify_by_quote(extractor, data, company)
            if error:
                out.update(status="failed", error="verification: " + error)
            else:
                fields = result_fields(reading, answer)
                out.update(fields)
        else:
            out.update(fields)
    out["seconds"] = round(time.time() - started, 1)
    print(json.dumps(out))
    sys.stdout.flush()
    os._exit(0)   # a timed-out reading's thread must not keep the process alive


def _verify_by_quote(extractor, data: bytes, company: str) -> tuple[dict | None, str]:
    """Second read: ask the same vision model to quote the words that name the company. (answer, error)"""
    import base64
    box: dict = {}

    def run():
        try:
            text = extractor.call_ollama_vision_model(
                extractor.OLLAMA_VISION_MODEL, base64.b64encode(data).decode("ascii"),
                VERIFY_PROMPT.format(company=company.replace('"', "'")), timeout=VERIFY_TIMEOUT)
            box["answer"] = extractor.parse_strict_json_response(text) if text else None
        except Exception as exc:  # noqa: BLE001
            box["error"] = f"{type(exc).__name__}: {exc}"[:120]

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(VERIFY_TIMEOUT + 30)
    if worker.is_alive():
        return None, "timeout"
    if "error" in box:
        return None, box["error"]
    if not isinstance(box.get("answer"), dict):
        return None, "no parseable answer"
    return box["answer"], ""


def _load_json(path: str):
    return json.load(open(path, encoding="utf-8"))


def _load_results(path: str) -> dict[str, dict]:
    lines = []
    if os.path.exists(path):
        for raw in open(path, encoding="utf-8"):
            raw = raw.strip()
            if raw:
                lines.append(json.loads(raw))
    return collapse_results(lines)


def cmd_plan(args) -> int:
    from features.ollama_invite_extract import clean_company_name
    inventory = _load_json(args.inventory)
    plan = build_plan(inventory, _load_results(args.results), cleaner=clean_company_name, grounding=args.grounding)
    plan["missing_entries"] = len(inventory.get("missing") or [])
    plan["unique_files"] = len(inventory.get("files") or {})
    json.dump(plan, open(args.out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({"summary": plan["summary"], "missing_entries": plan["missing_entries"], "unique_files": plan["unique_files"]}))
    return 0


def cmd_compare(args) -> int:
    from features.ollama_invite_extract import clean_company_name as clean
    inventory = _load_json(args.inventory)
    old, new = _load_results(args.old), _load_results(args.new)
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
        print(json.dumps({"cid8": item["cid"][:8], "result": "written", "company": wanted}))
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


# ---------------------------------------------------------------------------------------------
# Host side: the resumable loop
# ---------------------------------------------------------------------------------------------

LIVE_AI_PATHS = ("/public/slots/extract-invite-ai", "/public/slots/payment-proof", "/public/slots/extract-payment-ai",
                 "/handler-expenses/extract")


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


def cmd_drive(args) -> int:
    work = args.work
    inventory = _load_json(os.path.join(work, "inventory.json"))
    results_path = os.path.join(work, args.results)
    log_path = os.path.join(work, "driver.log")
    stop_file = os.path.join(work, "STOP")
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00")) if args.until else None

    def log(message: str):
        line = f"[{datetime.now(timezone.utc):%H:%M:%S}] {message}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    # Rows with an empty company first (they are what is being asked for), then the rest; oldest upload first.
    def priority(info):
        empty = any(not inventory["rows"][u["cid"]]["company"] for u in info["uploads"])
        return (0 if empty else 1, min(u["uploaded_at"] for u in info["uploads"]))

    queue = sorted(inventory["files"].values(), key=priority)
    if args.only:
        queue = [f for f in queue if f["sha"].startswith(tuple(args.only))]
    total = len(queue)
    _docker("cp", os.path.abspath(__file__), f"{CONTAINER}:{IN_CONTAINER}", timeout=60)
    done = 0
    while True:
        standing = _load_results(results_path)
        todo = [f for f in queue if f["sha"] not in standing
                or (standing[f["sha"]]["status"] == "failed" and standing[f["sha"]]["failures"] < MAX_ATTEMPTS)]
        # a file that failed waits until every untried file has had its first attempt
        todo.sort(key=lambda f: (f["sha"] in standing,))
        if not todo:
            log(f"nothing left to read: {total} files, every one has an outcome")
            return 0
        item = todo[0]
        if os.path.exists(stop_file):
            log("STOP file present: stopping cleanly")
            return 0
        if until and datetime.now(timezone.utc) >= until:
            log(f"reached {args.until}: stopping cleanly with {len(todo)} files left (resume by running drive again)")
            return 0
        waited = 0
        while live_ai_requests_in_last(args.courtesy_seconds) and waited < 1800:
            if waited == 0:
                log("a person is using the AI node: waiting for it to go quiet")
            time.sleep(30)
            waited += 30
        try:
            run = _docker("exec", CONTAINER, "python", IN_CONTAINER, "read", "--sha", item["sha"], "--path", item["path"],
                          "--mime", item["mime"], "--method", args.method, timeout=2 * CALL_TIMEOUT + 90)
        except subprocess.TimeoutExpired:
            line = {"sha": item["sha"], "at": _now(), "status": "failed", "error": "driver timeout", "seconds": 2 * CALL_TIMEOUT + 90}
        else:
            last = next((l for l in reversed(run.stdout.splitlines()) if l.startswith("{")), "")
            if not last:
                # the container is restarting (a deploy, or the purge window): wait and retry; not an attempt
                log(f"no answer from the container ({run.returncode}); waiting 60 s")
                time.sleep(60)
                continue
            line = json.loads(last)
        line["attempt_at_driver"] = _now()
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        done += 1
        company_note = (f"company={'yes' if line.get('company') else 'no'} grounded={line.get('grounded')} verified={line.get('verified')}"
                        if line.get("status") == "ok" else line.get("error", ""))
        log(f"{len(standing) + 1}/{total} {item['sha'][:8]} {line.get('status')} {line.get('seconds')}s {company_note}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory")
    sub.add_parser("snapshot")
    p = sub.add_parser("read")
    p.add_argument("--sha", required=True); p.add_argument("--path", required=True); p.add_argument("--mime", default="image/jpeg")
    p.add_argument("--method", choices=("slim", "full"), default="slim")
    p = sub.add_parser("plan")
    p.add_argument("--inventory", required=True); p.add_argument("--results", required=True); p.add_argument("--out", required=True)
    p.add_argument("--grounding", choices=("required", "advisory"), default="required")
    p = sub.add_parser("apply")
    p.add_argument("--plan", required=True); p.add_argument("--expect-plan-sha256", required=True)
    p.add_argument("--confirm-count", type=int, required=True); p.add_argument("--apply", action="store_true")
    p = sub.add_parser("verify")
    p.add_argument("--before", required=True); p.add_argument("--plan", required=True)
    p = sub.add_parser("drive")
    p.add_argument("--work", default="/root/company-backfill"); p.add_argument("--until", default="")
    p.add_argument("--courtesy-seconds", type=int, default=150); p.add_argument("--only", nargs="*")
    p.add_argument("--results", default="results.jsonl"); p.add_argument("--method", choices=("slim", "full"), default="slim")
    p = sub.add_parser("compare")
    p.add_argument("--inventory", required=True); p.add_argument("--old", required=True); p.add_argument("--new", required=True)
    args = parser.parse_args(argv)
    return {"inventory": cmd_inventory, "read": cmd_read, "plan": cmd_plan, "snapshot": cmd_snapshot,
            "apply": cmd_apply, "verify": cmd_verify, "drive": cmd_drive, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
