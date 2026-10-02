"""Report what a stored resume could safely fill for existing candidates.

READ-ONLY. It reads each candidate that is missing email, phone, technology or
name, finds a resume file that is still stored, has the AI read it, and prints
what `features.resume_autofill.plan_autofill` would write -- with every address
and number masked. It changes nothing. Applying a backfill is a separate,
explicit step that needs approval after this report has been read.

Run inside the Operations container:

    docker exec -i <operations-api> python - < scripts/resume_autofill_report.py [--all-stages]

Only in-progress candidates are considered unless --all-stages is given.
"""
from __future__ import annotations

import collections
import os
import sys


def mask_email(value: str) -> str:
    local, _, domain = (value or "").partition("@")
    return f"{local[:2]}***@{domain}" if domain else ""


def mask_phone(value: str) -> str:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return f"******{digits[-4:]}" if len(digits) >= 4 else ""


def main(argv: list[str]) -> int:
    from features import candidate_store as cs
    from features import resume_autofill as ra
    from features.ollama_resume_extract import extract_resume_with_ollama

    stages = None if "--all-stages" in argv else {"in_progress"}
    rows = cs._load().get("candidates") or []

    # One person = one phone identity, else one name; their gaps and resumes pool.
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        if stages and row.get("stage") not in stages:
            continue
        phone = cs.candidate_phone_identity(row.get("phone"))
        key = f"p:{phone}" if phone else "n:" + " ".join(str(row.get("name") or "").lower().split())
        groups[key].append(row)

    summary = collections.Counter()
    for key, group in sorted(groups.items(), key=lambda kv: str(kv[1][0].get("name"))):
        merged = {
            field: next((r.get(field) for r in group if str(r.get(field) or "").strip()), "")
            for field in ra.FIELDS
        }
        gaps = [
            field for field in ra.FIELDS
            if not ra.usable(field, merged.get(field), normalise_phone=cs.candidate_phone_identity,
                             canonical_technology=cs.canonical_technology)
        ]
        if not gaps:
            continue
        summary["candidates with a gap"] += 1
        resumes = [(r["id"], e) for r in group for e in (r.get("resumes") or [])]
        stored = [
            (rid, e, os.path.join(cs._resume_dir(rid), str(e.get("filename") or "")))
            for rid, e in resumes
        ]
        stored = [(rid, e, path) for rid, e, path in stored if os.path.isfile(path)]
        name = str(group[0].get("name") or "")
        print(f"\n{name}  gaps: {', '.join(gaps)}  | resume records: {len(resumes)}, files still stored: {len(stored)}")
        if not resumes:
            summary["no resume on record"] += 1
            print("   -> no resume on record")
            continue
        if not stored:
            summary["resume record but file lost"] += 1
            print("   -> resume record exists but the file is no longer stored")
            continue

        rid, entry, path = stored[-1]
        with open(path, "rb") as handle:
            data = handle.read()
        extraction = extract_resume_with_ollama(data, str(entry.get("mime_type") or "application/pdf"))
        anchor = group[0]
        others = [r for r in rows if r not in group and not ra._same_person_row(r, anchor)]
        plan = ra.plan_autofill(
            {**anchor, **{f: merged.get(f) for f in ra.FIELDS}}, extraction, others=others,
            normalise_phone=cs.candidate_phone_identity, canonical_technology=cs.canonical_technology,
            known_technologies=[r.get("technology") for r in rows],
        )
        print(f"   read by {extraction.get('extraction_source') or '?'} at confidence {extraction.get('confidence_score')}")
        if plan["refused"]:
            summary["refused"] += 1
            print(f"   -> NOT applied: {plan['refused']}")
            continue
        if not plan["fill"]:
            summary["nothing safe to fill"] += 1
        for field, value in plan["fill"].items():
            shown = mask_email(value) if field == "email" else mask_phone(value) if field == "phone" else value
            print(f"   -> WOULD FILL {field}: {shown}")
            summary[f"would fill {field}"] += 1
        for field, reason in plan["skipped"].items():
            print(f"   -> skip {field}: {reason}")
    print("\nSUMMARY (nothing was changed):")
    for key, count in sorted(summary.items()):
        print(f"  {count:>3}  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
