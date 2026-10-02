"""Fill a candidate's missing details from their resume -- and nothing else.

A resume is read by an AI node when it is uploaded, and the reading used to be
shown to an operator who could press "Fill fields". That button overwrote the
name, technology, phone and email of an existing candidate whether or not they
were already right, and nothing filled a gap on its own.

The rule here is narrower and safer:

* only a field that is **blank or unusable** is filled; a valid value is never
  replaced, and a blank from the resume never replaces anything;
* only an **AI reading** at or above `MIN_CONFIDENCE` is trusted. A regex or OCR
  guess ("the first email in the file") may be someone else's -- a referee's, a
  company's -- so it is reported, never applied;
* a resume whose name does not match the candidate's is **ignored entirely**:
  it is probably someone else's;
* a phone or email that another candidate already holds is **never written**:
  the phone is the identity key, and writing it would merge two people;
* every filled field is recorded on the resume it came from, so it can be
  traced and undone by hand.

`plan_autofill` is pure so every branch is testable without a database.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone
from typing import Any, Iterable

#: Self-reported confidence (0-100) an AI reading needs before it may fill anything.
MIN_CONFIDENCE = 70

#: Readings produced by a language/vision model. Regex and OCR-regex fallbacks
#: are deliberately absent.
TRUSTED_SOURCES = frozenset({"pdf_text_ai", "scanned_pdf_vision"})

FIELDS = ("email", "phone", "technology", "name")

_EMAIL = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$")
_JUNK_EMAIL_DOMAINS = frozenset({"example.com", "example.org", "test.com", "domain.com", "email.com", "yourmail.com"})
_PLACEHOLDER_NAMES = frozenset({"", "unknown", "candidate", "na", "n/a", "none", "test", "unnamed"})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", _clean(name).lower()) if len(t) > 1]


def same_person(a: str, b: str) -> bool:
    """Do two names plausibly belong to one person?

    Tolerates spelling variants ("Vekateshwarlu"/"Venkateshwarlu"), word order and
    an extra surname, but not a different person. Each token of the shorter name
    must have a close match in the longer one.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    matched = sum(
        1 for token in short
        if any(difflib.SequenceMatcher(None, token, other).ratio() >= 0.8 for other in long_)
    )
    return matched / len(short) >= 0.5 and matched >= 1


def valid_email(value: str) -> str:
    email = _clean(value).lower()
    if not _EMAIL.match(email):
        return ""
    return "" if email.split("@", 1)[1] in _JUNK_EMAIL_DOMAINS else email


def valid_phone(value: str, normalise) -> str:
    """The 10-digit Indian mobile identity, or "" if it is not one."""
    identity = normalise(value)
    return identity if re.fullmatch(r"[6-9]\d{9}", identity or "") else ""


def usable(field: str, value: Any, *, normalise_phone, canonical_technology) -> bool:
    """Is the candidate's current value good enough to keep?"""
    text = _clean(value)
    if field == "email":
        return bool(valid_email(text))
    if field == "phone":
        return bool(normalise_phone(text))
    if field == "technology":
        return canonical_technology(text) not in {"", "Unspecified"}
    if field == "name":
        return text.lower() not in _PLACEHOLDER_NAMES and len(_tokens(text)) >= 1
    return bool(text)


def plan_autofill(
    candidate: dict[str, Any],
    extraction: dict[str, Any],
    *,
    others: Iterable[dict[str, Any]],
    normalise_phone,
    canonical_technology,
    known_technologies: Iterable[str],
) -> dict[str, Any]:
    """Decide what the resume may fill. Returns {"fill": {}, "skipped": {}, "refused": ""}."""
    fill: dict[str, str] = {}
    skipped: dict[str, str] = {}

    source = _clean(extraction.get("extraction_source"))
    try:
        confidence = int(extraction.get("confidence_score") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if extraction.get("is_resume") is not True:
        return {"fill": {}, "skipped": {}, "refused": "the file was not recognised as a resume"}
    if source not in TRUSTED_SOURCES:
        return {"fill": {}, "skipped": {}, "refused": f"read by '{source or 'unknown'}', not an AI model; reported, not applied"}
    if confidence < MIN_CONFIDENCE:
        return {"fill": {}, "skipped": {}, "refused": f"confidence {confidence} is below {MIN_CONFIDENCE}"}

    current_name = _clean(candidate.get("name"))
    found_name = _clean(extraction.get("candidate_name"))
    name_known = usable("name", current_name, normalise_phone=normalise_phone, canonical_technology=canonical_technology)
    if name_known and found_name and not same_person(current_name, found_name):
        return {"fill": {}, "skipped": {}, "refused": "the name on the resume does not match this candidate"}
    if name_known and not found_name:
        # Nothing to confirm the resume is theirs: fill only what cannot be
        # someone else's -- which is nothing identifying.
        return {"fill": {}, "skipped": {}, "refused": "the resume gave no name to confirm it is this candidate's"}

    others = list(others)
    taken_emails = {valid_email(o.get("email")) for o in others} - {""}
    taken_phones = {normalise_phone(o.get("phone")) for o in others} - {""}
    known = {canonical_technology(t) for t in known_technologies} - {"", "Unspecified"}

    for field in FIELDS:
        if usable(field, candidate.get(field), normalise_phone=normalise_phone, canonical_technology=canonical_technology):
            continue  # a valid value is never replaced
        if field == "email":
            email = valid_email(extraction.get("email"))
            if not email:
                skipped[field] = "no valid email on the resume"
            elif email in taken_emails:
                skipped[field] = "that email belongs to another candidate"
            else:
                fill[field] = email
        elif field == "phone":
            phone = valid_phone(extraction.get("phone"), normalise_phone)
            if not phone:
                skipped[field] = "no valid mobile number on the resume"
            elif phone in taken_phones:
                skipped[field] = "that number belongs to another candidate"
            else:
                fill[field] = phone
        elif field == "technology":
            tech = canonical_technology(_clean(extraction.get("technology")))
            if tech in {"", "Unspecified"}:
                skipped[field] = "no technology on the resume"
            elif tech not in known:
                skipped[field] = f"'{tech}' is not a technology this roster uses"
            else:
                fill[field] = tech
        elif field == "name":
            if found_name and len(_tokens(found_name)) >= 1:
                fill[field] = found_name
            else:
                skipped[field] = "no name on the resume"
    return {"fill": fill, "skipped": skipped, "refused": ""}


# --- applying a plan -----------------------------------------------------------

def apply_autofill(cid: str, extraction: dict[str, Any], *, resume_id: str = "") -> dict[str, Any]:
    """Plan and apply a fill for one candidate, through the store's normal update.

    Goes through `update_candidate`, so normalisation and the sharing of
    identity fields across a profile's slot rows behave exactly as for any edit.
    A failure here never fails the upload that triggered it.
    """
    from features import candidate_store as cs

    row = cs.get_candidate(cid)
    if not row:
        return {"filled": {}, "skipped": {}, "refused": "candidate not found"}

    rows = cs._load().get("candidates") or []
    same_group = _same_profile_ids(cs, row, rows)
    others = [r for r in rows if str(r.get("id")) not in same_group and not _same_person_row(r, row)]
    known_tech = [r.get("technology") for r in rows]

    plan = plan_autofill(
        row, extraction, others=others,
        normalise_phone=cs.candidate_phone_identity,
        canonical_technology=cs.canonical_technology,
        known_technologies=known_tech,
    )
    result = {"filled": {}, "skipped": plan["skipped"], "refused": plan["refused"]}
    if not plan["fill"]:
        return result
    try:
        updated = cs.update_candidate(cid, dict(plan["fill"]))
    except Exception as exc:  # noqa: BLE001 - the upload itself must still succeed
        result["refused"] = f"could not be applied: {str(exc)[:120]}"
        return result
    if updated is None:
        result["refused"] = "candidate not found"
        return result
    result["filled"] = dict(plan["fill"])
    if resume_id:
        _record_on_resume(cs, cid, resume_id, result["filled"], extraction)
    return result


def _same_person_row(other: dict, row: dict) -> bool:
    return same_person(_clean(other.get("name")), _clean(row.get("name"))) and bool(_tokens(row.get("name")))


def _same_profile_ids(cs, row: dict, rows: list[dict]) -> set[str]:
    phone = cs.candidate_phone_identity(row.get("phone"))
    name = " ".join(_clean(row.get("name")).lower().split())
    ids = {str(row.get("id"))}
    for other in rows:
        if phone and cs.candidate_phone_identity(other.get("phone")) == phone:
            ids.add(str(other.get("id")))
        elif not phone and name and " ".join(_clean(other.get("name")).lower().split()) == name:
            ids.add(str(other.get("id")))
    return ids


def _record_on_resume(cs, cid: str, resume_id: str, filled: dict, extraction: dict) -> None:
    """Note on the resume which fields it supplied, for tracing and undoing."""
    try:
        data = cs._load()
        for r in data.get("candidates") or []:
            if str(r.get("id")) != str(cid):
                continue
            for item in r.get("resumes") or []:
                if str(item.get("id")) == str(resume_id):
                    item["autofill"] = {
                        "at": datetime.now(timezone.utc).isoformat(),
                        "fields": sorted(filled),
                        "source": _clean(extraction.get("extraction_source")),
                        "confidence": int(extraction.get("confidence_score") or 0),
                    }
                    cs._save(data)
                    return
    except Exception:  # noqa: BLE001 - provenance is best effort; the fill already happened
        return
