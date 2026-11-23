"""The interview round a booking's own evidence states, or none.

A round is never guessed (owner, 23 Nov 2026). This module reads the text a
booking already has -- the invite's subject or calendar title, the email body,
a screenshot's OCR text, operator notes -- and returns a round only when that
text names one in words that cannot mean anything else: "Round 1", "Level 1",
"1st Technical Level", "L2 Interview", "R1-<name>" at the head of a subject,
"Tech 1", "HR round", "Final interview", "Screening call".

What it does not do:

* "Technical round" / "Technical interview" with no number is not a round
  (the same rule `normalise_interview_round` has kept since September).
* "Managerial", "TP1", "client discussion" and other labels off the list are
  not mapped onto one.
* When the sources name two different rounds the answer is none: the booking
  needs a person to look, not a pick.

Anything without an answer is "Needs Review" in Daily Ops.
"""
from __future__ import annotations

import re
from typing import Iterable

NEEDS_REVIEW_LABEL = "Needs Review"
NEEDS_REVIEW_STATUS = "needs_review"

_ORDINALS = {"first": "1", "second": "2", "third": "3", "i": "1", "ii": "2", "iii": "3"}

_N = r"([1-3]|first|second|third)"
# "Level II" in capitals only; "Round I" is left out because "next round I
# will share" is a sentence, not L1.
_N_ROMAN = r"([1-3]|(?-i:I{1,3})|first|second|third)"
_INTERVIEW_CONTEXT = re.compile(r"\b(interview|round|discussion)\b", re.I)

#: (pattern, strong). A strong pattern names a round by itself. A weak one
#: ("Level 3", "Tech 2", "L2") also fits a job description ("Level 3
#: support"), so it counts only within a few words of interview, round or
#: discussion -- or in a subject/title that is about a meeting.
_NUMBERED = (
    # "Round 1", "Round-2", "Round: 3", "Rnd 1"
    (re.compile(rf"\b(?:round|rnd)\s*[-:#]?\s*{_N}\b", re.I), True),
    # "1st round", "2nd Technical Level", "first round", "second level"
    (re.compile(r"\b([1-3])(?:st|nd|rd)\s+(?:technical\s+)?(?:round|level|interview)\b", re.I), True),
    (re.compile(r"\b(first|second|third)\s+(?:technical\s+)?(?:round|level)\b", re.I), True),
    # "Level 1", "Level-II"
    (re.compile(rf"\blevel\s*[-:]?\s*{_N_ROMAN}\b", re.I), False),
    # "Technical Round 1" is caught above; "Tech 1", "Technical-2"
    (re.compile(r"\btech(?:nical)?\s*[-:]?\s*([1-3])\b", re.I), False),
    # "L1", "L 2"
    (re.compile(r"\bL\s?([1-3])\b"), False),
)
#: "R1-<name>": an "R" number heading the subject or calendar title.
_SUBJECT_R = re.compile(r"^\s*R([1-3])\s*[-:|]", re.I)

_NAMED = (
    # "HR" in capitals only (not "hr" in a URL); the word beside it any case.
    (re.compile(r"\bHR\s+(?i:interview|round|discussion)\b|\b(?i:interview|round)\s*[-:]?\s*HR\b"), "HR"),
    (re.compile(r"\bfinal\s+(?:interview|round|discussion)\b|\b(?:interview|round)\s*[-:]?\s*final\b", re.I), "Final"),
    (re.compile(r"\bscreening\s+(?:interview|round|call|discussion)\b|\b(?:interview|round)\s*[-:]?\s*screening\b", re.I), "Screening"),
)
#: A title about a meeting at all ("EY Interview | Tech 1", "L1 discussion").
_TITLE_ROUNDISH = re.compile(r"\b(interview|round|discussion|meeting|call|level|panel)\b", re.I)
#: A subject or invite title that opens with "Screening".
_SUBJECT_SCREENING = re.compile(r"^\s*screening\b", re.I)


def _plain(text) -> str:
    # Subjects glue words with "_" and "|" ("CAPGEMINI_L1_ Interview"); a
    # word boundary needs them apart.
    return re.sub(r"[_|–—·]+", " ", str(text or ""))


def _number(token: str) -> str:
    token = token.lower()
    return _ORDINALS.get(token, token)


def rounds_in_text(text, *, is_title: bool = False) -> list[tuple[str, str]]:
    """Every (round, phrase) the text states outright, in order found.

    `is_title` marks an email subject or calendar title, where an opening
    "R1-" or "Screening" names the meeting itself.
    """
    raw = str(text or "")
    plain = _plain(raw)
    found: list[tuple[str, str]] = []
    for pattern, strong in _NUMBERED:
        for m in pattern.finditer(plain):
            if not strong:
                if is_title:
                    if not _TITLE_ROUNDISH.search(plain):
                        continue
                elif not _INTERVIEW_CONTEXT.search(plain[max(0, m.start() - 40):m.end() + 40]):
                    continue
            found.append((f"L{_number(m.group(1))}", m.group(0).strip()))
    for pattern, label in _NAMED:
        for m in pattern.finditer(plain):
            found.append((label, m.group(0).strip()))
    if is_title:
        m = _SUBJECT_R.match(raw)
        if m:
            found.append((f"L{m.group(1)}", m.group(0).strip(" -:|")))
        m = _SUBJECT_SCREENING.match(plain)
        if m:
            found.append(("Screening", m.group(0).strip()))
    return found


def resolve_round(sources: Iterable[tuple[str, object]]) -> dict:
    """Decide a round from named evidence sources.

    `sources` is `(name, text)` pairs; a name ending in "subject" or "title"
    is read as a subject line. Returns::

        {"round": "L1" | "" , "status": "evidenced" | "needs_review",
         "evidence_source": "...", "phrase": "...", "confidence": "high"|"medium"|"",
         "reason": "..."}

    Confidence is "high" when a subject/title states the round and "medium"
    when only a body does. Two different rounds anywhere -> needs review.
    """
    hits: list[tuple[str, str, str, bool]] = []
    checked: list[str] = []
    for name, text in sources:
        if not str(text or "").strip():
            continue
        checked.append(name)
        is_title = str(name).endswith(("subject", "title"))
        for label, phrase in rounds_in_text(text, is_title=is_title):
            hits.append((label, phrase, name, is_title))
    labels = sorted({h[0] for h in hits})
    if len(labels) == 1:
        title_hits = [h for h in hits if h[3]]
        best = title_hits[0] if title_hits else hits[0]
        return {
            "round": labels[0],
            "status": "evidenced",
            "evidence_source": best[2],
            "phrase": best[1],
            "confidence": "high" if title_hits else "medium",
            "reason": f'{best[2]} says "{best[1]}"',
            "sources_checked": checked,
        }
    if labels:
        reason = "sources name different rounds: " + ", ".join(
            f'{label} ("{next(h[1] for h in hits if h[0] == label)}")' for label in labels
        )
    elif checked:
        reason = "no round is named in " + ", ".join(checked)
    else:
        reason = "no stored text to read a round from"
    return {
        "round": "",
        "status": NEEDS_REVIEW_STATUS,
        "evidence_source": "",
        "phrase": "",
        "confidence": "",
        "reason": reason,
        "sources_checked": checked,
    }


def explicit_round(*texts, title: str = "") -> str:
    """The one round a subject/title and body state, or ""."""
    sources = [("title", title)] + [(f"text{i}", t) for i, t in enumerate(texts)]
    return resolve_round(sources)["round"]
