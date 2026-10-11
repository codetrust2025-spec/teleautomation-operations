"""Fill a candidate's missing details from their resume -- and nothing else.

A resume is read by an AI node when it is uploaded, and the reading used to be
shown to an operator who could press "Fill fields". That button overwrote the
name, technology, phone and email of an existing candidate whether or not they
were already right, and nothing filled a gap on its own.

The rule here is narrower and safer:

* only an **empty** field (or template filler such as "xyz@gmail.com") is
  filled. Whatever else the record holds -- a cut-short name, an incomplete
  number, a different email -- is the person's to replace: the edit form shows
  it beside the resume's value to accept or keep (11 Oct). A blank from the
  resume never replaces anything;
* a resume whose phone differs from the candidate's valid one fills nothing,
  but may still be compared field by field (`compare_only`);
* only a **complete** reading is used: a name needs two words of two letters
  or more, a phone ten digits;
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
#: Local parts that are template filler, not a person: a resume built from a
#: template ("xyz@gmail.com") was read at 85 confidence and would have been written.
_PLACEHOLDER_LOCALS = frozenset({
    "xyz", "abc", "abcd", "xxx", "test", "testing", "example", "sample", "name", "yourname",
    "youremail", "your.name", "your.email", "email", "mail", "username", "user", "demo",
    "noname", "na", "none", "firstname.lastname", "firstnamelastname", "john.doe", "johndoe",
})
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


#: Words of resume section headings. A line containing one is never a person's
#: name: on 11 Oct "SUMMARY OF QUALIFICATIONS" was taken for a candidate's name and
#: the resume refused as someone else's.
_HEADING_WORDS = frozenset({
    "summary", "qualification", "qualifications", "resume", "curriculum", "vitae", "cv", "biodata",
    "objective", "objectives", "profile", "professional", "experience", "experiences", "education",
    "educational", "skills", "skill", "declaration", "contact", "details", "projects", "project",
    "certifications", "certification", "achievements", "employment", "career", "responsibilities",
    "overview", "personal", "technical", "academic", "work", "history", "competencies", "expertise",
    "strengths", "hobbies", "languages", "references", "address", "email", "mobile", "phone", "gmail",
})


def person_name(value: Any) -> str:
    """A person's name as the resume writes it -- one word or more (initials may
    be added), letters only, not a placeholder or a section heading -- in title
    case when written in capitals; "" otherwise. One word is a whole name: most
    candidates here are known by one ("Ravina")."""
    text = " ".join(_clean(value).split())
    if not text or text.lower() in _PLACEHOLDER_NAMES:
        return ""
    if re.search(r"[^A-Za-z\s.\-']", text):
        return ""
    words = re.findall(r"[A-Za-z]+", text)
    if not any(len(word) >= 2 for word in words) or len(words) > 6:
        return ""
    if any(word.lower() in _HEADING_WORDS for word in words):
        return ""
    return text.title() if text.isupper() else text


def complete_name(value: Any) -> str:
    """A full name -- at least two words of two letters or more (initials may be
    added) and not a placeholder -- or "" when the value is not one."""
    text = " ".join(_clean(value).split())
    if text.lower() in _PLACEHOLDER_NAMES:
        return ""
    words = re.findall(r"[A-Za-z]+", text)
    if sum(1 for word in words if len(word) >= 2) < 2:
        return ""
    return text


def _close(a: str, b: str) -> bool:
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def equivalent_names(a: str, b: str) -> bool:
    """The same words, in any order, spelling variants aside:
    "Kolla Ragavendrarao" and "Raghavendrarao Kolla" are one name."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or len(ta) != len(tb):
        return False
    remaining = list(tb)
    for token in ta:
        match = next((other for other in remaining if _close(token, other)), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def _cut_short(token: str, full: str) -> bool:
    """`token` is `full` cut short: "raghava" of "raghavendrarao". Four letters at
    least, so a short real name ("Ram" beside "Ramesh") is not taken for one."""
    if len(token) < 4 or len(token) >= len(full):
        return False
    head = full[: len(token)]
    return head == token or difflib.SequenceMatcher(None, token, head).ratio() >= 0.8


def partial_name(current: str, complete: str) -> bool:
    """Is `current` the complete name `complete` cut short? Every word of it is
    a word of the complete name or a cut-short one, and something is missing:
    a word is cut short, or whole words are absent. The same name in another
    order or spelling is not partial."""
    partial, _shortened = _partial_shape(current, complete)
    return partial


def shortened_name(current: str, complete: str) -> bool:
    """`current` is the complete name with a word cut short ("Kolla Raghava" of
    "Raghavendrarao Kolla"). Leaving a whole word out does not count."""
    partial, shortened = _partial_shape(current, complete)
    return partial and shortened


def _partial_shape(current: str, complete: str) -> tuple[bool, bool]:
    tc, tf = _tokens(current), _tokens(complete)
    if not tc or not complete_name(complete) or equivalent_names(current, complete):
        return False, False
    remaining = list(tf)
    shortened = False
    for token in tc:
        match = next((other for other in remaining if _close(token, other)), None)
        if match is None:
            match = next((other for other in remaining if _cut_short(token, other)), None)
            if match is None:
                return False, False
            shortened = True
        remaining.remove(match)
    return shortened or bool(remaining), shortened


def _phone_digits(value: Any) -> str:
    digits = re.sub(r"\D", "", _clean(value))
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    return digits[1:] if len(digits) == 11 and digits.startswith("0") else digits


def identity_overwrite_problem(
    stored: dict[str, Any],
    incoming: dict[str, Any],
    *,
    normalise_phone,
    allow_shorter_name: bool = False,
) -> dict[str, Any]:
    """Why a save may not replace the saved phone or name, or {} when it may.

    * The phone changes only to a full 10-digit mobile (or is cleared): "77995"
      never replaces a saved number.
    * A full saved name is not replaced by itself with a word cut short ("Kolla
      Raghava" for "Raghavendrarao Kolla") unless the person confirms it
      (`allow_shorter_name`). Dropping or changing whole words is not affected.
    """
    if "phone" in incoming:
        new_phone = _clean(incoming.get("phone"))
        if (
            new_phone
            and _phone_digits(new_phone) != _phone_digits(stored.get("phone"))
            and not valid_phone(new_phone, normalise_phone)
        ):
            return {
                "message": f"{new_phone} is not a full 10-digit mobile number. The saved number was not changed.",
                "invalid_phone": True,
            }
    if "name" in incoming and not allow_shorter_name:
        new_name = " ".join(_clean(incoming.get("name")).split())
        saved_name = " ".join(_clean(stored.get("name")).split())
        if complete_name(saved_name) and shortened_name(new_name, saved_name):
            return {
                "message": f"“{new_name}” looks like the saved name “{saved_name}” cut short.",
                "shorter_name": True,
            }
    return {}


def valid_email(value: str) -> str:
    email = _clean(value).lower()
    if not _EMAIL.match(email):
        return ""
    local, domain = email.split("@", 1)
    if domain in _JUNK_EMAIL_DOMAINS or local in _PLACEHOLDER_LOCALS:
        return ""
    return email


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
        # Ten digits, a mobile: "77995" or a nine-digit number is not a phone.
        return bool(valid_phone(text, normalise_phone))
    if field == "technology":
        return canonical_technology(text) not in {"", "Unspecified"}
    if field == "name":
        return text.lower() not in _PLACEHOLDER_NAMES and len(_tokens(text)) >= 1
    return bool(text)


def _fillable(field: str, value: Any, *, canonical_technology) -> bool:
    """Empty, or a placeholder rather than real data."""
    text = _clean(value)
    if not text:
        return True
    if field == "name":
        return text.lower() in _PLACEHOLDER_NAMES
    if field == "email":
        local, _, domain = text.lower().partition("@")
        return local in _PLACEHOLDER_LOCALS or domain in _JUNK_EMAIL_DOMAINS
    if field == "technology":
        return canonical_technology(text) in {"", "Unspecified"}
    return False


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
    found_name = person_name(extraction.get("candidate_name"))
    name_known = usable("name", current_name, normalise_phone=normalise_phone, canonical_technology=canonical_technology)
    current_phone = valid_phone(candidate.get("phone"), normalise_phone)
    resume_phone = valid_phone(extraction.get("phone"), normalise_phone)
    # The candidate's own 10-digit number on the resume says it is theirs, even
    # when the name was read badly; the name is then not needed to confirm it.
    phone_confirms = bool(current_phone and current_phone == resume_phone)
    if name_known and found_name and not same_person(current_name, found_name) and not phone_confirms:
        return {"fill": {}, "skipped": {}, "refused": "the name on the resume does not match this candidate"}
    if name_known and not found_name and not phone_confirms:
        # Nothing to confirm the resume is theirs: fill only what cannot be
        # someone else's -- which is nothing identifying.
        return {"fill": {}, "skipped": {}, "refused": "the resume gave no name to confirm it is this candidate's"}

    # A resume whose own phone number differs from a candidate's valid one is
    # not evidence about that candidate: a copied template, or someone else's
    # contact block. Nothing it says about them is trusted.
    if current_phone and resume_phone and current_phone != resume_phone:
        # Nothing is filled from it, but the form may still compare it, field
        # by field, for the person to accept or keep.
        return {"fill": {}, "skipped": {}, "refused": "",
                "compare_only": "the phone number on the resume differs from this candidate's"}


    others = list(others)
    taken_emails = {valid_email(o.get("email")) for o in others} - {""}
    taken_phones = {normalise_phone(o.get("phone")) for o in others} - {""}
    known = {canonical_technology(t) for t in known_technologies} - {"", "Unspecified"}

    for field in FIELDS:
        # Only an empty field (or template filler) is filled. Anything the record
        # holds -- even a cut-short name or an incomplete number -- is the
        # person's to replace: the form compares it with the resume instead.
        if not _fillable(field, candidate.get(field), canonical_technology=canonical_technology):
            continue
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
            if found_name:
                fill[field] = found_name
            elif _clean(extraction.get("candidate_name")):
                skipped[field] = "the name on the resume is not a person's name"
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
