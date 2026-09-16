"""What an assessment mail actually commits the candidate to, and when.

An interview invitation names a time. An assessment invitation usually names a
*window* -- "Start time 15-Sep 07:05 PM, End time 16-Sep 08:30 AM, Time limit 60
min(s)" -- which says the candidate may sit a one-hour test at any point inside
thirteen hours. The two are not the same commitment, and the roster only
understands the first, so this module turns the second into one: a single slot
the operator can see, chosen by rule rather than invented by the model.

The rules, in the order they apply:

* A stated assessment time is taken as given, for the stated duration.
* A window is resolved to the first free slot from 6:00 PM on the window's own
  date -- the hour candidates actually sit tests -- never earlier than the
  window opens and never earlier than now.
* The whole slot must finish before the deadline and must not overlap a booking
  the candidate already holds.
* If no slot satisfies that, nothing is booked and the reason says which rule
  refused. A time is never invented to fill the gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type, datetime, time as time_type, timedelta
import re
from typing import Any
from zoneinfo import ZoneInfo

#: Every stored slot is Asia/Kolkata, as the roster reads them.
LOCAL_ZONE = ZoneInfo("Asia/Kolkata")

#: When a window says nothing about which hour, candidates sit tests in the
#: evening. This is the earliest the resolver will choose on its own; a window
#: opening later than this wins, because an assessment cannot start before it
#: is released.
EVENING_START = time_type(18, 0)

#: A test with no stated length is treated as an hour, which is what the
#: platforms overwhelmingly send.
DEFAULT_DURATION_MINUTES = 60

#: Candidate start times are tried on this grid, so a blocked slot moves to a
#: readable time rather than an arbitrary offset.
STEP_MINUTES = 15

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Tuesday, 15-Sep-2026 07:05 PM (IST)", "15 September 2026, 7:05 PM",
# "2026-09-15 19:05", "15/09/2026 07:05 PM" -- the shapes assessment platforms
# actually send. A bare date with no clock is accepted too: the window rule
# needs the day, and the hour is exactly what it is allowed to decide.
_DATE = (r"(?P<day>[0-3]?\d)[-/\s]+(?P<month>[A-Za-z]{3,9}|[01]?\d)[-/\s]+(?P<year>20\d{2})"
         r"|(?P<iso_year>20\d{2})-(?P<iso_month>[01]\d)-(?P<iso_day>[0-3]\d)")
_CLOCK = r"(?P<hour>[0-2]?\d)[:.](?P<minute>[0-5]\d)(?:\s*(?P<meridiem>[AaPp]\.?[Mm]\.?))?"
_DATETIME_RE = re.compile(rf"(?:{_DATE})(?:[,\s]+(?:at\s+)?{_CLOCK})?")
_DURATION_RE = re.compile(
    r"(?:time\s*limit|duration|test\s*duration|assessment\s*duration)\s*[:\-]?\s*"
    r"(?P<value>\d{1,3})\s*(?P<unit>min|minute|minutes|min\(s\)|hour|hours|hr|hrs)",
    re.I)
# Each label must be an actual field heading. "start" on its own also appears
# in "before you start the assessment" and in the "Start Assessment" link, and a
# bare match there would read the next date it could find as an opening time.
_LABELLED = {
    "start": r"(?:start\s*(?:time|date|date\s*&?\s*time)|assessment\s*starts?|window\s*opens?|available\s*from|valid\s*from)",
    "deadline": r"(?:end\s*(?:time|date|date\s*&?\s*time)|due\s*(?:date|by)|deadline|last\s*date|expires?\s*(?:on|at)?|complete\s*(?:it\s*)?(?:by|before)|valid\s*(?:till|until|upto|up\s*to)|window\s*closes?)",
    "scheduled": r"(?:scheduled\s*(?:on|at|for)|assessment\s*(?:is\s*)?scheduled|appointment\s*(?:on|at))",
}
# Platform mail arrives as one flattened line, so the name runs into the next
# field unless the following heading stops it.
_ASSESSMENT_NAME_RE = re.compile(
    r"assessment\s*name\s*[:\-]\s*(?P<name>.{1,80}?)"
    r"(?=\s*(?:number\s*of|no\.?\s*of|start\s*time|end\s*time|time\s*limit|duration|$)|[\n\r<])",
    re.I | re.S)


def _labelled_datetime(text: str, label: str) -> datetime | None:
    """The first datetime that follows one of this label's phrasings."""
    for match in re.finditer(_LABELLED[label], text, re.I):
        tail = text[match.end(): match.end() + 120]
        found = _DATETIME_RE.search(tail)
        if found:
            parsed = _to_datetime(found)
            if parsed:
                return parsed
    return None


def _to_datetime(match: re.Match[str]) -> datetime | None:
    groups = match.groupdict()
    try:
        if groups.get("iso_year"):
            day = date_type(int(groups["iso_year"]), int(groups["iso_month"]), int(groups["iso_day"]))
        else:
            raw_month = str(groups.get("month") or "")
            month = _MONTHS.get(raw_month[:3].lower()) if raw_month[:1].isalpha() else int(raw_month)
            if not month:
                return None
            day = date_type(int(groups["year"]), month, int(groups["day"]))
    except (TypeError, ValueError):
        return None
    hour_text = groups.get("hour")
    if hour_text is None:
        return datetime.combine(day, time_type(0, 0), LOCAL_ZONE)
    hour, minute = int(hour_text), int(groups["minute"])
    meridiem = (groups.get("meridiem") or "").replace(".", "").upper()
    if meridiem == "AM" and hour == 12:
        hour = 0
    elif meridiem == "PM" and hour != 12:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return datetime.combine(day, time_type(hour, minute), LOCAL_ZONE)


def _duration_minutes(text: str) -> int:
    match = _DURATION_RE.search(text)
    if not match:
        return DEFAULT_DURATION_MINUTES
    value = int(match.group("value"))
    if match.group("unit").lower().startswith(("hour", "hr")):
        value *= 60
    return value if 5 <= value <= 12 * 60 else DEFAULT_DURATION_MINUTES


@dataclass
class AssessmentWindow:
    """The commitment an assessment mail describes, as far as it states one."""

    start: datetime | None = None
    deadline: datetime | None = None
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    #: True when the mail names a time to sit the test rather than a window.
    exact: bool = False
    name: str = ""
    evidence: dict[str, str] = field(default_factory=dict)

    @property
    def has_date(self) -> bool:
        return bool(self.start or self.deadline)


def parse_window(subject: str, body: str, *, attachments: list[dict[str, Any]] | None = None,
                 result: dict[str, Any] | None = None) -> AssessmentWindow:
    """Read the window out of the mail, falling back to the model's own reading.

    The mail is read first and the model second. These bodies state the window
    in labelled fields that a regular expression reads exactly, while the model
    -- having no assessment vocabulary to answer in -- reported the window's
    opening time as an interview time, which is how an assessment ended up
    proposing a 7:05 PM interview nobody had scheduled.
    """
    text = "\n".join([str(subject or ""), str(body or "")]
                     + [str(item.get("text") or "") for item in (attachments or [])])
    start = _labelled_datetime(text, "scheduled") or _labelled_datetime(text, "start")
    deadline = _labelled_datetime(text, "deadline")
    duration = _duration_minutes(text)
    name_match = _ASSESSMENT_NAME_RE.search(text)
    interview = ((result or {}).get("interview") or {}) if isinstance(result, dict) else {}
    if start is None and interview.get("date"):
        found = _DATETIME_RE.search(f"{interview.get('date')} {interview.get('time') or ''}".strip())
        start = _to_datetime(found) if found else None
    if start and deadline and deadline <= start:
        deadline = None
    exact = bool(start) and (
        deadline is None or (deadline - start) <= timedelta(minutes=duration))
    evidence = {}
    if start:
        evidence["start"] = start.isoformat()
    if deadline:
        evidence["deadline"] = deadline.isoformat()
    return AssessmentWindow(
        start=start, deadline=deadline, duration_minutes=duration, exact=exact,
        name=(name_match.group("name").strip() if name_match else ""), evidence=evidence,
    )


@dataclass
class SlotDecision:
    """Either a slot to book, or the rule that refused to invent one."""

    date: str = ""
    time: str = ""
    time_end: str = ""
    reason_code: str = ""
    reason: str = ""

    @property
    def bookable(self) -> bool:
        return bool(self.date and self.time)


def _overlaps(start: datetime, end: datetime, existing: list[dict[str, Any]]) -> bool:
    for row in existing or []:
        day = str(row.get("date") or "")[:10]
        if day != start.date().isoformat():
            continue
        try:
            other_start = datetime.combine(
                date_type.fromisoformat(day),
                datetime.strptime(str(row.get("time") or "")[:5], "%H:%M").time(), LOCAL_ZONE)
        except ValueError:
            continue
        raw_end = str(row.get("time_end") or "")[:5]
        try:
            other_end = datetime.combine(
                other_start.date(), datetime.strptime(raw_end, "%H:%M").time(), LOCAL_ZONE)
        except ValueError:
            other_end = other_start + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        if other_end <= other_start:
            other_end = other_start + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        if start < other_end and other_start < end:
            return True
    return False


def _ceil_to_step(moment: datetime) -> datetime:
    remainder = moment.minute % STEP_MINUTES
    moment = moment.replace(second=0, microsecond=0)
    return moment if remainder == 0 else moment + timedelta(minutes=STEP_MINUTES - remainder)


def resolve_slot(window: AssessmentWindow, *, existing: list[dict[str, Any]] | None = None,
                 now: datetime | None = None) -> SlotDecision:
    """Choose the slot to book for this window, or say why none was chosen."""
    current = (now or datetime.now(LOCAL_ZONE)).astimezone(LOCAL_ZONE)
    duration = timedelta(minutes=window.duration_minutes)
    if not window.has_date:
        return SlotDecision(
            reason_code="ASSESSMENT_WITHOUT_DATE",
            reason="The assessment mail names no date, so no slot was booked.")
    if window.deadline and window.deadline <= current:
        return SlotDecision(
            reason_code="ASSESSMENT_WINDOW_CLOSED",
            reason=f"The assessment window closed at {window.deadline:%d %b %Y %H:%M}.")

    if window.exact and window.start:
        start = window.start
        if start <= current:
            return SlotDecision(
                reason_code="ASSESSMENT_TIME_PASSED",
                reason=f"The assessment time {start:%d %b %Y %H:%M} has already passed.")
        if window.deadline and start + duration > window.deadline:
            # A mail that states a start, an end, and a time limit longer than
            # the two allow is describing something nobody can sit. Booking the
            # stated start would put a slot past the deadline it must respect.
            return SlotDecision(
                reason_code="ASSESSMENT_WINDOW_TOO_SHORT",
                reason=f"The window is shorter than the {window.duration_minutes}-minute assessment.")
        if _overlaps(start, start + duration, existing or []):
            return SlotDecision(
                reason_code="ASSESSMENT_SLOT_CONFLICT",
                reason=f"{start:%d %b %H:%M} is already taken by another booking.")
        return _slot(start, duration)

    # A window: the day is the mail's, the hour is this rule's.
    #
    # The evening of the assessment's own date is tried first, because that is
    # the hour the rule is written around. These windows routinely run past
    # midnight -- 7:05 PM to 8:30 AM -- so the rest of the window is tried only
    # when that evening is gone: fully booked, or already behind us because the
    # mail arrived overnight. Without that second pass a reminder read at 7 AM
    # would report "no slot" while an hour of its window remained.
    opening = window.start or window.deadline
    day = opening.date()
    evening = datetime.combine(day, EVENING_START, LOCAL_ZONE)
    limit = window.deadline or datetime.combine(day, time_type(23, 59), LOCAL_ZONE)
    midnight = datetime.combine(day + timedelta(days=1), time_type(0, 0), LOCAL_ZONE)
    passes = (
        (max(evening, window.start or evening, _ceil_to_step(current)), min(limit, midnight)),
        (max(_ceil_to_step(current), midnight), limit),
    )
    for earliest, upper in passes:
        # The first candidate is the moment the window allows, not a rounded
        # one: a window opening at 7:05 PM should be offered 7:05 PM. Only a
        # blocked slot moves, and then by a readable step.
        candidate = earliest
        while candidate + duration <= upper:
            if not _overlaps(candidate, candidate + duration, existing or []):
                return _slot(candidate, duration)
            candidate = candidate + timedelta(minutes=STEP_MINUTES)
    if (window.start or evening) + duration > limit:
        return SlotDecision(
            reason_code="ASSESSMENT_WINDOW_TOO_SHORT",
            reason=f"The window is shorter than the {window.duration_minutes}-minute assessment.")
    return SlotDecision(
        reason_code="ASSESSMENT_NO_FREE_SLOT",
        reason=f"Every {window.duration_minutes}-minute slot before "
               f"{limit:%d %b %H:%M} is already booked.")


def _slot(start: datetime, duration: timedelta) -> SlotDecision:
    end = start + duration
    return SlotDecision(
        date=start.date().isoformat(), time=f"{start:%H:%M}", time_end=f"{end:%H:%M}")
