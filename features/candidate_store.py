"""Persistent storage for the Candidates / Profiles tracker.

This replaces the old Google Sheet ("Profiles list update Form"). Every row
that used to live in the sheet is now a record in `data/candidates.json` and
is editable directly from the dashboard.

Schema (one row):

    {
        "id":            "auto-generated short id (string)",
        "name":          "candidate full name",
        "stage":         "in_progress | completed | fail | dropped",
        "technology":    "SAP BASIS | React JS | AWS Admin | ..." (free text),
        "task":          "not_started | in_progress | decision_need | completed",
        "phone":         "10-digit Indian phone or international",
        "email":         "candidate email address (stored lowercase)",
        "reference":     "who referred the lead (free text)",
        "payment":       <int> rupees (0 if blank),
        "date":          "YYYY-MM-DD" (interview slot day when slot_confirmed; else lead logged date),
        "logged_date":   "YYYY-MM-DD" (when the lead was first logged — never overwritten by slot assign),
        "time":          "HH:MM" 24h (blank ok),
        "time_end":      "HH:MM" 24h interview slot end (blank ok),
        "slot_confirmed": false until owner + initial payment (handler workspace rule),
        "slot_confirmed_at": ISO timestamp when slot was confirmed (blank ok),
        "slots_group_posted": true after slot screenshot posted in Interview slots WA group,
        "interview_attendee": "Nikhila | Bhavana | Tool — who supported the live interview (set when marking attendance)",
        "interview_attendance_status": "attended | not_attended | cancelled | rescheduled | re_service | blank (pending)",
        "interview_attendance_remark": "optional note when logging attendance",
        "interview_attended": legacy bool — true when status is attended,
        "interview_attended_at": ISO timestamp when attendance was logged,
        "interview_attended_by": handler name who logged attendance,
        "purpose":       "interview_support | work_support | experience_docs | other (Excel PURPOSE column)",
        "expenses":      "free text — e.g. '12000 GYM', '3000 fuel' (was 'Expenses PAVAN')",
        "notes":         "free text — any extra context",
        "created_at":    ISO timestamp (set on insert),
        "updated_at":    ISO timestamp (set on every patch),
    }

Everything is intentionally JSON-on-disk with a coarse lock so it matches
the rest of the project (`crm/leads.json`, `ai_smart_reply.json`, etc.).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import date as _date, datetime, timedelta, timezone
from threading import Lock

from core.config import DATA_DIR
from features.candidate_attachments import (
    ATTACHMENT_FIELDS,
    AttachmentType,
    parse_attachment_type,
    partition_candidate_attachments,
)
from features import payment_allocation
from features import payment_receipts

_FILE = os.path.join(DATA_DIR, "candidates.json")
# Each candidate gets its own folder under here so we never accidentally
# mix screenshots between people, even if filenames collide.
PROOFS_DIR = os.path.join(DATA_DIR, "candidates_proofs")
RESUMES_DIR = os.path.join(DATA_DIR, "candidates_resumes")
_lock = Lock()
_load_cache: dict | None = None
_load_cache_at: float = 0.0
_LOAD_CACHE_TTL = 15.0  # seconds — avoids repeated PG reads per dashboard refresh

# Allowed image MIME types for payment-proof uploads. We deliberately
# keep this short — the dashboard is meant for screenshots / receipts,
# not arbitrary file uploads.
_ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/jpg":  "jpg",
    "image/png":  "png",
    "image/webp": "webp",
    "image/gif":  "gif",
    "image/heic": "heic",
    "image/heif": "heif",
}
_ALLOWED_RESUME_MIME = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
}
MAX_PROOF_BYTES = 8 * 1024 * 1024  # 8 MB per screenshot
MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10 MB per resume file

VALID_STAGES = {"in_progress", "completed", "fail", "dropped"}
VALID_TASKS = {"not_started", "in_progress", "decision_need", "completed"}

# Auto-generated screenshot / import placeholders — never store in candidate notes.
_SCREENSHOT_NOTE_NOISE = (
    "microsoft teams — read from screenshot",
    "microsoft teams - read from screenshot",
    "zoom — read from screenshot",
    "zoom - read from screenshot",
    "google calendar — read from screenshot",
    "google calendar - read from screenshot",
    "read from screenshot",
    "manual entry on submit-slot form",
    "candidate screenshot upload",
    "candidate manual slot entry",
    "public-upload",
)


def sanitize_candidate_notes(text: str) -> str:
    """Drop platform/import boilerplate from notes; keep real operator text."""
    raw = (text or "").strip()
    if not raw:
        return ""
    kept: list[str] = []
    for line in raw.splitlines():
        chunk = line.strip()
        if not chunk:
            continue
        lower = chunk.lower()
        if lower in _SCREENSHOT_NOTE_NOISE:
            continue
        for noise in _SCREENSHOT_NOTE_NOISE:
            if noise in lower:
                chunk = re.sub(re.escape(noise), "", chunk, flags=re.IGNORECASE).strip()
                chunk = re.sub(r"^[·\-–—\s]+|[·\-–—\s]+$", "", chunk).strip()
                lower = chunk.lower()
        if chunk and lower not in _SCREENSHOT_NOTE_NOISE:
            kept.append(chunk)
    return "\n".join(kept).strip()


def purge_screenshot_placeholder_notes() -> dict[str, int]:
    """One-shot cleanup: remove stored Teams/screenshot boilerplate from every candidate."""
    data = _load(force=True)
    rows = data.get("candidates") or []
    changed = 0
    for row in rows:
        old = row.get("notes") or ""
        new = sanitize_candidate_notes(old)
        if new != old:
            row["notes"] = new
            row["updated_at"] = _now_iso()
            changed += 1
    if changed:
        data["candidates"] = rows
        _save(data)
    return {"changed": changed, "total": len(rows)}

# Every candidate is expected to pay this much as the baseline onboarding
# amount. Anything less is tracked as a pending balance with a required
# follow-up remark.
#
# Two baselines because we have two acquisition channels:
#   - direct  (default): ₹20,000 per profile
#   - consultancy        : ₹15,000 per profile (consultancy partner already
#                          takes their cut, so we charge the client less)
# A per-candidate boolean `consultancy` flips the default. The operator
# can still override `expected_payment` manually for either path.
DEFAULT_EXPECTED_PAYMENT       = 20_000
CONSULTANCY_EXPECTED_PAYMENT   = 15_000
ROUND_WISE_EXTERNAL_PAYMENT = 5_000
ROUND_WISE_INTERNAL_PAYMENT = 9_000
BGV_CERTIFICATES_PAYMENT = 30_000
# Legacy aliases (domestic/non_domestic → external/internal)
ROUND_WISE_DOMESTIC_PAYMENT = ROUND_WISE_EXTERNAL_PAYMENT
ROUND_WISE_NON_DOMESTIC_PAYMENT = ROUND_WISE_INTERNAL_PAYMENT
# Minimum initial payment before a handler may mark the interview slot confirmed.
PROFILE_SERVICE_SLOT_MIN_PAYMENT = 10_000

VALID_SERVICE_TYPES = {"profile_service", "round_wise"}
VALID_INTERVIEW_SCOPES = {"external", "internal"}
VALID_PURPOSES = {"interview_support", "work_support", "experience_docs", "other"}
VALID_INTERVIEW_ROUNDS = frozenset({
    "L1", "L2", "HR", "Final", "Screening",
})
INTERVIEW_ATTENDANCE_STATUSES = frozenset({
    "attended",
    "not_attended",
    "cancelled",
    "rescheduled",
    # Admin-only marker that grants one free repeat interview. It is never
    # surfaced on the candidate portal and never counts as attendance.
    "re_service",
})


def baseline_for(consultancy: bool) -> int:
    """The default rupee baseline a candidate is expected to pay."""
    return CONSULTANCY_EXPECTED_PAYMENT if consultancy else DEFAULT_EXPECTED_PAYMENT


def baseline_for_service(
    service_type: str,
    *,
    consultancy: bool = False,
    interview_scope: str = "external",
    bgv_certificates: bool = False,
) -> int:
    if service_type == "round_wise":
        scope = _normalise_interview_scope(interview_scope)
        base = (
            ROUND_WISE_INTERNAL_PAYMENT
            if scope == "internal"
            else ROUND_WISE_EXTERNAL_PAYMENT
        )
    else:
        base = baseline_for(consultancy)
    return base + (BGV_CERTIFICATES_PAYMENT if bgv_certificates else 0)

# The referrer (handler) is paid this share of every rupee the client pays
# the business. The operator does not log commissions by hand — they're
# computed from the candidate's `payment` field. The handler_expenses
# ledger now only tracks money already paid OUT (commission disbursements,
# travel, food etc.) — net = auto_earnings − paid_out.
#
# Commission is based on eligible cash received, capped at the agreed client
# charge. Charging below the prescribed tariff does not reduce the referrer's
# percentage; the agreed deal itself already limits the commissionable amount.
HANDLER_COMMISSION_PCT = 50
PROFILE_CLOSURE_COMPLIMENTARY_AMOUNT = 5_000
PROFILE_CLOSURE_ADMIN_REFERENCE = "Thrilok"

_log = logging.getLogger(__name__)


class AccountingSourceUnavailable(RuntimeError):
    """A store a payable balance depends on could not be read.

    Raised internally so a missing source takes the same path as a corrupt
    one, and both end up reported rather than silently treated as zero.
    """


# Stores that a handler's payable balance is computed from. Losing any of them
# does not break the arithmetic — it quietly removes an offset, which is how
# the August 2026 opening balances came to be overstated after the service
# split. Anything added here must also be registered for migration.
REQUIRED_ACCOUNTING_SOURCES = ("handler_expenses", "handler_salaries",
                               "payment_verification_ledger")


def unavailable_accounting_sources() -> list[str]:
    """Which required accounting stores cannot be read right now."""
    missing: list[str] = []
    try:
        from features import handler_expenses as _he
        if not _he.store_available():
            missing.append("handler_expenses")
    except Exception:
        missing.append("handler_expenses")
    try:
        from features import handler_salaries as _hs
        if not _hs.store_available():
            missing.append("handler_salaries")
    except Exception:
        missing.append("handler_salaries")
    try:
        from features.payment_verification_engine import ledger_available
        if not ledger_available():
            missing.append("payment_verification_ledger")
    except Exception:
        missing.append("payment_verification_ledger")
    return missing

# Owners / admins — not handler commission recipients (hidden from payout UI).
HANDLER_PAYOUT_EXCLUDED_REF_KEYS = frozenset({"ravinder"})

# Always offered in the Reference dropdown (even before their first lead).
HANDLER_REFERENCE_PRESETS: tuple[str, ...] = (
    "Charan",
    "Ravinder",
)

# WhatsApp interview-slots group — always offer in public submit-slot dropdown.
PUBLIC_SLOT_BOOKER_NAMES: tuple[str, ...] = (
    "Ravali",
    "Gangadhar",
    "Raja Gopal",
    "Vaishnavi",
    "Adivi Satyanarayana",
    "Manu",
    "Keerthana",
    "Ram Charan M S",
    "Abilash Perla",
    "Gopichand",
    "KALESHWAR",
    "Thummala Karunakar",
    "Shailaja",
)

# No longer booking slots via /submit-slot (placed, dropped, etc.).
PUBLIC_SLOT_BOOKING_EXCLUDED: tuple[str, ...] = (
    "Farhana",
)


def prescribed_baseline(row: dict) -> int:
    """Tariff before any manual expected_payment override."""
    consultancy = bool(row.get("consultancy", False))
    service_type = _normalise_service_type(row.get("service_type"), row)
    interview_scope = _normalise_interview_scope(row.get("interview_scope"), row)
    return baseline_for_service(
        service_type,
        consultancy=consultancy,
        interview_scope=interview_scope,
        bgv_certificates=_coerce_bool(row.get("bgv_certificates")),
    )


def effective_expected_payment(row: dict) -> int:
    """Agreed client charge for this row (manual override or prescribed baseline)."""
    if is_free_service_candidate(row.get("name") or ""):
        return 0
    consultancy = bool(row.get("consultancy", False))
    service_type = _normalise_service_type(row.get("service_type"), row)
    interview_scope = _normalise_interview_scope(row.get("interview_scope"), row)
    fallback = baseline_for_service(
        service_type,
        consultancy=consultancy,
        interview_scope=interview_scope,
        bgv_certificates=_coerce_bool(row.get("bgv_certificates")),
    )
    expected = int(row.get("expected_payment") or 0)
    if expected <= 0:
        return fallback
    # Stale direct default (₹20k) on a consultancy profile row — use ₹15k channel baseline.
    if (
        service_type == "profile_service"
        and consultancy
        and expected == DEFAULT_EXPECTED_PAYMENT
    ):
        return CONSULTANCY_EXPECTED_PAYMENT
    return expected


def payment_allocation_for(row: dict) -> dict:
    """How this row's verified money splits between service and BGV.

    `expected_payment` already includes the BGV charge, so the service
    obligation is what remains once the pass-through is taken out.
    """
    bgv_enabled = _coerce_bool(row.get("bgv_certificates"))
    bgv_expected = BGV_CERTIFICATES_PAYMENT if bgv_enabled else 0
    expected = effective_expected_payment(row)
    return payment_allocation.allocate(
        verified_total=int(row.get("payment") or 0),
        service_expected=max(0, expected - bgv_expected),
        bgv_expected=bgv_expected,
        bgv_enabled=bgv_enabled,
    )


def referrer_commission_basis(row: dict) -> int:
    """Rupee basis for handler commission before the 50% split.

    BGV certificates are billed by a third-party company and are only
    mediated here, so their ₹30k pass-through amount is never commissionable.
    """
    received = int(row.get("payment") or 0)
    if received <= 0:
        return 0
    # Commission follows the money actually received, not the invoice, and only
    # the service part of it. Subtracting the BGV charge from the total was too
    # blunt: a candidate who owes ₹20,000 service plus ₹30,000 BGV and has paid
    # ₹30,000 has settled the service in full, so ₹20,000 is commissionable —
    # the old arithmetic gave ₹0. Allocation decides which money is which.
    return payment_allocation.commissionable_amount(payment_allocation_for(row))


def referrer_commission_amount(row: dict) -> int:
    return (referrer_commission_basis(row) * HANDLER_COMMISSION_PCT) // 100


def is_closed_profile_service(row: dict) -> bool:
    """Whether a candidate earns the two profile-closure complimentary amounts."""
    return (
        str(row.get("stage") or "").strip().lower() == "completed"
        and _normalise_service_type(row.get("service_type"), row) == "profile_service"
    )


def referrer_complimentary_amount(row: dict) -> int:
    reference = _reference_key(row.get("reference") or "")
    if reference == "unknown" or not is_closed_profile_service(row):
        return 0
    return PROFILE_CLOSURE_COMPLIMENTARY_AMOUNT


def admin_complimentary_amount(row: dict) -> int:
    if not is_closed_profile_service(row):
        return 0
    return PROFILE_CLOSURE_COMPLIMENTARY_AMOUNT


def handler_earning_allocations(row: dict) -> dict[str, int]:
    """Allocate base commission and closure extras to their earning handlers."""
    allocations: dict[str, int] = {}
    reference = _reference_key(row.get("reference") or "")
    if reference != "unknown":
        allocations[reference] = (
            referrer_commission_amount(row) + referrer_complimentary_amount(row)
        )
    admin_bonus = admin_complimentary_amount(row)
    if admin_bonus:
        admin_key = _reference_key(PROFILE_CLOSURE_ADMIN_REFERENCE)
        allocations[admin_key] = allocations.get(admin_key, 0) + admin_bonus
    return {key: amount for key, amount in allocations.items() if amount > 0}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso_date() -> str:
    """Local calendar date. Payout months follow the operator's calendar, not
    UTC — a closure logged late on the 31st belongs to that month."""
    return datetime.now().strftime("%Y-%m-%d")


def canonical_technology(tech: str) -> str:
    """Merge spelling variants (e.g. React Js vs React JS) for roster grouping."""
    raw = (tech or "").strip()
    if not raw:
        return "Unspecified"
    key = " ".join(raw.lower().replace("_", " ").replace("-", " ").split())
    aliases = {
        "react js": "React JS",
        "reactjs": "React JS",
        "angular": "Angular",
        "angularjs": "Angular",
        "mern stack": "MERN stack",
        "aws devops": "AWS DevOps",
        "automation testing": "Automation Testing",
        "testing": "Testing",
        "etl": "ETL",
        "sap basis": "SAP BASIS",
        "unspecified": "Unspecified",
        "data analyst": "Data Analyst",
    }
    return aliases.get(key, raw)


# Who supported the interview (set when marking attendance).
INTERVIEW_ATTENDEE_NAMES = ("Nikhila", "Bhavana", "Tool")
TOOL_PROFILE_CANDIDATE_TECHNOLOGY = "Data Analyst"


def _normalise_candidate_name_key(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def candidate_phone_identity(phone: str | None) -> str:
    """Stable identity; +91, leading zero, spaces and punctuation normalize alike."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) >= 8 else ""


_CANDIDATE_NAME_ALIASES: dict[str, str] = {
    "perla abhilash": "Abilash Perla",
    "abhilash perla": "Abilash Perla",
    "abilash perla": "Abilash Perla",
    "ram charan m s": "Ram Charan M S",
    "ram charan ms": "Ram Charan M S",
    "reddy charan m s": "Ram Charan M S",
    "ganguli1433": "Gangadhar",
    "ganguli": "Gangadhar",
}


def canonical_candidate_name(name: str) -> str:
    """Single display name per person — e.g. PERLA ABHILASH → Abilash Perla."""
    raw = _clean_str(name)
    if not raw:
        return ""
    key = _normalise_candidate_name_key(raw)
    if key in _CANDIDATE_NAME_ALIASES:
        return _CANDIDATE_NAME_ALIASES[key]
    if "perla" in key and ("abhilash" in key or "abilash" in key):
        return "Abilash Perla"
    if ("ram charan" in key or "reddy charan" in key) and ("m s" in key or key.endswith(" ms")):
        return "Ram Charan M S"
    return raw


def candidate_defaults_to_tool_attendee(name: str) -> bool:
    """Keerthana / Satyanarayana — Tool attends and Data Analyst tech stack."""
    return is_free_service_candidate(name)


def is_free_service_candidate(name: str) -> bool:
    """Complimentary Tool-attended profiles — no client payment expected."""
    key = _normalise_candidate_name_key(name)
    if not key:
        return False
    return "keerthana" in key or "satyanarayana" in key


# ── Re-Service ───────────────────────────────────────────────────────────────
# An admin marks a round "Re-Service" when the candidate deserves one free
# repeat interview. The grant lives entirely on the candidate row: the public
# booking flow never names it, and the candidate portal never renders it.
RE_SERVICE_STATUS = "re_service"


def re_service_grant_allowed(role: str) -> bool:
    """Only administrators may issue the one-time Re-Service entitlement."""
    return _clean_str(role).lower() == "admin"


def row_has_re_service_grant(row: dict) -> bool:
    """True when this row carries an unused Re-Service entitlement."""
    if not isinstance(row, dict):
        return False
    return _coerce_bool(row.get("re_service_eligible")) and not _coerce_bool(
        row.get("re_service_consumed")
    )


def find_re_service_grant(
    *,
    name: str = "",
    phone: str = "",
    interview_round: str = "",
    candidate_id: str = "",
    rows: list[dict] | None = None,
) -> dict | None:
    """Locate an unused Re-Service grant for this candidate.

    Matching follows the identifiers the booking form actually supplies:
    candidate id when known, otherwise phone + interview round, falling back to
    phone alone and finally the canonical name. Returns None when the candidate
    has no grant, which keeps every normal booking on the untouched path.
    """
    if rows is None:
        rows = list_candidates(stage="all", month="all")
    grants = [r for r in rows if row_has_re_service_grant(r)]
    if not grants:
        return None

    cid = _clean_str(candidate_id)
    if cid:
        hit = next((r for r in grants if str(r.get("id") or "") == cid), None)
        if hit:
            return hit
        return None

    phone_key = candidate_phone_identity(phone)
    round_label = normalise_interview_round(interview_round)
    if phone_key:
        by_phone = [
            r for r in grants
            if candidate_phone_identity(r.get("phone")) == phone_key
        ]
        if by_phone:
            if round_label:
                exact = next(
                    (
                        r for r in by_phone
                        if normalise_interview_round(r.get("interview_round")) == round_label
                    ),
                    None,
                )
                if exact:
                    return exact
            return by_phone[0]
        return None

    name_key = _normalise_candidate_name_key(canonical_candidate_name(_clean_str(name)))
    if name_key:
        by_name = [
            r for r in grants
            if _normalise_candidate_name_key(r.get("name") or "") == name_key
        ]
        if by_name:
            if round_label:
                exact = next(
                    (
                        r for r in by_name
                        if normalise_interview_round(r.get("interview_round")) == round_label
                    ),
                    None,
                )
                if exact:
                    return exact
            return by_name[0]
    return None


def candidate_is_re_service_eligible(
    *,
    name: str = "",
    phone: str = "",
    interview_round: str = "",
    candidate_id: str = "",
) -> bool:
    """Public-facing predicate used to waive payment for one repeat booking."""
    return find_re_service_grant(
        name=name,
        phone=phone,
        interview_round=interview_round,
        candidate_id=candidate_id,
    ) is not None


def grant_re_service(cid: str, *, by: str = "") -> dict | None:
    """Give this candidate one free re-service interview."""
    data = _load()
    rows = data.get("candidates") or []
    for i, r in enumerate(rows):
        if str(r.get("id") or "") != str(cid):
            continue
        r = dict(r)
        r["re_service_eligible"] = True
        r["re_service_consumed"] = False
        r["re_service_granted_at"] = _now_iso()
        r["re_service_granted_by"] = (by or "").strip()[:120]
        r["re_service_consumed_at"] = ""
        r["updated_at"] = _now_iso()
        rows[i] = r
        data["candidates"] = rows
        _save(data)
        return _with_computed(r)
    return None


def consume_re_service_grant(cid: str, *, booking_id: str = "") -> dict | None:
    """Burn the one-time grant once the re-service interview is completed."""
    data = _load()
    rows = data.get("candidates") or []
    for i, r in enumerate(rows):
        if str(r.get("id") or "") != str(cid):
            continue
        if not _coerce_bool(r.get("re_service_eligible")):
            return _with_computed(dict(r))
        r = dict(r)
        r["re_service_eligible"] = False
        r["re_service_consumed"] = True
        r["re_service_consumed_at"] = _now_iso()
        if booking_id:
            r["re_service_consumed_booking_id"] = str(booking_id)
        r["updated_at"] = _now_iso()
        rows[i] = r
        data["candidates"] = rows
        _save(data)
        return _with_computed(r)
    return None


def is_low_priority_slot_booker(name: str) -> bool:
    """Last-priority bookers via submit-slot — cannot take slots held by others."""
    key = _normalise_candidate_name_key(canonical_candidate_name(name))
    if not key:
        return False
    if "keerthana" in key:
        return True
    if "satyanarayana" in key:
        return True
    if "raja gopal" in key:
        return True
    return False


# Nicknames / WhatsApp handles → substring expected in canonical store name
_CANDIDATE_SEARCH_HINTS: dict[str, tuple[str, ...]] = {
    "satya": ("satyanarayana", "adivi"),
    "keerthana": ("keerthana",),
    "keethana": ("keerthana",),
    "farha": ("farhana",),
    "farhana": ("farhana",),
    "gangadhar": ("gangadhar", "gangadhara"),
    "ganguli": ("gangadhar", "gangadhara"),
    "ravali": ("ravali",),
    "data": ("kaleshwar",),
    "manu": ("manu",),
    "charan": ("ram charan", "reddy charan"),
    "abhilash": ("abilash", "perla"),
    "perla": ("abilash", "perla"),
    "gopi": ("gopichand",),
    "gopichand": ("gopichand",),
    "karunakar": ("karunakar", "thummala"),
    "vaishnavi": ("vaishnavi",),
    "raja": ("raja gopal",),
}


def candidate_matches_search(name: str, query: str) -> bool:
    """True when free-text query matches candidate display name (incl. nicknames)."""
    q = (query or "").strip().lower()
    if not q:
        return True
    n = _normalise_candidate_name_key(name)
    if not n:
        return False
    if q in n:
        return True
    hints = _CANDIDATE_SEARCH_HINTS.get(q)
    if hints:
        return any(h in n for h in hints)
    for part in q.split():
        if len(part) < 2:
            continue
        if part in n:
            return True
        part_hints = _CANDIDATE_SEARCH_HINTS.get(part)
        if part_hints and any(h in n for h in part_hints):
            return True
    return False


def row_candidate_technology(row: dict) -> str:
    stored = canonical_technology(row.get("technology") or "")
    if candidate_defaults_to_tool_attendee(row.get("name") or ""):
        if stored in {"", "Unspecified"}:
            return TOOL_PROFILE_CANDIDATE_TECHNOLOGY
    return stored


def infer_interview_attendee(technology: str = "", name: str = "") -> str:
    if candidate_defaults_to_tool_attendee(name):
        return "Tool"
    return "Bhavana"


def normalise_interview_attendee_name(raw: str | None) -> str:
    key = (raw or "").strip()
    if not key:
        return ""
    canon = _canonical_reference_name(key)
    lowered = canon.lower()
    if lowered == "nikhila":
        return "Nikhila"
    if lowered == "bhavana":
        return "Bhavana"
    if lowered == "tool":
        return "Tool"
    raise ValueError("Interview attendee must be Nikhila, Bhavana, or Tool")


INTERVIEW_FEEDBACK_VALUES = {"positive", "negative"}


def normalise_interview_feedback(raw: str | None) -> str:
    """Map free-text feedback onto the two supported outcomes ("" = not set)."""
    key = (raw or "").strip().lower()
    if key in {"", "pending", "none"}:
        return ""
    if key in {"positive", "good", "pass", "passed"}:
        return "positive"
    if key in {"negative", "bad", "fail", "failed"}:
        return "negative"
    raise ValueError("Interview feedback must be positive or negative")


def normalise_interview_attendance_status(
    raw: str | None,
    *,
    legacy_attended: bool | None = None,
) -> str:
    key = (raw or "").strip().lower()
    if key in {"pending", ""}:
        return ""
    if key == "canceled":
        key = "cancelled"
    if key == "reschedule":
        key = "rescheduled"
    if key in INTERVIEW_ATTENDANCE_STATUSES:
        return key
    if legacy_attended is True:
        return "attended"
    return ""


def row_interview_attendance_status(row: dict) -> str:
    stored = (row.get("interview_attendance_status") or "").strip().lower()
    if stored == "canceled":
        stored = "cancelled"
    if stored == "reschedule":
        stored = "rescheduled"
    if stored in INTERVIEW_ATTENDANCE_STATUSES:
        return stored
    if _coerce_bool(row.get("interview_attended")):
        return "attended"
    return ""


def _interview_attendance_counts(rows: list[dict]) -> dict[str, int]:
    """One counter per stored status, plus Pending for the rows carrying none.

    Counted by iterating INTERVIEW_ATTENDANCE_STATUSES rather than naming four
    of them in an if/elif chain. The chain left `re_service` out, and Pending
    was derived by subtracting the four it did name -- so every Re-Service row
    was reported as Pending, on all five surfaces that call this. A status
    added to the set now gets its own counter instead of quietly landing in
    Pending.
    """
    counted = {status: 0 for status in INTERVIEW_ATTENDANCE_STATUSES}
    for row in rows:
        status = row_interview_attendance_status(row)
        if status in counted:
            counted[status] += 1
    return {
        f"{status}_count": total for status, total in counted.items()
    } | {
        # Pending is the absence of a stored status, never a stored value.
        "pending_count": max(0, len(rows) - sum(counted.values())),
    }


def row_interview_attendee(row: dict) -> str:
    explicit = (row.get("interview_attendee") or "").strip()
    if candidate_defaults_to_tool_attendee(row.get("name") or ""):
        return "Tool"
    if explicit:
        try:
            return normalise_interview_attendee_name(explicit)
        except ValueError:
            # Older imports accidentally copied the referrer into this field.
            # A referrer is never an interview attendee.
            pass
    # Bhavana is the default support attendee for every non-Tool interview.
    return infer_interview_attendee(row.get("technology") or "", row.get("name") or "")


def repair_invalid_interview_attendees() -> int:
    """Replace legacy referrer values in the attendee field with the default."""
    data = _load()
    rows = data.get("candidates") or []
    changed = 0
    for index, raw in enumerate(rows):
        if not _coerce_bool(raw.get("slot_confirmed")):
            continue
        expected = infer_interview_attendee(raw.get("technology") or "", raw.get("name") or "")
        current = _clean_str(raw.get("interview_attendee"))
        try:
            valid = normalise_interview_attendee_name(current) if current else ""
        except ValueError:
            valid = ""
        if valid == expected:
            continue
        row = dict(raw)
        row["interview_attendee"] = expected
        row["updated_at"] = _now_iso()
        rows[index] = row
        changed += 1
    if changed:
        data["candidates"] = rows
        _save(data)
    return changed


def _is_interview_attender_reference(reference: str | None) -> bool:
    key = (reference or "").strip().lower()
    return key in {"bhavana", "nikhila"}


def _technology_key(tech: str) -> str:
    return canonical_technology(tech).lower()


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _empty() -> dict:
    return {"candidates": [], "updated_at": None, "_snapshot_versions": {}}


def _snapshot_versions(rows: list[dict]) -> dict[str, str]:
    return {
        str(row.get("id")): str(row.get("_store_updated_at") or "")
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }


def _merge_candidate_snapshot(
    current_rows: list[dict],
    desired_rows: list[dict],
    versions: dict[str, str],
    *,
    save_at: str,
) -> list[dict]:
    """Merge one stale-capable snapshot without losing concurrent row writes."""
    desired_by_id = {
        str(row.get("id")): row
        for row in desired_rows
        if isinstance(row, dict) and row.get("id")
    }
    current_ids: set[str] = set()
    merged: list[dict] = []
    for current in current_rows:
        cid = str(current.get("id") or "")
        if not cid:
            merged.append(current)
            continue
        current_ids.add(cid)
        expected = versions.get(cid)
        current_version = str(current.get("_store_updated_at") or "")
        desired = desired_by_id.get(cid)
        if desired is None:
            if expected is not None and current_version == expected:
                continue
            merged.append(current)
            continue
        if expected is not None and current_version == expected:
            replacement = dict(desired)
            replacement["_store_updated_at"] = save_at
            merged.append(replacement)
        else:
            merged.append(current)
    for cid, desired in desired_by_id.items():
        if cid in current_ids:
            continue
        if cid in versions:
            continue
        replacement = dict(desired)
        replacement["_store_updated_at"] = save_at
        merged.append(replacement)
    return merged


def _load(*, force: bool = False) -> dict:
    global _load_cache, _load_cache_at
    import time

    now = time.monotonic()
    if (
        not force
        and _load_cache is not None
        and (now - _load_cache_at) < _LOAD_CACHE_TTL
    ):
        return _load_cache

    from core.db.connection import use_postgres

    if use_postgres():
        from core.db.candidates_pg import pg_load as pg_candidates_load
        data = pg_candidates_load()
        if not data.get("candidates"):
            data = _empty()
        else:
            data.setdefault("updated_at", None)
    else:
        with _lock:
            if not os.path.exists(_FILE):
                data = _empty()
            else:
                try:
                    with open(_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        data = _empty()
                    else:
                        data.setdefault("candidates", [])
                        data.setdefault("updated_at", None)
                except (OSError, json.JSONDecodeError):
                    data = _empty()

    data["_snapshot_versions"] = _snapshot_versions(list(data.get("candidates") or []))
    _load_cache = data
    _load_cache_at = now
    return data


def _save(data: dict) -> None:
    global _load_cache, _load_cache_at
    _load_cache = None
    _load_cache_at = 0.0

    from core.db.connection import use_postgres

    if use_postgres():
        from core.db.candidates_pg import pg_save as pg_candidates_save
        data = dict(data)
        data["updated_at"] = _now_iso()
        pg_candidates_save(data)
        return
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    save_at = _now_iso()
    desired_rows = list(data.get("candidates") or [])
    versions = dict(data.get("_snapshot_versions") or {})
    with _lock:
        current = _empty()
        if os.path.exists(_FILE):
            try:
                with open(_FILE, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    current = loaded
            except (OSError, json.JSONDecodeError):
                current = _empty()
        stored = {
            key: value
            for key, value in data.items()
            if key not in {"candidates", "_snapshot_versions"}
        }
        stored["candidates"] = _merge_candidate_snapshot(
            list(current.get("candidates") or []),
            desired_rows,
            versions,
            save_at=save_at,
        )
        stored["updated_at"] = save_at
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stored, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


# The fields that *are* the booking. Nothing that merely annotates a row —
# evidence pointers, provenance markers — may carry its own copy of these,
# because an older copy written back over a confirmed slot silently un-books
# the interview.
BOOKING_SLOT_FIELDS = ("date", "time", "time_end", "slot_confirmed", "slot_confirmed_at")

_ROW_PATCH_ATTEMPTS = 3


def _patch_row_fields(cid: str, fields) -> dict | None:
    """Persist a few keys on one row without rewriting the rest of the store.

    `fields` is a dict, or a callable taking the freshly-read row and returning
    one — use the callable form for read-modify-write changes such as appending
    to a list, so a retry recomputes against the row as it now stands instead of
    re-applying a change built from a row that has since moved on.

    This replaces a whole-list `_load()` -> mutate -> `_save(data)` pattern. That
    pattern wrote every row from one in-memory snapshot back to storage, so a
    snapshot taken before a booking landed carried the pre-booking values of
    `date`/`time`/`time_end`/`slot_confirmed` back over the row that had just
    been booked — and for a row created *after* the snapshot, the whole-store
    save took the insert branch and dropped the update entirely.

    Reading fresh, writing a snapshot that contains only the target row, and
    re-reading to confirm keeps the write to the one row and the few keys
    actually being changed. A version clash means someone else committed in
    between; retrying re-reads their result rather than overwriting it.
    """
    target = _clean_str(cid)
    if not target or not fields:
        return None
    for _attempt in range(_ROW_PATCH_ATTEMPTS):
        data = _load(force=True)
        current = next(
            (
                r for r in (data.get("candidates") or [])
                if isinstance(r, dict) and str(r.get("id") or "") == target
            ),
            None,
        )
        if current is None:
            return None
        resolved = fields(current) if callable(fields) else fields
        banned = sorted(set(resolved) & set(BOOKING_SLOT_FIELDS))
        if banned:
            raise ValueError(
                f"Booking fields {banned} cannot be changed through a targeted row patch."
            )
        updated = dict(current)
        updated.update(resolved)
        updated["updated_at"] = _now_iso()
        # A single-row desired list with a single-row version map. The merge in
        # `_save`/`pg_save` then has nothing else it *could* rewrite, and its
        # deletion pass — which only visits ids in the version map — has nothing
        # it could delete.
        versions = dict(data.get("_snapshot_versions") or {})
        _save({
            "candidates": [updated],
            "_snapshot_versions": {target: versions.get(target, "")},
        })
        stored = next(
            (
                r for r in (_load(force=True).get("candidates") or [])
                if isinstance(r, dict) and str(r.get("id") or "") == target
            ),
            None,
        )
        if stored is None:
            return None
        if all(stored.get(key) == value for key, value in resolved.items()):
            return _with_computed(stored)
    return None


def _coerce_payment(value) -> int:
    """Accept '5000', '₹5,000', '₹5,000.00', 5000, 5000.5 — normalise to int rupees."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().replace("₹", "").replace(",", "").replace(" ", "")
    if not s or s.lower() in {"xx.xx", "nan", "-"}:
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _clean_str(value, *, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s or default


_ALIAS_CACHE: dict[str, str] | None = None


def _reference_alias_map() -> dict[str, str]:
    """alias key -> canonical key, from the referrer registry.

    The registry already records that "LUKKA PAVAN KALYAN" is an alias of
    "Pavan Kalyan" and that the payment account under that holder name belongs
    to referrer-pavan. Nothing consumed it: reference buckets were built from
    the raw string, so a recovery recorded against the account-holder name
    became a second handler with its own opening balance.

    Cached because this runs once per row; call reload_reference_aliases()
    after the registry changes.
    """
    global _ALIAS_CACHE
    if _ALIAS_CACHE is not None:
        return _ALIAS_CACHE
    mapping: dict[str, str] = {}
    try:
        from features import referrer_registry as _rr

        for row in _rr.list_referrers(include_inactive=True):
            canonical = str(row.get("name") or "").strip().lower()
            if not canonical:
                continue
            for alias in row.get("aliases") or []:
                key = str(alias or "").strip().lower()
                if key and key != canonical:
                    mapping[key] = canonical
    except Exception:
        # Registry unavailable — fall back to raw keys, exactly as before.
        mapping = {}
    _ALIAS_CACHE = mapping
    return mapping


def reload_reference_aliases() -> None:
    """Drop the cached alias map after the referrer registry is written."""
    global _ALIAS_CACHE
    _ALIAS_CACHE = None


def _reference_key(ref: str) -> str:
    """Case-insensitive bucket key for handler / reference names.

    Aliases resolve to their canonical handler so one person cannot appear as
    two rows. Names with no alias entry are unchanged.
    """
    key = (ref or "").strip().lower()
    if not key:
        return "unknown"
    return _reference_alias_map().get(key, key)


def _reference_matches_scope(ref: str, scope_key: str | None) -> bool:
    if not scope_key:
        return True
    return _reference_key(ref) == scope_key


def _payout_excluded_handler(ref: str) -> bool:
    """Owner/admin accounts — excluded from handler payout totals and recovery UI."""
    return _reference_key(ref) in HANDLER_PAYOUT_EXCLUDED_REF_KEYS


def _canonical_reference_name(ref: str) -> str:
    """Normalize spelling for display — 'PAVAN KALYAN' → 'Pavan Kalyan'.

    An alias resolves to the registry's name for that referrer, so the merged
    row is labelled with the canonical handler rather than whichever spelling
    happened to be typed first.
    """
    s = " ".join((ref or "").split()).strip()
    if not s:
        return ""
    if s.lower() == "unknown":
        return "Unknown"
    canonical = _reference_alias_map().get(s.lower())
    if canonical:
        return canonical.title()
    return s.title()


def _prefer_reference_display(existing: str, new: str) -> str:
    """When the same handler was typed two ways, pick the nicer label."""
    a = (existing or "").strip()
    b = (new or "").strip()
    if not a:
        return _canonical_reference_name(b)
    if not b:
        return a
    if _reference_key(a) != _reference_key(b):
        return a
    a_caps = a == a.upper() and any(c.isalpha() for c in a)
    b_caps = b == b.upper() and any(c.isalpha() for c in b)
    if a_caps and not b_caps:
        return b
    if b_caps and not a_caps:
        return a
    return _canonical_reference_name(a)


def reference_dropdown_names(rows: list[dict] | None = None) -> list[str]:
    """Sorted referrer names for add/edit candidate Reference field."""
    if rows is None:
        rows = list_candidates()
    by_key: dict[str, str] = {}
    for preset in HANDLER_REFERENCE_PRESETS:
        name = _canonical_reference_name(preset)
        if name:
            by_key[_reference_key(name)] = name
    for row in rows:
        ref_raw = (row.get("reference") or "").strip()
        if not ref_raw or ref_raw.lower() == "unknown":
            continue
        name = _canonical_reference_name(ref_raw)
        key = _reference_key(name)
        by_key[key] = _prefer_reference_display(by_key.get(key, name), ref_raw)
    return sorted(by_key.values(), key=lambda x: x.lower())


def _coerce_bool(value) -> bool:
    """Accept True/False, 1/0, '1', 'true', 'yes', 'on', 'consultancy'."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "on", "y", "t", "consultancy")


# ── Schema normalisation ────────────────────────────────────────────────────

_ALLOWED_FIELDS = {
    "re_service_eligible", "re_service_consumed", "re_service_booking",
    "re_service_grant_row_id",
    "name", "stage", "technology", "task", "phone", "email", "reference",
    "consultancy", "bgv_certificates", "ctc_percentage",
    "payment", "expected_payment", "follow_up",
    "date", "logged_date", "time", "time_end", "expenses", "notes",
    # Auto-stamped when a profile is marked completed; patchable so an operator
    # can correct a closure that was recorded on the wrong day.
    "closure_date",
    "telegram_slot", "telegram_user_id",
    "service_type", "interview_scope",
    "slot_confirmed",
    "slots_group_posted",
    "interview_attendee",
    "interview_round",
    "interview_company", "interview_role", "interview_source_thread_id",
    "interview_source_message_id", "interview_source_timezone",
    # The calendar event a slot came from. An organiser who moves the meeting
    # re-sends the same UID with a higher SEQUENCE, and these are what let a
    # revision find the booking it supersedes instead of creating a second one.
    "interview_calendar_uid", "interview_calendar_sequence",
    "interview_booking_source",
    "booking_idempotency_key",
    "previousBookingId", "reusedPaymentId", "paymentReusedByBookingId",
    "purpose",
}


def minimum_payment_for_slot(row: dict) -> int:
    """Rupee threshold before slot_confirmed is allowed (owner + money rule)."""
    if is_free_service_candidate(row.get("name") or ""):
        return 0
    service_type = _normalise_service_type(row.get("service_type"), row)
    consultancy = bool(row.get("consultancy", False))
    expected = effective_expected_payment(row)
    if service_type == "round_wise":
        return expected
    return min(PROFILE_SERVICE_SLOT_MIN_PAYMENT, expected)


def slot_confirm_block_reason(row: dict) -> str | None:
    """None if slot_confirmed may be set; else human-readable blocker."""
    if not _coerce_bool(row.get("slots_group_posted")):
        return (
            "Confirm the slot screenshot was posted in the Interview slots "
            "WhatsApp group first."
        )
    ref = (row.get("reference") or "").strip()
    if not ref or ref.lower() == "unknown":
        return "Assign an owner (reference) before confirming the interview slot."
    received = int(row.get("payment") or 0)
    need = minimum_payment_for_slot(row)
    if received < need:
        return (
            f"Record at least ₹{need:,} received before confirming the slot "
            f"(currently ₹{received:,})."
        )
    if not (row.get("date") or "").strip():
        return "Set the interview date before confirming the slot."
    return None


def can_confirm_slot(row: dict) -> bool:
    return slot_confirm_block_reason(row) is None


def _normalise_service_type(raw, base: dict | None = None) -> str:
    val = _clean_str(raw if raw is not None else (base or {}).get("service_type", "profile_service")).lower()
    return val if val in VALID_SERVICE_TYPES else "profile_service"


def validate_profile_ctc_percentage(record: dict, *, existing: dict | None = None) -> float | str:
    """Validate and normalize the mandatory profile-service CTC percentage."""
    base = existing or {}
    service_type = _normalise_service_type(record.get("service_type"), base)
    if service_type != "profile_service":
        return ""
    raw = record.get("ctc_percentage", base.get("ctc_percentage", ""))
    stage = _clean_str(record.get("stage", base.get("stage", ""))).lower().replace(" ", "_")
    if stage == "dropped":
        # Dropped candidates are historical closures. Preserve a valid existing
        # percentage when available, but never block closure on this field.
        for optional_raw in (raw, base.get("ctc_percentage", "")):
            try:
                optional_value = float(optional_raw)
            except (TypeError, ValueError):
                continue
            if 0 < optional_value <= 100:
                return int(optional_value) if optional_value.is_integer() else optional_value
        return ""
    if raw is None or str(raw).strip() == "":
        raise ValueError("% on CTC is required for profile-service candidates.")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("% on CTC must be a valid number.") from exc
    if value <= 0 or value > 100:
        raise ValueError("% on CTC must be greater than 0 and not more than 100.")
    return int(value) if value.is_integer() else value


def _normalise_purpose(raw, base: dict | None = None) -> str:
    val = _clean_str(raw if raw is not None else (base or {}).get("purpose", "")).lower().replace(" ", "_")
    if val in VALID_PURPOSES:
        return val
    if "work" in val:
        return "work_support"
    if "experience" in val or "doc" in val:
        return "experience_docs"
    if "interview" in val:
        return "interview_support"
    return "interview_support"


def _normalise_interview_scope(raw, base: dict | None = None) -> str:
    val = _clean_str(
        raw if raw is not None else (base or {}).get("interview_scope", "external"),
    ).lower().replace("-", "_").replace(" ", "_")
    internal_aliases = {
        "non_domestic", "nondomestic", "internal", "international",
        "abroad", "usa", "us", "india_abroad",
    }
    external_aliases = {
        "domestic", "india", "external", "regular", "round",
    }
    if val in internal_aliases:
        return "internal"
    if val in external_aliases:
        return "external"
    return val if val in VALID_INTERVIEW_SCOPES else "external"


def normalise_interview_round(raw) -> str:
    """Canonical interview round label (L1, L2, …) for slot booking."""
    val = _clean_str(raw)
    if not val:
        return ""
    compact = re.sub(r"\s+", "", val).upper()
    if compact in VALID_INTERVIEW_ROUNDS:
        return compact
    m = re.match(r"^L(\d)$", compact, re.IGNORECASE)
    if m:
        label = f"L{m.group(1)}"
        return label if label in VALID_INTERVIEW_ROUNDS else ""
    title = val.title()
    if title in VALID_INTERVIEW_ROUNDS:
        return title
    return ""


def _normalise(record: dict, *, existing: dict | None = None) -> dict:
    """Turn whatever the UI sent into a clean row, preserving existing
    timestamps when patching."""
    base = dict(existing) if existing else {}

    # `consultancy` flips the default baseline: True → ₹15k, False → ₹20k.
    # Stored as a clean bool so the UI doesn't have to guess from strings.
    consultancy = _coerce_bool(record.get("consultancy", base.get("consultancy", False)))
    bgv_certificates = _coerce_bool(record.get("bgv_certificates", base.get("bgv_certificates", False)))
    service_type = _normalise_service_type(record.get("service_type"), base)
    interview_scope = _normalise_interview_scope(record.get("interview_scope"), base)
    if service_type == "round_wise":
        consultancy = False
    ctc_raw = record.get("ctc_percentage", base.get("ctc_percentage", ""))
    try:
        ctc_percentage = float(ctc_raw) if str(ctc_raw).strip() else ""
        if isinstance(ctc_percentage, float) and ctc_percentage.is_integer():
            ctc_percentage = int(ctc_percentage)
    except (TypeError, ValueError):
        ctc_percentage = ""
    if service_type != "profile_service":
        ctc_percentage = ""

    default_for_channel = baseline_for_service(
        service_type,
        consultancy=consultancy,
        interview_scope=interview_scope,
        bgv_certificates=bgv_certificates,
    )
    exp_raw = record.get("expected_payment",
                         base.get("expected_payment", default_for_channel))
    expected = _coerce_payment(exp_raw)
    if expected <= 0:
        expected = default_for_channel
    elif (
        service_type == "profile_service"
        and consultancy
        and expected == DEFAULT_EXPECTED_PAYMENT
        and "consultancy" in record
    ):
        expected = CONSULTANCY_EXPECTED_PAYMENT
    elif (
        service_type == "profile_service"
        and not consultancy
        and expected == CONSULTANCY_EXPECTED_PAYMENT
        and "consultancy" in record
    ):
        expected = DEFAULT_EXPECTED_PAYMENT

    # `proofs` is intentionally NOT in _ALLOWED_FIELDS — it's only mutated
    # through add_proof / delete_proof so screenshots can't be wiped by a
    # plain PATCH on the candidate record.
    out = {
        "id":               base.get("id") or _new_id(),
        "name":             canonical_candidate_name(
            _clean_str(record.get("name", base.get("name")))
        ),
        "stage":            _clean_str(record.get("stage", base.get("stage", "in_progress"))).lower().replace(" ", "_"),
        "technology":       canonical_technology(
            _clean_str(record.get("technology", base.get("technology")))
        ),
        "task":             _clean_str(record.get("task", base.get("task", "not_started"))).lower().replace(" ", "_"),
        "phone":            _clean_str(record.get("phone", base.get("phone"))),
        "email":            _clean_str(record.get("email", base.get("email"))).lower(),
        "reference":        _canonical_reference_name(
            _clean_str(record.get("reference", base.get("reference")))
        ),
        "consultancy":      consultancy,
        "bgv_certificates": bgv_certificates,
        "ctc_percentage":   ctc_percentage,
        "service_type":     service_type,
        "interview_scope":  interview_scope if service_type == "round_wise" else "",
        "payment":          _coerce_payment(record.get("payment", base.get("payment"))),
        "expected_payment": expected,
        "follow_up":        _clean_str(record.get("follow_up", base.get("follow_up"))),
        "purpose":          _normalise_purpose(record.get("purpose"), base),
        "date":             _clean_str(record.get("date", base.get("date"))),
        "logged_date":      _clean_str(record.get("logged_date", base.get("logged_date"))),
        "time":             _clean_str(record.get("time", base.get("time"))),
        "time_end":         _clean_str(record.get("time_end", base.get("time_end"))),
        "expenses":         _clean_str(record.get("expenses", base.get("expenses"))),
        "notes":            sanitize_candidate_notes(_clean_str(record.get("notes", base.get("notes")))),
        "interview_attendee": _canonical_reference_name(
            _clean_str(record.get("interview_attendee", base.get("interview_attendee")))
        ),
        "interview_round":  normalise_interview_round(
            record.get("interview_round", base.get("interview_round", ""))
        ),
        "interview_company": _clean_str(record.get("interview_company", base.get("interview_company"))),
        "interview_role": _clean_str(record.get("interview_role", base.get("interview_role"))),
        "interview_source_thread_id": _clean_str(record.get("interview_source_thread_id", base.get("interview_source_thread_id"))),
        "interview_source_message_id": _clean_str(record.get("interview_source_message_id", base.get("interview_source_message_id"))),
        "interview_source_timezone": _clean_str(record.get("interview_source_timezone", base.get("interview_source_timezone"))),
        "interview_calendar_uid": _clean_str(record.get("interview_calendar_uid", base.get("interview_calendar_uid"))),
        "interview_calendar_sequence": _clean_str(record.get("interview_calendar_sequence", base.get("interview_calendar_sequence"))),
        "interview_booking_source": _clean_str(record.get("interview_booking_source", base.get("interview_booking_source"))).lower(),
        "booking_idempotency_key": _clean_str(
            record.get("booking_idempotency_key", base.get("booking_idempotency_key"))
        ),
        "previousBookingId": _clean_str(
            record.get("previousBookingId", base.get("previousBookingId"))
        ),
        "reusedPaymentId": _clean_str(
            record.get("reusedPaymentId", base.get("reusedPaymentId"))
        ),
        "paymentReusedByBookingId": _clean_str(
            record.get("paymentReusedByBookingId", base.get("paymentReusedByBookingId"))
        ),
        # Once a row's received total has been derived from adjudicated proofs,
        # it stays proof-controlled: later proof changes, including reductions
        # from a rejected proof, apply without a reconciliation prompt.
        "payment_proof_controlled": _coerce_bool(
            record.get(
                "payment_proof_controlled", base.get("payment_proof_controlled", False)
            )
        ),
        "re_service_eligible": _coerce_bool(
            record.get("re_service_eligible", base.get("re_service_eligible", False))
        ),
        "re_service_consumed": _coerce_bool(
            record.get("re_service_consumed", base.get("re_service_consumed", False))
        ),
        "re_service_booking": _coerce_bool(
            record.get("re_service_booking", base.get("re_service_booking", False))
        ),
        "re_service_grant_row_id": _clean_str(
            record.get("re_service_grant_row_id", base.get("re_service_grant_row_id"))
        ),
        "re_service_granted_at": _clean_str(
            record.get("re_service_granted_at", base.get("re_service_granted_at"))
        ),
        "re_service_granted_by": _clean_str(
            record.get("re_service_granted_by", base.get("re_service_granted_by"))
        ),
        "re_service_consumed_at": _clean_str(
            record.get("re_service_consumed_at", base.get("re_service_consumed_at"))
        ),
        "re_service_consumed_booking_id": _clean_str(
            record.get(
                "re_service_consumed_booking_id",
                base.get("re_service_consumed_booking_id"),
            )
        ),
        "telegram_slot":    _clean_str(record.get("telegram_slot", base.get("telegram_slot"))),
        "telegram_user_id": int(record.get("telegram_user_id") or base.get("telegram_user_id") or 0) or None,
        "payment_proofs":   list(record.get("payment_proofs", base.get("payment_proofs")) or []),
        "slot_screenshot_proofs": list(record.get("slot_screenshot_proofs", base.get("slot_screenshot_proofs")) or []),
        # Pointer to the screenshot that evidences this booking. Deliberately
        # absent from _ALLOWED_FIELDS — like `proofs`, it is only ever moved by
        # the attachment path — but it must survive the rebuild here, or the
        # next edit of any kind silently drops the evidence link.
        "slot_screenshot_proof_id": _clean_str(
            record.get("slot_screenshot_proof_id", base.get("slot_screenshot_proof_id"))
        ),
        "profile_photo":    record.get("profile_photo", base.get("profile_photo")) if isinstance(record.get("profile_photo", base.get("profile_photo")), dict) else None,
        "attachment_review_queue": list(record.get("attachment_review_queue", base.get("attachment_review_queue")) or []),
        "attachment_schema_version": int(base.get("attachment_schema_version") or 2),
        "proofs":           list(base.get("proofs") or []),
        "resumes":          list(base.get("resumes") or []),
        "created_at":       base.get("created_at") or _now_iso(),
        "updated_at":       _now_iso(),
    }
    out["slots_group_posted"] = _coerce_bool(
        record.get("slots_group_posted", base.get("slots_group_posted", False))
    )
    want_confirm = _coerce_bool(record.get("slot_confirmed", base.get("slot_confirmed", False)))
    prev_confirm = _coerce_bool(base.get("slot_confirmed", False))
    out["slot_confirmed"] = want_confirm
    if want_confirm and not prev_confirm:
        out["slot_confirmed_at"] = _now_iso()
    elif want_confirm:
        out["slot_confirmed_at"] = base.get("slot_confirmed_at") or _now_iso()
    else:
        out["slot_confirmed"] = False
        out["slot_confirmed_at"] = ""
    out["interview_attendance_status"] = normalise_interview_attendance_status(
        base.get("interview_attendance_status"),
        legacy_attended=_coerce_bool(base.get("interview_attended", False)),
    )
    out["interview_attendance_remark"] = _clean_str(base.get("interview_attendance_remark"))
    out["interview_attended"] = out["interview_attendance_status"] == "attended"
    out["interview_attended_at"] = base.get("interview_attended_at") or ""
    out["interview_attended_by"] = _clean_str(base.get("interview_attended_by"))
    if out["stage"] not in VALID_STAGES:
        out["stage"] = "in_progress"
    if out["task"] not in VALID_TASKS:
        out["task"] = "not_started"
    # ── Closure date ──
    # The profile-closure complimentary is earned on the day a profile actually
    # closes, which can fall in a different month from the day the lead was
    # registered. Stamp the date when the stage becomes "completed" and keep it
    # stable across later edits; clear it if the profile reopens, so that a
    # re-closure records the date it really happened rather than the first one.
    was_completed = str((existing or {}).get("stage") or "").strip().lower() == "completed"
    supplied = _clean_str(record.get("closure_date"))[:10] if "closure_date" in record else ""
    recorded = _clean_str(base.get("closure_date"))[:10]
    if out["stage"] == "completed":
        if len(supplied) == 10:
            # An operator corrected the date; take it and re-stamp the audit time.
            out["closure_date"] = supplied
            out["closure_recorded_at"] = _now_iso()
        elif was_completed and len(recorded) == 10:
            out["closure_date"] = recorded
            out["closure_recorded_at"] = _clean_str(base.get("closure_recorded_at"))
        else:
            out["closure_date"] = _today_iso_date()
            out["closure_recorded_at"] = _now_iso()
    else:
        out["closure_date"] = ""
        out["closure_recorded_at"] = ""
    logged = (out.get("logged_date") or "").strip()[:10]
    day = (out.get("date") or "").strip()[:10]
    if len(logged) != 10 and len(day) == 10 and (not existing or "date" in record):
        out["logged_date"] = day
    return out


def _row_closure_month(row: dict) -> str:
    """Month a profile closed, for attributing closure complimentary amounts.

    Falls back to the registration month for rows closed before closure dates
    were recorded, so no historical figure moves when this is introduced.
    """
    closed = _clean_str(row.get("closure_date"))[:10]
    if len(closed) >= 7 and closed[4] == "-":
        return closed[:7]
    return _row_display_month(row)


def _row_lead_date(row: dict) -> str:
    """When the lead was logged — preserved when interview slots are assigned later."""
    logged = _clean_str(row.get("logged_date"))[:10]
    if len(logged) == 10:
        return logged
    return _clean_str(row.get("date"))[:10]


def interview_booking_source(row: dict) -> str:
    """Return the durable origin label used by Daily Ops.

    New bookings persist the source explicitly. Older AI-mail bookings are
    identified from the audit note written by the booking pipeline so existing
    roster rows receive the correct label without a data migration.
    """
    if not _coerce_bool(row.get("slot_confirmed")):
        return ""
    stored = _clean_str(row.get("interview_booking_source")).lower()
    if stored in {"ai_auto_booked", "candidate_booked"}:
        return stored
    note = _clean_str(row.get("notes")).lower()
    automatic_markers = (
        "automatically booked from validated interview email",
        "rescheduled from validated interview email",
    )
    if any(marker in note for marker in automatic_markers):
        return "ai_auto_booked"
    return "candidate_booked"


def _with_computed(row: dict) -> dict:
    """Append derived fields (`balance_due`, `payment_status`) without
    persisting them. Keeps the storage format simple while giving the
    UI a single, server-computed source of truth."""
    consultancy = bool(row.get("consultancy", False))
    service_type = _normalise_service_type(row.get("service_type"), row)
    interview_scope = _normalise_interview_scope(row.get("interview_scope"), row)
    fallback = baseline_for_service(
        service_type,
        consultancy=consultancy,
        interview_scope=interview_scope,
        bgv_certificates=_coerce_bool(row.get("bgv_certificates")),
    )
    expected = effective_expected_payment(row)
    # Verified payment proofs are what the candidate actually paid. The typed
    # figure is only a fallback for rows that predate proof capture, because
    # deriving those from an empty proof list would erase real money.
    attachments = partition_candidate_attachments(row)
    receipts = payment_receipts.receipt_summary(
        expected=expected,
        recorded=int(row.get("payment") or 0),
        proofs=attachments["payment_proofs"],
        proof_controlled=_coerce_bool(row.get("payment_proof_controlled")),
    )
    received = receipts["verified_received"]
    balance = receipts["outstanding"]
    if received <= 0:
        status = "unpaid"
    elif received >= expected:
        status = "paid"
    else:
        status = "partial"
    enriched = dict(row)
    enriched["interview_booking_source"] = interview_booking_source(row)
    enriched["consultancy"] = consultancy
    enriched["bgv_certificates"] = _coerce_bool(row.get("bgv_certificates"))
    enriched["service_type"] = service_type
    enriched["interview_scope"] = interview_scope if service_type == "round_wise" else ""
    enriched["expected_payment"] = expected
    enriched["prescribed_baseline"] = fallback
    enriched["balance_due"] = balance
    enriched["payment_status"] = status
    enriched["needs_followup"] = balance > 0
    enriched["closure_date"] = _clean_str(row.get("closure_date"))[:10]
    enriched["closure_month"] = _row_closure_month(row)
    base_commission = referrer_commission_amount(row)
    referrer_bonus = referrer_complimentary_amount(row)
    admin_bonus = admin_complimentary_amount(row)
    enriched["base_handler_commission"] = base_commission
    # The payment-referral share, kept apart from any closure complimentary so
    # the editor can label one without absorbing the other.
    enriched["referral_commission"] = base_commission
    enriched["referral_percentage"] = HANDLER_COMMISSION_PCT
    enriched["referral_basis"] = referrer_commission_basis(row)
    allocation = payment_allocation_for(row)
    enriched["payment_allocation"] = allocation
    enriched["service_expected"] = allocation["service_expected"]
    enriched["service_received"] = allocation["service_received"]
    enriched["service_outstanding"] = allocation["service_outstanding"]
    enriched["bgv_expected"] = allocation["bgv_expected"]
    enriched["bgv_received"] = allocation["bgv_received"]
    enriched["bgv_outstanding"] = allocation["bgv_outstanding"]
    enriched["unallocated_excess"] = allocation["unallocated_excess"]
    enriched["needs_excess_review"] = allocation["needs_excess_review"]
    enriched["referrer_complimentary_amount"] = referrer_bonus
    enriched["admin_complimentary_amount"] = admin_bonus
    # Candidate rows are shown under their own referrer. The admin bonus is
    # itemised separately in Thrilok's earnings breakdown to avoid double count.
    enriched["handler_commission"] = base_commission + referrer_bonus
    enriched["total_handler_earnings"] = sum(handler_earning_allocations(row).values())
    commissionable_expected = max(0, expected - (BGV_CERTIFICATES_PAYMENT if enriched["bgv_certificates"] else 0))
    enriched["handler_commission_max"] = (
        (commissionable_expected * HANDLER_COMMISSION_PCT) // 100
    ) + referrer_bonus
    # BGV money is collected on a third party's behalf, so it is never company
    # revenue. Only the service part of what was received is.
    enriched["company_revenue"] = (
        payment_allocation_for(row)["service_received"]
        - enriched["total_handler_earnings"]
    )
    # The Received field is system-calculated whenever proof evidence exists, so
    # the UI can present it read-only and show the arithmetic behind it.
    enriched["payment"] = received
    enriched["recorded_payment"] = receipts["recorded_amount"]
    enriched["payment_is_proof_derived"] = receipts["proof_derived"]
    enriched["expected_minimum"] = receipts["expected_minimum"]
    enriched["verified_received"] = receipts["verified_received"]
    enriched["verified_proof_total"] = receipts["verified_proof_total"]
    enriched["verified_proof_count"] = receipts["verified_proof_count"]
    enriched["above_minimum"] = receipts["above_minimum"]
    enriched["payment_proof_status_counts"] = receipts["status_counts"]
    enriched["payment_unevidenced"] = receipts["unevidenced"]
    enriched["payment_needs_reconciliation"] = receipts["needs_reconciliation"]
    enriched["payment_reconciliation_gap"] = receipts["reconciliation_gap"]
    payment_proofs = attachments["payment_proofs"]
    enriched.pop("proofs", None)
    enriched["payment_proofs"] = payment_proofs
    enriched["proof_count"] = len(payment_proofs)
    enriched["slot_screenshot_proofs"] = attachments["slot_screenshot_proofs"]
    enriched["profile_photo"] = attachments["profile_photo"]
    enriched["attachment_review_queue"] = attachments["attachment_review_queue"]
    enriched["attachment_schema_version"] = 2
    resumes = enriched.get("resumes") or []
    enriched["resumes"] = resumes
    enriched["resume_count"] = len(resumes)
    if resumes:
        enriched["latest_resume"] = max(
            resumes,
            key=lambda r: r.get("uploaded_at") or "",
        )
    else:
        enriched["latest_resume"] = None
    required_details = {
        "name": _clean_str(enriched.get("name")),
        "technology": _clean_str(enriched.get("technology")),
        "date": _clean_str(enriched.get("date")),
        "phone": _clean_str(enriched.get("phone")),
        "reference": _clean_str(enriched.get("reference")),
        "resume": bool(resumes),
        # Once money is recorded, at least one payment proof is required
        # before the row can be considered fully complete.
        "payment_proof": bool(payment_proofs) if received > 0 else True,
    }
    enriched["completion_missing"] = [
        field for field, value in required_details.items() if not value
    ]
    # This is a data-entry completion signal only. An unpaid balance or a
    # future interview slot must not hide it, because those are workflow state.
    enriched["details_complete"] = not enriched["completion_missing"]
    slot_ok = can_confirm_slot(enriched)
    enriched["can_confirm_slot"] = slot_ok
    enriched["slot_confirm_block_reason"] = slot_confirm_block_reason(enriched)
    enriched["slot_confirm_min_payment"] = minimum_payment_for_slot(enriched)
    enriched["interview_attendee_resolved"] = row_interview_attendee(enriched)
    enriched["interview_attendance_status_resolved"] = row_interview_attendance_status(enriched)
    enriched["technology_resolved"] = row_candidate_technology(enriched)
    return enriched


def backfill_tool_default_interview_attendees() -> int:
    """Persist Tool on Keerthana / Satyanarayana slots that still have empty or Bhavana."""
    data = _load(force=True)
    rows = data.get("candidates") or []
    changed = 0
    for i, r in enumerate(rows):
        if not candidate_defaults_to_tool_attendee(r.get("name") or ""):
            continue
        if not _clean_str(r.get("date")):
            continue
        explicit = (r.get("interview_attendee") or "").strip().lower()
        if explicit == "tool":
            continue
        if explicit and explicit not in {"", "bhavana"}:
            continue
        r = dict(r)
        r["interview_attendee"] = "Tool"
        r["updated_at"] = _now_iso()
        rows[i] = r
        changed += 1
    if changed:
        data["candidates"] = rows
        data["updated_at"] = _now_iso()
        _save(data)
        global _load_cache, _load_cache_at
        _load_cache = None
        _load_cache_at = 0.0
    return changed


def backfill_logged_dates() -> int:
    """Set logged_date from earliest slot/lead date (or created_at) per profile client name."""
    data = _load(force=True)
    rows = data.get("candidates") or []
    earliest_by_name: dict[str, str] = {}
    for r in rows:
        if _normalise_service_type(r.get("service_type"), r) == "round_wise":
            continue
        key = _normalise_candidate_name_key(r.get("name") or "")
        if not key:
            continue
        # Use logged_date if available, then date, then created_at
        day = _clean_str(r.get("logged_date"))[:10]
        if len(day) != 10:
            day = _clean_str(r.get("date"))[:10]
        if len(day) != 10:
            day = _clean_str(r.get("created_at"))[:10]
        if len(day) != 10:
            continue
        prev = earliest_by_name.get(key)
        if not prev or day < prev:
            earliest_by_name[key] = day
    changed = 0
    for i, r in enumerate(rows):
        if _normalise_service_type(r.get("service_type"), r) == "round_wise":
            continue
        key = _normalise_candidate_name_key(r.get("name") or "")
        lead = earliest_by_name.get(key, "")
        if len(lead) != 10:
            continue
        current = _clean_str(r.get("logged_date"))[:10]
        if current == lead:
            continue
        r = dict(r)
        r["logged_date"] = lead
        r["updated_at"] = _now_iso()
        rows[i] = r
        changed += 1
    if changed:
        data["candidates"] = rows
        data["updated_at"] = _now_iso()
        _save(data)
        global _load_cache, _load_cache_at
        _load_cache = None
        _load_cache_at = 0.0
    return changed


def backfill_tool_default_candidate_technology() -> int:
    """Persist Data Analyst on Keerthana / Satyanarayana rows still marked Unspecified."""
    data = _load(force=True)
    rows = data.get("candidates") or []
    changed = 0
    for i, r in enumerate(rows):
        if not candidate_defaults_to_tool_attendee(r.get("name") or ""):
            continue
        stored = canonical_technology(r.get("technology") or "")
        if stored not in {"", "Unspecified"}:
            continue
        r = dict(r)
        r["technology"] = TOOL_PROFILE_CANDIDATE_TECHNOLOGY
        r["updated_at"] = _now_iso()
        rows[i] = r
        changed += 1
    if changed:
        data["candidates"] = rows
        data["updated_at"] = _now_iso()
        _save(data)
        global _load_cache, _load_cache_at
        _load_cache = None
        _load_cache_at = 0.0
    return changed


def backfill_canonical_candidate_names() -> int:
    """Merge name variants (PERLA ABHILASH vs Abilash Perla) to one canonical label."""
    data = _load(force=True)
    rows = data.get("candidates") or []
    changed = 0
    for i, r in enumerate(rows):
        old = (r.get("name") or "").strip()
        new = canonical_candidate_name(old)
        if not new or new == old:
            continue
        rows[i] = _normalise({"name": new}, existing=r)
        changed += 1
    if changed:
        data["candidates"] = rows
        data["updated_at"] = _now_iso()
        _save(data)
        global _load_cache, _load_cache_at
        _load_cache = None
        _load_cache_at = 0.0
    return changed


# ── Public API ──────────────────────────────────────────────────────────────

def _apply_list_filters(
    rows: list[dict],
    *,
    stage: str | None = None,
    task: str | None = None,
    search: str | None = None,
    month: str | None = None,
    pending_only: bool = False,
    reference: str | None = None,
    service_type: str | None = None,
) -> list[dict]:
    if stage and stage != "all":
        rows = [r for r in rows if r.get("stage") == stage]
    if task and task != "all":
        rows = [r for r in rows if r.get("task") == task]
    if month and month != "all":
        if month == "undated":
            rows = [r for r in rows if not _row_month(r) and not _row_display_month(r)]
        else:
            rows = [r for r in rows if _row_in_month(r, month)]
    if pending_only:
        rows = [r for r in rows if r.get("balance_due", 0) > 0]
    if reference and reference != "all":
        needle = reference.strip().lower()
        rows = [r for r in rows if (r.get("reference") or "").strip().lower() == needle]
    if service_type and service_type != "all":
        rows = [r for r in rows if _normalise_service_type(r.get("service_type"), r) == service_type]
    if search:
        q = search.strip().lower()
        if q:
            def _hit(r: dict) -> bool:
                if q == "consultancy" and r.get("consultancy"):
                    return True
                for k in ("name", "technology", "reference", "phone", "notes", "follow_up"):
                    if q in (r.get(k) or "").lower():
                        return True
                return False
            rows = [r for r in rows if _hit(r)]
    rows.sort(key=lambda r: (r.get("date") or "", r.get("updated_at") or ""), reverse=True)
    return rows


def _slim_list_row(row: dict) -> dict:
    """Drop payment proof blobs from list payloads — resume metadata stays for the viewer."""
    slim = dict(row)
    slim.pop("proofs", None)
    return slim


def _collapse_profile_candidates(rows: list[dict], *, month: str | None = None) -> list[dict]:
    """Show one Candidates-page record per profile candidate.

    Multiple interview slots are stored as separate rows for scheduling, but
    they are not separate profile candidates.  Keep round-wise support rows
    independent and merge only profile-service rows by normalised name.
    """
    grouped: dict[str, list[dict]] = {}
    result: list[dict] = []
    for row in rows:
        if _normalise_service_type(row.get("service_type"), row) == "round_wise":
            result.append(row)
            continue
        key = " ".join((row.get("name") or "").strip().lower().split())
        if not key:
            result.append(row)
            continue
        grouped.setdefault(key, []).append(row)
    for group in grouped.values():
        # When a month filter is active, prefer the row whose date matches that month.
        # This prevents picking a row with an empty date or wrong month as the winner.
        if month and month != "all":
            month_matching = [r for r in group if _row_display_month(r) == month]
            if month_matching:
                newest = max(month_matching, key=lambda r: (r.get("date") or "", r.get("updated_at") or ""))
            else:
                newest = max(group, key=lambda r: (r.get("updated_at") or "", r.get("date") or ""))
        else:
            newest = max(group, key=lambda r: (r.get("updated_at") or "", r.get("date") or ""))
        merged = dict(newest)
        merged["slot_count"] = len(group)
        # Use the max payment across all slot clones for this profile.
        # Payment is recorded on one slot but the collapsed row should reflect it.
        max_payment = max(int(r.get("payment") or 0) for r in group)
        if max_payment > merged.get("payment", 0):
            merged["payment"] = max_payment
        # A profile may have old interview-slot duplicates.  Keep its explicit
        # Ravinder referral instead of letting a newer duplicate (for example
        # one imported with Thrilok) replace it in the consolidated row.
        ravinder_row = next(
            (r for r in group if _reference_key(r.get("reference") or "") == "ravinder"),
            None,
        )
        if ravinder_row:
            merged["reference"] = "Ravinder"
        elif key in {"keerthana", "satyanarayana", "adivi satyanarayana"}:
            merged["reference"] = "Ravinder"
        all_resumes = {item.get("id"): item for r in group for item in (r.get("resumes") or []) if item.get("id")}
        if all_resumes:
            merged["resumes"] = list(all_resumes.values())
            merged["resume_count"] = len(all_resumes)
            merged["latest_resume"] = max(all_resumes.values(), key=lambda item: item.get("uploaded_at") or "")
        # Merge each typed collection independently across legacy profile clones.
        all_proofs = {}
        slot_proofs = {}
        for r in group:
            attachments = partition_candidate_attachments(r)
            for item in attachments["payment_proofs"]:
                pid = item.get("id")
                if not pid or pid in all_proofs:
                    continue
                item = dict(item)
                item.setdefault("candidate_id", r.get("id"))
                all_proofs[pid] = item
            for item in attachments["slot_screenshot_proofs"]:
                pid = item.get("id")
                if not pid or pid in slot_proofs:
                    continue
                item = dict(item)
                item.setdefault("candidate_id", r.get("id"))
                slot_proofs[pid] = item
        if all_proofs:
            merged["payment_proofs"] = list(all_proofs.values())
            merged["proof_count"] = len(all_proofs)
        if slot_proofs:
            merged["slot_screenshot_proofs"] = list(slot_proofs.values())
        result.append(merged)
    result.sort(key=lambda r: (r.get("date") or "", r.get("updated_at") or ""), reverse=True)
    return result


def _in_progress_rows(rows: list[dict], month: str | None) -> list[dict]:
    out = [r for r in rows if r.get("stage") == "in_progress"]
    if month and month != "all":
        if month == "undated":
            out = [r for r in out if not _row_month(r)]
        else:
            out = [r for r in out if _row_in_month(r, month)]
    return out


def _attach_pending_work_stats(payload: dict, pw: dict) -> dict:
    payload["pending_works"] = pw["works"]
    payload["pending_works_count"] = pw["count"]
    payload["pending_works_candidates"] = pw["candidate_count"]
    payload["pending_works_checked"] = pw["candidates_checked"]
    payload["pending_works_by_kind"] = pw["by_kind"]
    return payload


def list_candidates(*, stage: str | None = None, task: str | None = None,
                    search: str | None = None, month: str | None = None,
                    pending_only: bool = False,
                    reference: str | None = None,
                    service_type: str | None = None) -> list[dict]:
    """Return candidates sorted by most-recent first.
    Optional filters: by stage, by task, by free-text search across
    name / technology / reference / phone / notes / follow_up, by month
    ('YYYY-MM'), `pending_only=True` to keep only rows where the
    received payment is less than the expected baseline, and `reference`
    for an exact case-insensitive handler match (so the dashboard can
    show only one handler's leads)."""
    reconcile_resume_metadata()
    data = _load()
    rows = [_with_computed(r) for r in (data.get("candidates") or [])]
    # Apply month filter BEFORE collapse so we don't accidentally pick
    # a June slot when filtering for July (collapse picks newest by updated_at).
    if month and month != "all":
        if month == "undated":
            rows = [r for r in rows if not _row_month(r) and not _row_display_month(r)]
        else:
            rows = [r for r in rows if _row_in_month(r, month)]
    # Consolidate after month filtering. Reference filter still needs to happen
    # after collapse to respect the Ravinder fallback logic.
    rows = _collapse_profile_candidates(rows, month=month)
    return _apply_list_filters(
        rows,
        stage=stage,
        task=task,
        search=search,
        month=None,  # Already applied above
        pending_only=pending_only,
        reference=reference,
        service_type=service_type,
    )


def _is_roster_placeholder(row: dict) -> bool:
    """Skip empty import stubs from the active tech roster (e.g. Unspecified / not started)."""
    tech = (row.get("technology") or "").strip().lower()
    task = (row.get("task") or "").strip().lower()
    phone = (row.get("phone") or "").strip()
    if tech not in {"", "unspecified"}:
        return False
    if task != "not_started":
        return False
    return not phone


def _hidden_from_candidates_page(name: str) -> bool:
    """Match dashboard Candidates page — hide Tool-only roster names."""
    return is_free_service_candidate(name)


PENDING_WORK_LABELS = {
    "missing_reference": "Assign referrer",
    "missing_resume": "Upload resume",
    "payment_due": "Payment pending",
    "missing_follow_up": "Add follow-up remark",
    "missing_phone": "Add phone number",
}

PENDING_WORK_PRIORITY = {
    "missing_reference": 10,
    "missing_resume": 20,
    "payment_due": 30,
    "missing_follow_up": 35,
    "missing_phone": 50,
}


def _pending_work_item(*, kind: str, row: dict, detail: str = "") -> dict:
    return {
        "id": f"{kind}:{row.get('id')}",
        "kind": kind,
        "label": PENDING_WORK_LABELS[kind],
        "detail": detail,
        "priority": PENDING_WORK_PRIORITY[kind],
        "candidate_id": row.get("id"),
        "candidate_name": row.get("name") or "",
        "reference": row.get("reference") or "",
        "technology": row.get("technology") or "",
        "service_type": row.get("service_type") or "profile_service",
    }


def _merge_profile_rows_for_pending(rows: list[dict]) -> dict:
    """Collapse profile slot clones — same rules as the Candidates table merge."""
    rep = max(
        rows,
        key=lambda r: (
            int(r.get("payment") or 0),
            len(r.get("resumes") or []),
            len(partition_candidate_attachments(r)["payment_proofs"]),
            (r.get("date") or ""),
        ),
    )
    payment = max(int(r.get("payment") or 0) for r in rows)
    # Match dashboard merge: use representative row's channel-aware expected, not max across clones.
    expected = effective_expected_payment(rep)
    resume_count = max(len(r.get("resumes") or []) for r in rows)
    # Merge resumes from all clones into the representative row
    all_resumes_merged = {}
    for r in rows:
        for res in (r.get("resumes") or []):
            rid = res.get("id")
            if rid and rid not in all_resumes_merged:
                all_resumes_merged[rid] = res
    merged_resumes = list(all_resumes_merged.values())
    phone = next((r.get("phone") for r in rows if _clean_str(r.get("phone"))), "")
    reference = next(
        (
            r.get("reference")
            for r in rows
            if _clean_str(r.get("reference"))
            and (r.get("reference") or "").strip().lower() != "unknown"
        ),
        rep.get("reference"),
    )
    follow_up = next((r.get("follow_up") for r in rows if _clean_str(r.get("follow_up"))), "")
    merged = {
        **rep,
        "payment": payment,
        "expected_payment": expected,
        "balance_due": max(0, expected - payment),
        "resume_count": len(merged_resumes),
        "resumes": merged_resumes,
        "phone": phone or rep.get("phone"),
        "reference": reference or rep.get("reference"),
        "follow_up": follow_up or rep.get("follow_up"),
    }
    return merged


_STAGE_RANK = {"completed": 4, "in_progress": 3, "fail": 2, "dropped": 1}


def _merge_profile_rows_for_stats(rows: list[dict]) -> dict:
    """Collapse slot clones for KPI aggregates — max payment once per profile."""
    merged = _merge_profile_rows_for_pending(rows)
    merged["stage"] = max(
        (r.get("stage") or "in_progress" for r in rows),
        key=lambda s: _STAGE_RANK.get(s, 0),
    )
    merged["consultancy"] = any(_coerce_bool(r.get("consultancy")) for r in rows)
    return merged


def _stats_rows_deduped(rows: list[dict]) -> list[dict]:
    """One logical client per profile name; round-wise stays one row per slot."""
    profile_by_name: dict[str, list[dict]] = {}
    round_rows: list[dict] = []
    for row in rows:
        if _hidden_from_candidates_page(row.get("name") or ""):
            continue
        if _is_roster_placeholder(row):
            continue
        if _normalise_service_type(row.get("service_type"), row) == "round_wise":
            round_rows.append(row)
            continue
        key = _normalise_candidate_name_key(row.get("name") or "")
        if not key:
            continue
        profile_by_name.setdefault(key, []).append(row)
    merged_profiles = [
        _merge_profile_rows_for_stats(group)
        for group in profile_by_name.values()
    ]
    return merged_profiles + round_rows


def _pending_collections_from_rows(
    rows: list[dict],
) -> tuple[int, int, int, dict[str, dict[str, int]]]:
    """Pending balance once per profile client — not per slot clone."""
    profile_by_name: dict[str, list[dict]] = {}
    round_rows: list[dict] = []
    for row in rows:
        if _normalise_service_type(row.get("service_type"), row) == "round_wise":
            round_rows.append(row)
            continue
        key = _normalise_candidate_name_key(row.get("name") or "")
        if not key:
            continue
        profile_by_name.setdefault(key, []).append(row)

    merged = [
        _merge_profile_rows_for_pending(group)
        for group in profile_by_name.values()
    ] + round_rows

    pending_total = 0
    pending_count = 0
    pending_no_remark = 0
    by_ref: dict[str, dict[str, int]] = {}

    for row in merged:
        balance = int(row.get("balance_due") or 0)
        if balance <= 0:
            expected = effective_expected_payment(row)
            paid = int(row.get("payment") or 0)
            balance = max(0, expected - paid)
        if balance <= 0:
            continue
        pending_total += balance
        pending_count += 1
        if not (row.get("follow_up") or "").strip():
            pending_no_remark += 1
        ref_key = _reference_key(row.get("reference") or "Unknown")
        bucket = by_ref.setdefault(ref_key, {"pending_total": 0, "pending_count": 0})
        bucket["pending_total"] += balance
        bucket["pending_count"] += 1

    return pending_total, pending_count, pending_no_remark, by_ref


def _collect_pending_works_for_row(row: dict) -> list[dict]:
    works: list[dict] = []
    ref = (row.get("reference") or "").strip()
    if not ref or ref.lower() == "unknown":
        works.append(_pending_work_item(kind="missing_reference", row=row))
    service_type = _normalise_service_type(row.get("service_type"), row)
    if service_type != "round_wise" and int(row.get("resume_count") or len(row.get("resumes") or [])) == 0:
        works.append(_pending_work_item(kind="missing_resume", row=row))
    # Payment balance is enforced at slot booking — do not surface as pending work.
    if not (row.get("phone") or "").strip():
        works.append(_pending_work_item(kind="missing_phone", row=row))
    return works


def _pending_works_core(rows: list[dict]) -> dict:
    """Build pending-work items from pre-filtered in-progress rows."""
    rows = [
        r for r in rows
        if not _hidden_from_candidates_page(r.get("name") or "")
        and not _is_roster_placeholder(r)
    ]
    profile_groups: dict[str, list[dict]] = {}
    round_rows: list[dict] = []
    for row in rows:
        if _normalise_service_type(row.get("service_type"), row) == "round_wise":
            round_rows.append(row)
            continue
        key = _normalise_candidate_name_key(row.get("name") or "")
        if not key:
            continue
        profile_groups.setdefault(key, []).append(row)

    merged_profiles = [
        _merge_profile_rows_for_pending(group)
        for group in profile_groups.values()
    ]
    works: list[dict] = []
    for row in merged_profiles + round_rows:
        works.extend(_collect_pending_works_for_row(row))
    works.sort(
        key=lambda item: (
            item["priority"],
            (item.get("candidate_name") or "").lower(),
        ),
    )
    by_kind: dict[str, int] = {}
    candidate_keys: set[str] = set()
    for item in works:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        candidate_keys.add(_normalise_candidate_name_key(item.get("candidate_name") or ""))
    return {
        "works": works,
        "count": len(works),
        "candidate_count": len(candidate_keys),
        "candidates_checked": len(merged_profiles) + len(round_rows),
        "by_kind": by_kind,
    }


def pending_works(*, month: str | None = None, reference: str | None = None) -> dict:
    """Auto-detected operator to-dos for active in-progress candidates.

    Omit `month` or pass ``all`` to scan the whole active pipeline (default for
    dashboard alerts). Pass ``YYYY-MM`` only when a month-scoped view is needed.
    """
    month_filter = month if month and month != "all" else None
    rows = list_candidates(stage="in_progress", month=month_filter, reference=reference)
    return _pending_works_core(rows)


def active_roster(
    *,
    month: str | None = None,
    reference: str | None = None,
) -> dict:
    """Active (in_progress) candidates grouped by technology for roster views."""
    rows = [
        r for r in list_candidates(stage="in_progress", month=month, reference=reference)
        if not _is_roster_placeholder(r)
    ]
    by_technology: dict[str, list[dict]] = {}
    for row in rows:
        tech = canonical_technology(row.get("technology") or "")
        by_technology.setdefault(tech, []).append(row)
    tech_counts = {tech: len(items) for tech, items in by_technology.items()}
    sorted_techs = sorted(
        by_technology.keys(),
        key=lambda t: (-len(by_technology[t]), t.lower()),
    )
    return {
        "candidates": rows,
        "count": len(rows),
        "by_technology": tech_counts,
        "groups": {tech: by_technology[tech] for tech in sorted_techs},
    }


_SLOT_SCREENSHOT_NOTE_RE = re.compile(
    r"slot\s*screenshot|interview\s*(slot\s*)?screenshot|interview\s*confirmation",
    re.I,
)


def _is_slot_screenshot_proof(proof: dict) -> bool:
    note = (proof.get("note") or "").strip()
    if note and _SLOT_SCREENSHOT_NOTE_RE.search(note):
        return True
    if "payment" in note.lower():
        return False
    oname = (proof.get("original_name") or proof.get("filename") or "").lower()
    return oname.startswith("slot-screenshot")


def _slim_slot_screenshot_proof(cid: str, proof: dict) -> dict:
    pid = proof.get("id")
    return {
        "id": pid,
        "candidate_id": cid,
        "url": f"/candidates/{cid}/attachments/slot_screenshot_proof/{pid}",
        "note": proof.get("note"),
        "uploaded_at": proof.get("uploaded_at"),
        "original_name": proof.get("original_name") or proof.get("filename"),
    }


def _latest_slot_screenshot_proof(row: dict) -> dict | None:
    cid = str(row.get("id") or "")
    proof_id = _clean_str(row.get("slot_screenshot_proof_id"))
    if cid and proof_id:
        hit = get_attachment(
            cid, proof_id, AttachmentType.SLOT_SCREENSHOT_PROOF
        )
        if hit:
            _, entry = hit
            return _slim_slot_screenshot_proof(cid, entry)
    hits = partition_candidate_attachments(row)["slot_screenshot_proofs"]
    if not hits:
        return None
    # More than one screenshot accumulates whenever a slot is re-uploaded, or
    # when auto-booking evidence is later joined by a manual upload. Returning
    # None for that case hid the screenshot completely and the roster read
    # "Not available" for a booking that had two of them on disk. The roster
    # shows one thumbnail, and the newest upload is the one that describes the
    # current booking — which is what this function's name already promised.
    latest = max(
        hits,
        key=lambda proof: (
            _clean_str(proof.get("uploaded_at")),
            _clean_str(proof.get("id")),
        ),
    )
    return _slim_slot_screenshot_proof(cid, latest)


def _resolve_slot_screenshot_proof(
    row: dict,
    *,
    by_id: dict[str, dict],
    by_name: dict[str, list[dict]],
) -> dict | None:
    """Return the slot screenshot stored on this interview row only."""
    del by_name  # kept for call-site compatibility
    cid = str(row.get("id") or "")
    full = by_id.get(cid) or row
    return _latest_slot_screenshot_proof(full)


def _enrich_interview_rows_with_slot_screenshots(rows: list[dict]) -> list[dict]:
    """Attach slot_screenshot_proof from this row or a same-name profile/slot clone."""
    if not rows:
        return rows
    all_candidates = _load().get("candidates") or []
    by_id = {str(raw.get("id") or ""): raw for raw in all_candidates if raw.get("id")}
    by_name: dict[str, list[dict]] = {}
    for raw in all_candidates:
        key = _normalise_candidate_name_key(
            canonical_candidate_name((raw.get("name") or "").strip()),
        )
        if key:
            by_name.setdefault(key, []).append(raw)
    enriched: list[dict] = []
    for row in rows:
        r = dict(row)
        proof = _resolve_slot_screenshot_proof(r, by_id=by_id, by_name=by_name)
        if proof:
            r["slot_screenshot_proof"] = proof
        enriched.append(r)
    return enriched


def daily_interview_roster(
    day: str,
    *,
    viewer_reference: str | None = None,
    filter_attendee: str | None = None,
    filter_search: str | None = None,
    filter_channel: str | None = None,
    filter_round: str | None = None,
    filter_technology: str | None = None,
    include_unconfirmed: bool = False,
) -> dict:
    """Confirmed interview slots for one calendar day (YYYY-MM-DD).

    Interview attenders (Nikhila, Bhavana) see slots assigned to them.
    Other handlers see only their referred candidates. Admins see everything.
    """
    day = (day or "").strip()[:10]
    if len(day) != 10 or day[4] != "-" or day[7] != "-":
        raise ValueError("date must be YYYY-MM-DD")

    rows = _interview_rows_for_range(day, day, include_unconfirmed=include_unconfirmed)
    rows = _filter_interview_rows(
        rows,
        viewer_reference=viewer_reference,
        filter_attendee=filter_attendee,
        filter_search=filter_search,
        filter_channel=filter_channel,
        filter_round=filter_round,
        filter_technology=filter_technology,
    )
    counts = _interview_attendance_counts(rows)
    rows = _enrich_interview_rows_with_slot_screenshots(rows)
    return {
        "date": day,
        "interviews": rows,
        "count": len(rows),
        **counts,
    }


def _interview_rows_for_range(
    from_date: str,
    to_date: str,
    *,
    include_unconfirmed: bool = False,
) -> list[dict]:
    start = (from_date or "").strip()[:10]
    end = (to_date or "").strip()[:10]
    if len(start) != 10 or len(end) != 10:
        raise ValueError("from and to must be YYYY-MM-DD")
    if start > end:
        start, end = end, start
    rows: list[dict] = []
    for raw in _load().get("candidates") or []:
        if raw.get("stage") in {"dropped", "fail"}:
            continue
        slot_date = (raw.get("date") or "").strip()[:10]
        if not slot_date or slot_date < start or slot_date > end:
            continue
        if not include_unconfirmed and not _coerce_bool(raw.get("slot_confirmed")):
            continue
        rows.append(_with_computed(raw))
    return rows


def _interview_time_sort_key(value: str) -> tuple[int, str]:
    """Minutes since midnight — earliest interview first in roster lists."""
    raw = (value or "").strip()
    if not raw:
        return (24 * 60 + 1, "")
    s = re.sub(r"\s+", " ", raw.lower().replace(".", ":"))
    s = s.replace("a.m.", "am").replace("p.m.", "pm")

    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?(?:\s*(am|pm))?$", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        ampm = m.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour * 60 + minute, raw)

    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        elif m.group(3) == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour * 60 + minute, raw)

    return (24 * 60, raw)


def _normalize_iso_date(value: str) -> str:
    """YYYY-MM-DD with zero-padded month/day so string sort matches calendar order."""
    raw = _clean_str(value)[:10]
    if not raw:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if not m:
        return raw
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _slot_chronological_sort_key(row: dict) -> tuple:
    """Earliest interview first — date, then time of day, then name."""
    day = _normalize_iso_date(row.get("date") or "")
    time_mins = _interview_time_sort_key(row.get("time") or "")[0]
    return (day, time_mins, (row.get("name") or "").lower())


def _slot_range_minutes(time: str, time_end: str = "") -> tuple[int, int] | None:
    """Start/end minutes since midnight; default 1hr when end missing or invalid."""
    start = _interview_time_sort_key(time or "")[0]
    if start >= 24 * 60:
        return None
    end = _interview_time_sort_key(time_end or "")[0]
    if end >= 24 * 60 or not (time_end or "").strip() or end <= start:
        end = start + 60
    return start, end


def _slot_ranges_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _interview_slot_still_upcoming(
    date_str: str,
    time: str,
    time_end: str = "",
    *,
    now: float | None = None,
) -> bool:
    """True when the slot end (IST) is still in the future — hides completed interviews."""
    from core.ist_time import ist_now

    day = (date_str or "").strip()[:10]
    if len(day) != 10:
        return True
    rng = _slot_range_minutes(time, time_end)
    if not rng:
        return True
    end_min = rng[1]
    try:
        y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    except ValueError:
        return True
    now_dt = ist_now(now)
    slot_end = now_dt.replace(
        year=y, month=m, day=d,
        hour=end_min // 60, minute=end_min % 60,
        second=0, microsecond=0,
    )
    return now_dt < slot_end


def _filter_upcoming_only_rows(rows: list[dict]) -> list[dict]:
    """Daily ops Upcoming tab — pending slots only (exclude resolved attendance)."""
    out: list[dict] = []
    for row in rows:
        # Any stored status is a resolved one. Naming four of them left a
        # Re-Service row sitting in Upcoming as though nothing had happened to
        # it, which is the same omission the counters had.
        if row_interview_attendance_status(row) in INTERVIEW_ATTENDANCE_STATUSES:
            continue
        out.append(row)
    out.sort(key=_slot_chronological_sort_key)
    return out


def _split_pending_interviews_by_slot_phase(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pending rows split into scheduled (slot not ended) vs awaiting status update."""
    scheduled: list[dict] = []
    awaiting: list[dict] = []
    for raw in rows:
        if row_interview_attendance_status(raw):
            continue
        row = dict(raw)
        slot_date = (row.get("date") or "").strip()[:10]
        slot_time = (row.get("time") or "").strip()
        slot_end = (row.get("time_end") or "").strip()
        still = _interview_slot_still_upcoming(slot_date, slot_time, slot_end)
        row["slot_phase"] = "scheduled" if still else "awaiting_status"
        if still:
            scheduled.append(row)
        else:
            awaiting.append(row)
    scheduled.sort(key=_slot_chronological_sort_key)
    awaiting.sort(key=_slot_chronological_sort_key)
    return scheduled, awaiting


def find_interview_slot_conflicts(
    date: str,
    time: str,
    time_end: str = "",
    *,
    exclude_candidate_id: str | None = None,
    attendee: str | None = None,
) -> list[dict]:
    """Confirmed slots on the same day that overlap the proposed time range.

    A clash is a clash for *a person*. Two candidates interviewed at the same
    hour by two different attendees are not competing for anything, and
    blocking one of them loses a real interview to a scheduling rule that was
    never about the schedule.

    So when the caller says who will attend, only that attendee's slots count.
    When it does not — or when either side's attendee is unknown — every
    overlap still counts, because an unattributed clash is exactly the case
    where guessing is unsafe. Existing callers that pass no attendee therefore
    behave exactly as before.
    """
    day = _clean_str(date)[:10]
    if len(day) != 10:
        return []
    proposed = _slot_range_minutes(time, time_end)
    if not proposed:
        return []
    exclude = _clean_str(exclude_candidate_id or "")
    wanted_attendee = _clean_str(attendee or "").casefold()
    conflicts: list[dict] = []
    for raw in _load().get("candidates") or []:
        if raw.get("stage") in {"dropped", "fail"}:
            continue
        if not _coerce_bool(raw.get("slot_confirmed")):
            continue
        cid = _clean_str(raw.get("id") or "")
        if exclude and cid == exclude:
            continue
        slot_date = _clean_str(raw.get("date") or "")[:10]
        if slot_date != day:
            continue
        existing = _slot_range_minutes(raw.get("time") or "", raw.get("time_end") or "")
        if not existing or not _slot_ranges_overlap(proposed, existing):
            continue
        row = _with_computed(raw)
        existing_attendee = row_interview_attendee(row)
        if wanted_attendee and _clean_str(existing_attendee).casefold() and _clean_str(existing_attendee).casefold() != wanted_attendee:
            # Different people, same hour — not a clash.
            continue
        conflicts.append({
            "id": cid,
            "name": row.get("name") or "",
            "date": slot_date,
            "time": row.get("time") or "",
            "time_end": row.get("time_end") or "",
            "interview_attendee": existing_attendee,
        })
    conflicts.sort(
        key=lambda r: (
            _interview_time_sort_key(r.get("time") or "")[0],
            (r.get("name") or "").lower(),
        ),
    )
    return conflicts


class SlotBookedError(ValueError):
    """Raised when a new slot overlaps an existing confirmed interview."""

    def __init__(
        self,
        *,
        date: str,
        time: str,
        time_end: str,
        conflicts: list[dict],
    ):
        self.date = date
        self.time = time
        self.time_end = time_end
        self.conflicts = conflicts
        if len(conflicts) == 1:
            who = conflicts[0].get("name") or "another candidate"
            super().__init__(f"This interview slot is already booked — {who} has this time.")
        else:
            names = ", ".join(c.get("name") or "Unknown" for c in conflicts[:3])
            if len(conflicts) > 3:
                names = f"{names} (+{len(conflicts) - 3} more)"
            super().__init__(f"This interview slot is already booked — overlaps with {names}.")


class SlotNotPersistedError(ValueError):
    """Raised when a booking call returned but storage does not hold the slot.

    A booking that is not durably stored must never be reported as booked. The
    corruption this guards against was silent precisely because the in-memory
    return value looked correct while the stored row had been overwritten.
    """

    def __init__(self, *, candidate_id: str, date: str, time: str, time_end: str, stored: dict | None):
        self.candidate_id = candidate_id
        self.date = date
        self.time = time
        self.time_end = time_end
        self.stored = stored
        if stored is None:
            detail = "the booking row is missing from storage"
        else:
            detail = (
                "stored slot is "
                f"{stored.get('date') or 'no date'} "
                f"{stored.get('time') or 'no time'}-{stored.get('time_end') or 'no end'} "
                f"confirmed={bool(stored.get('slot_confirmed'))}"
            )
        super().__init__(
            f"Interview slot {date} {time}-{time_end} was not persisted for "
            f"candidate {candidate_id} — {detail}."
        )


def slot_row_matches(row: dict | None, *, date: str, time: str, time_end: str) -> bool:
    """True when a stored row really holds this confirmed slot."""
    if not isinstance(row, dict):
        return False
    return (
        _clean_str(row.get("date"))[:10] == _clean_str(date)[:10]
        and _clean_str(row.get("time")) == _clean_str(time)
        and _clean_str(row.get("time_end")) == _clean_str(time_end)
        and _coerce_bool(row.get("slot_confirmed"))
    )


def assert_slot_persisted(candidate_id: str, *, date: str, time: str, time_end: str = "") -> dict:
    """Re-read past every cache and prove the slot survived the write."""
    cid = _clean_str(candidate_id)
    stored = next(
        (
            r for r in (_load(force=True).get("candidates") or [])
            if isinstance(r, dict) and str(r.get("id") or "") == cid
        ),
        None,
    )
    if not slot_row_matches(stored, date=date, time=time, time_end=time_end):
        raise SlotNotPersistedError(
            candidate_id=cid, date=date, time=time, time_end=time_end, stored=stored,
        )
    return _with_computed(stored)


class PaymentDueError(ValueError):
    """Raised when a candidate with dues must upload payment proof before booking."""

    def __init__(self, *, name: str, balance_due: int, needs_proof: bool = True):
        self.name = name
        self.balance_due = balance_due
        self.needs_proof = needs_proof
        if needs_proof:
            msg = (
                f"₹{balance_due:,} payment is pending for {name}. "
                "Upload your payment screenshot first, then book the interview slot."
            )
        else:
            msg = (
                f"₹{balance_due:,} payment is pending for {name}. "
                "Please pay your handler before booking an interview slot."
            )
        super().__init__(msg)


PAYMENT_PROOF_MAX_AGE_HOURS = 12


def _proof_uploaded_recently(entry: dict, max_hours: int = PAYMENT_PROOF_MAX_AGE_HOURS) -> bool:
    raw = (entry.get("uploaded_at") or "").strip()
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - ts <= timedelta(hours=max_hours)
    except ValueError:
        return False


def _best_row_for_slot_name(name: str) -> dict | None:
    """Representative in-progress profile row for public slot / proof actions."""
    canon = canonical_candidate_name(_clean_str(name))
    key = _normalise_candidate_name_key(canon)
    if not key:
        return None
    rows = [
        r for r in list_candidates(stage="in_progress", month="all")
        if _normalise_service_type(r.get("service_type"), r) != "round_wise"
    ]
    best: dict | None = None
    for row in rows:
        if _normalise_candidate_name_key(row.get("name") or "") != key:
            continue
        if best is None or _slot_picker_row_score(row, prefer_react_js=True) > _slot_picker_row_score(
            best, prefer_react_js=True
        ):
            best = row
    if best:
        return best
    for row in list_candidates(stage="in_progress", month="all"):
        if _normalise_candidate_name_key(row.get("name") or "") == key:
            return row
    return None


def candidate_id_for_slot_name(name: str) -> str | None:
    row = _best_row_for_slot_name(name)
    cid = (row or {}).get("id")
    return str(cid) if cid else None


def _round_wise_pending_payment_row(name: str) -> dict | None:
    """Return the current unpaid, unbooked round ledger row for a name."""
    canon = canonical_candidate_name(_clean_str(name))
    key = _normalise_candidate_name_key(canon)
    if not key:
        return None
    matches: list[dict] = []
    for row in list_candidates(stage="all", month="all"):
        if _normalise_candidate_name_key(row.get("name") or "") != key:
            continue
        if _normalise_service_type(row.get("service_type"), row) != "round_wise":
            continue
        computed = _with_computed(row)
        if _candidate_has_confirmed_slot(computed):
            continue
        expected = effective_expected_payment(computed)
        paid = int(computed.get("payment") or 0)
        if max(0, expected - paid) <= 0:
            continue
        matches.append(computed)
    if not matches:
        return None
    return max(matches, key=lambda row: _clean_str(row.get("created_at") or row.get("updated_at")))


def round_wise_payment_due_for_name(name: str) -> int:
    """Amount due for the next round, independent of old/profile balances."""
    pending = _round_wise_pending_payment_row(name)
    if pending:
        return max(0, effective_expected_payment(pending) - int(pending.get("payment") or 0))
    return baseline_for_service("round_wise")


def _round_wise_identity_source(name: str, *, exclude_id: str = "") -> dict | None:
    """Best existing same-name row from which a new round inherits identity."""
    key = _normalise_candidate_name_key(canonical_candidate_name(_clean_str(name)))
    if not key:
        return None
    matches = [
        row
        for row in list_candidates(stage="all", month="all")
        if str(row.get("id") or "") != str(exclude_id or "")
        and _normalise_candidate_name_key(row.get("name") or "") == key
        and (
            candidate_phone_identity(row.get("phone"))
            or _clean_str(row.get("reference"))
            or canonical_technology(row.get("technology")) not in {"", "Unspecified"}
        )
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda row: (
            int(bool(candidate_phone_identity(row.get("phone"))))
            + int(bool(_clean_str(row.get("reference"))))
            + int(canonical_technology(row.get("technology")) not in {"", "Unspecified"}),
            int(row.get("payment") or 0),
            _clean_str(row.get("updated_at") or row.get("created_at")),
        ),
    )


def ensure_round_wise_payment_row(
    name: str,
    *,
    phone: str,
    technology: str,
    interview_round: str,
    allow_incomplete: bool = False,
) -> dict:
    """Create/reuse the unpaid round row before payment verification.

    First-time round-wise candidates are intentionally allowed on the public
    booking form.  The payment engine and proof store therefore need a stable
    candidate ID before either writes audit data.
    """
    canon = canonical_candidate_name(_clean_str(name))
    if not canon:
        raise ValueError("Enter your name")
    phone_key = candidate_phone_identity(phone)
    if not phone_key and not allow_incomplete:
        raise ValueError("Enter a valid phone number")
    tech = canonical_technology(_clean_str(technology))
    if (not tech or tech == "Unspecified") and not allow_incomplete:
        raise ValueError("Select the technology for this round")
    round_label = normalise_interview_round(interview_round)
    if not round_label and not allow_incomplete:
        raise ValueError("Select the interview round")

    pending = _round_wise_pending_payment_row(canon)
    identity_source = _round_wise_identity_source(
        canon,
        exclude_id=str((pending or {}).get("id") or ""),
    )
    if not phone_key and identity_source:
        inherited_phone = _clean_str(identity_source.get("phone"))
        phone_key = candidate_phone_identity(inherited_phone)
        if phone_key:
            phone = inherited_phone
    if (not tech or tech == "Unspecified") and identity_source:
        inherited_tech = canonical_technology(identity_source.get("technology"))
        if inherited_tech not in {"", "Unspecified"}:
            tech = inherited_tech
    inherited_reference = _clean_str((identity_source or {}).get("reference"))

    if pending:
        patch = {}
        if phone_key and candidate_phone_identity(pending.get("phone")) != phone_key:
            patch["phone"] = _clean_str(phone)
        if tech and tech != "Unspecified" and canonical_technology(pending.get("technology")) != tech:
            patch["technology"] = tech
        if inherited_reference and not _clean_str(pending.get("reference")):
            patch["reference"] = inherited_reference
        if round_label and normalise_interview_round(pending.get("interview_round")) != round_label:
            patch["interview_round"] = round_label
        if patch:
            pending = update_candidate(
                str(pending["id"]),
                patch,
                allow_slot_without_rules=True,
            ) or pending
        return pending

    return create_candidate(
        {
            "name": canon,
            "phone": _clean_str(phone),
            "technology": tech or "Unspecified",
            "reference": inherited_reference,
            "interview_round": round_label,
            "service_type": "round_wise",
            "interview_scope": "external",
            "stage": "in_progress",
            "task": "in_progress",
            "payment": 0,
            "expected_payment": baseline_for_service("round_wise"),
            "slot_confirmed": False,
        },
        allow_slot_without_rules=True,
    )


def _payment_proof_owner_for_slot_name(
    name: str,
    payment_proof_id: str | None,
) -> tuple[dict, tuple[Path, dict]] | None:
    """Find a proof across every same-name ledger row, not only the display row."""
    proof_id = _clean_str(payment_proof_id)
    key = _normalise_candidate_name_key(canonical_candidate_name(_clean_str(name)))
    if not proof_id or not key:
        return None
    for row in list_candidates(stage="all", month="all"):
        if _normalise_candidate_name_key(row.get("name") or "") != key:
            continue
        cid = _clean_str(row.get("id"))
        if not cid:
            continue
        hit = get_proof(cid, proof_id)
        if hit:
            return row, hit
    return None


def merged_balance_due_for_name(name: str, rows: list[dict] | None = None) -> int:
    """Outstanding balance once per profile — merged slot clones."""
    canon = canonical_candidate_name(_clean_str(name))
    if not canon or is_free_service_candidate(canon):
        return 0
    key = _normalise_candidate_name_key(canon)
    if not key:
        return 0
    if rows is None:
        rows = [_with_computed(r) for r in list_candidates(stage="in_progress", month="all")]
    else:
        rows = [_with_computed(r) for r in rows]
    profile_rows = [
        r for r in rows
        if _normalise_candidate_name_key(r.get("name") or "") == key
        and _normalise_service_type(r.get("service_type"), r) != "round_wise"
    ]
    if not profile_rows:
        profile_rows = [
            r for r in rows
            if _normalise_candidate_name_key(r.get("name") or "") == key
        ]
    if not profile_rows:
        return 0
    if len(profile_rows) == 1 and _normalise_service_type(profile_rows[0].get("service_type"), profile_rows[0]) == "round_wise":
        rep = profile_rows[0]
    else:
        rep = _merge_profile_rows_for_pending(profile_rows)
    balance = int(rep.get("balance_due") or 0)
    if balance <= 0:
        expected = effective_expected_payment(rep)
        paid = int(rep.get("payment") or 0)
        balance = max(0, expected - paid)
    return balance


def public_booking_payment_requirement(
    *,
    service_type: str,
    name: str = "",
    phone: str = "",
    candidate_id: str = "",
    interview_round: str = "",
) -> dict:
    """What a public slot booking must pay, and whether a proof is required.

    The single authority for the amount. The upload boundary verifies against
    it, /bookings/confirm enforces it, and the booking form asks it rather than
    deriving a figure of its own — which is how round-wise came to display a
    profile balance while the backend charged the round-wise tariff.

    The two services price differently and always have. Round-wise is a flat
    per-round tariff (`baseline_for_service`), owed on every round regardless of
    any profile balance; `merged_balance_due_for_name` deliberately excludes
    round-wise rows and answers a different question. Profile service bills the
    outstanding profile balance. Either is waived by an unused Re-Service grant,
    matching the waiver confirm already applies.
    """
    normalized_service = "round_wise" if _clean_str(service_type) == "round_wise" else "profile_service"
    if find_re_service_grant(
        name=name,
        phone=phone,
        interview_round=interview_round,
        candidate_id=candidate_id,
    ):
        return {
            "service_type": normalized_service,
            "payment_required": False,
            "amount_due": 0,
            "re_service": True,
        }
    if normalized_service == "round_wise":
        amount = max(0, int(baseline_for_service("round_wise") or 0))
        # Round-wise is payable per round, so a proof is always required. This
        # is the rule /bookings/confirm already enforces by refusing a
        # round-wise booking that carries no verified proof.
        return {
            "service_type": normalized_service,
            "payment_required": True,
            "amount_due": amount,
            "re_service": False,
        }
    amount = max(0, int(merged_balance_due_for_name(name) or 0)) if _clean_str(name) else 0
    return {
        "service_type": normalized_service,
        "payment_required": amount > 0,
        "amount_due": amount,
        "re_service": False,
    }


def slot_booking_payment_block_reason(
    name: str,
    *,
    payment_proof_id: str | None = None,
    require_payment_proof: bool = False,
    phone: str = "",
    interview_round: str = "",
) -> str | None:
    """None if the candidate may book; else human-readable payment blocker."""
    # One admin-granted free re-interview: no dues, no receipt, no expiry check.
    if candidate_is_re_service_eligible(
        name=name, phone=phone, interview_round=interview_round
    ):
        return None
    due = merged_balance_due_for_name(name)
    if due <= 0 and not require_payment_proof:
        return None
    canon = canonical_candidate_name(_clean_str(name))
    if not payment_proof_id:
        if require_payment_proof and due <= 0:
            return "Upload a verified payment screenshot before booking this round."
        return (
            f"₹{due:,} payment is pending for {canon or name}. "
            "Upload your payment screenshot first, then book the interview slot."
        )
    owner = _payment_proof_owner_for_slot_name(name, payment_proof_id)
    if not owner:
        return "Payment screenshot not found — upload it again before booking."
    _owner_row, hit = owner
    _path, entry = hit
    if _is_slot_screenshot_proof(entry):
        return "Upload a payment screenshot — an interview invite cannot be used as payment proof."
    from features.payment_verification_engine import stored_proof_is_booking_eligible
    if not stored_proof_is_booking_eligible(entry):
        return (
            "This receipt is not a verified company or registered-referrer payment. "
            "Upload a valid receipt or wait for authorized review."
        )
    if not _proof_uploaded_recently(entry):
        return (
            "Your payment screenshot has expired — upload a fresh payment screenshot, "
            "then book the interview slot."
        )
    return None


def _idempotent_round_wise_duplicate(name: str, fraud_check: dict) -> dict | None:
    """Reuse a recent verified proof for the same still-unbooked round."""
    key = _normalise_candidate_name_key(canonical_candidate_name(_clean_str(name)))
    if not key:
        return None
    rows_by_id = {
        str(row.get("id") or ""): row
        for row in (_load(force=True).get("candidates") or [])
        if isinstance(row, dict) and row.get("id")
    }
    from features.payment_verification_engine import stored_proof_is_booking_eligible

    for match in fraud_check.get("duplicate_matches") or []:
        cid = str(match.get("candidate_id") or "")
        proof_id = str(match.get("proof_id") or "")
        row = rows_by_id.get(cid)
        if not row or not proof_id:
            continue
        if _normalise_candidate_name_key(row.get("name") or "") != key:
            continue
        if _normalise_service_type(row.get("service_type"), row) != "round_wise":
            continue
        if _candidate_has_confirmed_slot(row):
            continue
        hit = get_proof(cid, proof_id)
        if not hit:
            continue
        _path, proof = hit
        if not stored_proof_is_booking_eligible(proof):
            continue
        if not _proof_uploaded_recently(proof):
            continue
        return {"candidate_id": cid, "row": row, "proof": proof}
    return None


def public_add_payment_proof_for_name(
    name: str,
    *,
    data: bytes,
    original_name: str,
    mime_type: str,
    note: str = "",
    extraction: dict | None = None,
    service_type: str = "",
) -> dict:
    """Attach a payment screenshot from the public submit-slot page.

    Also updates the received amount after centralized Ollama verification.
    """
    canon = canonical_candidate_name(_clean_str(name))
    if not canon:
        raise ValueError("Enter your name")
    is_round_wise = _normalise_service_type(service_type, {}) == "round_wise"
    pending = _round_wise_pending_payment_row(canon) if is_round_wise else None
    if is_round_wise:
        cid = str((pending or {}).get("id") or "")
        due = (
            max(0, effective_expected_payment(pending) - int(pending.get("payment") or 0))
            if pending
            else baseline_for_service("round_wise")
        )
    else:
        due = merged_balance_due_for_name(canon)
        cid = candidate_id_for_slot_name(canon)
    if due <= 0:
        raise ValueError("No payment due — you can book your interview slot directly.")
    if not cid:
        raise ValueError(
            "Round-wise candidate details were not saved. "
            "Re-enter the name, phone number, technology, and interview round."
            if is_round_wise
            else "Candidate not found — contact your coordinator."
        )
    engine_result = dict(extraction or {})
    if (
        engine_result.get("verification_engine")
        not in {"central_payment_verification_v1", "central_payment_verification_v2"}
        or not engine_result.get("booking_eligible")
    ):
        reasons = list(engine_result.get("deterministic_reasons") or [])
        raise ValueError(
            " ".join(reasons)
            or "This receipt is not a verified payment to the company account."
        )
    caption = _clean_str(note)[:200]
    if not caption:
        caption = f"Payment proof · ₹{due:,} due · submit-slot"
    from features.payment_fraud_detection import assess_payment_proof
    fraud_check = assess_payment_proof(data, extraction, candidate_id=cid, candidate_name=canon)
    if fraud_check["decision"] == "rejected":
        duplicate = _idempotent_round_wise_duplicate(canon, fraud_check) if is_round_wise else None
        if duplicate:
            return {
                "candidate_id": duplicate["candidate_id"],
                "proof_id": duplicate["proof"]["id"],
                "proof": duplicate["proof"],
                "balance_due": 0,
                "fraud_check": {**fraud_check, "decision": "idempotent", "verified": True},
                "name": canon,
                "reused_existing_proof": True,
            }
        match = (fraud_check.get("duplicate_matches") or [{}])[0]
        duplicate_name = match.get("candidate_name") or "another candidate"
        raise ValueError(f"Duplicate payment proof already used for {duplicate_name}.")
    entry = add_payment_proof(
        cid,
        data=data,
        original_name=original_name,
        mime_type=mime_type,
        note=caption,
        metadata={
            "sha256": fraud_check["sha256"],
            "fraud_decision": fraud_check["decision"],
            "fraud_reasons": fraud_check["reasons"],
            "fraud_warnings": fraud_check["warnings"],
            "fraud_checked_at": fraud_check["checked_at"],
            "utr_number": str(
                engine_result.get("utr_number")
                or engine_result.get("reference_number")
                or engine_result.get("transaction_id")
                or ""
            ),
            "transaction_id": str((extraction or {}).get("transaction_id") or ""),
            "payment_status": engine_result.get("status") or "",
            "company_payment_verified": bool(engine_result.get("company_payment_verified")),
            "booking_eligible": bool(engine_result.get("booking_eligible")),
            "verification_state": engine_result.get("verification_state") or "",
            "receiver_name": engine_result.get("receiver_name") or "",
            "receiver_upi_id": engine_result.get("receiver_upi_id") or "",
            "receiver_phone": engine_result.get("receiver_phone") or "",
            "receiver_account": engine_result.get("receiver_account") or "",
            "receiver_type": engine_result.get("receiver_type") or "company",
            "verified_amount": int(engine_result.get("amount") or 0),
            "ledger_entry_id": engine_result.get("ledger_entry_id") or "",
            "ledger_action": engine_result.get("ledger_action") or "",
            "ledger_status": engine_result.get("ledger_status") or "",
            "payment_id": engine_result.get("payment_id") or "",
            "evidence_id": engine_result.get("evidence_id") or "",
            "entitlement_id": engine_result.get("entitlement_id") or "",
            "payment_scope": engine_result.get("payment_scope") or "",
            "source_module": engine_result.get("source_module") or "",
        },
    )
    if entry is None:
        raise ValueError("Could not save payment screenshot — try again")
    # Auto-update received amount: add the due amount to payment
    # (since validation already confirmed the proof covers the full due)
    try:
        _auto_increment_payment_on_proof(cid, due)
    except Exception:
        pass  # Don't fail the upload if auto-update fails
    new_due = 0 if is_round_wise else merged_balance_due_for_name(canon)
    return {
        "candidate_id": cid,
        "proof_id": entry["id"],
        "proof": entry,
        "balance_due": new_due,
        "fraud_check": fraud_check,
        "name": canon,
    }


def _auto_increment_payment_on_proof(cid: str, amount_proven: int) -> None:
    """Add the proven amount to the candidate's received payment field."""
    data = _load(force=True)
    rows = data.get("candidates") or []
    for i, row in enumerate(rows):
        if row.get("id") == cid:
            current_payment = int(row.get("payment") or 0)
            expected = effective_expected_payment(row)
            new_payment = min(current_payment + amount_proven, expected)
            if new_payment > current_payment:
                rows[i] = dict(row)
                rows[i]["payment"] = new_payment
                rows[i]["updated_at"] = _now_iso()
                data["candidates"] = rows
                _save(data)
            return

def _resolve_public_slot_conflicts(
    *,
    candidate_name: str,
    date: str,
    time: str,
    time_end: str,
    exclude_candidate_id: str | None = None,
) -> None:
    """Apply booking priority: low-priority names yield slots to everyone else."""
    conflicts = find_interview_slot_conflicts(
        date, time, time_end, exclude_candidate_id=exclude_candidate_id,
    )
    if not conflicts:
        return

    if is_low_priority_slot_booker(candidate_name):
        raise SlotBookedError(
            date=date,
            time=time,
            time_end=time_end,
            conflicts=conflicts,
        )

    blocking = [
        c for c in conflicts
        if not is_low_priority_slot_booker(c.get("name") or "")
    ]
    bumpable = [
        c for c in conflicts
        if is_low_priority_slot_booker(c.get("name") or "")
    ]

    if blocking:
        raise SlotBookedError(
            date=date,
            time=time,
            time_end=time_end,
            conflicts=blocking,
        )

    for row in bumpable:
        cancel_interview_slot(candidate_id=row["id"])


def _filter_interview_rows(
    rows: list[dict],
    *,
    viewer_reference: str | None = None,
    filter_attendee: str | None = None,
    filter_search: str | None = None,
    filter_channel: str | None = None,
    filter_round: str | None = None,
    filter_technology: str | None = None,
) -> list[dict]:
    attendee_filter = (filter_attendee or "").strip()
    search_filter = (filter_search or "").strip()
    channel_filter = (filter_channel or "").strip().lower()
    viewer = (viewer_reference or "").strip()
    # Daily Ops is a shared operations roster.  Do not hide another
    # handler's booked interview from a handler session; every authenticated
    # operator needs the same live schedule as admin.
    if attendee_filter:
        needle = attendee_filter.lower()
        rows = [
            r for r in rows
            if _reference_key(row_interview_attendee(r)) == needle
        ]
    if search_filter:
        rows = [
            r for r in rows
            if candidate_matches_search(r.get("name") or "", search_filter)
        ]
    if channel_filter and channel_filter != "all":
        if channel_filter == "round_wise":
            rows = [
                r for r in rows
                if _normalise_service_type(r.get("service_type"), r) == "round_wise"
            ]
        elif channel_filter in {"profile", "profile_service"}:
            rows = [
                r for r in rows
                if _normalise_service_type(r.get("service_type"), r) != "round_wise"
            ]

    # Round filter — normalize both the filter value and each row's round
    round_val = (filter_round or "").strip()
    if round_val:
        round_key = normalise_interview_round(round_val)
        rows = [
            r for r in rows
            if normalise_interview_round(r.get("interview_round")) == round_key
        ]

    # Technology filter — normalize and compare case-insensitively
    tech_val = (filter_technology or "").strip()
    if tech_val:
        tech_key = canonical_technology(tech_val).lower()
        rows = [
            r for r in rows
            if canonical_technology(r.get("technology") or "").lower() == tech_key
        ]

    rows.sort(key=_slot_chronological_sort_key)
    return rows


def _interview_slot_is_future(row: dict) -> bool:
    from datetime import date

    slot_date = (row.get("date") or "").strip()[:10]
    if len(slot_date) != 10:
        return False
    return slot_date > date.today().isoformat()


def interview_monitor(
    from_date: str,
    to_date: str,
    *,
    viewer_reference: str | None = None,
    filter_attendee: str | None = None,
    filter_search: str | None = None,
    filter_channel: str | None = None,
    filter_round: str | None = None,
    filter_technology: str | None = None,
    include_unconfirmed: bool = False,
    upcoming_only: bool = False,
) -> dict:
    """All confirmed interview slots in a date range — admin monitor view."""
    rows = _interview_rows_for_range(
        from_date,
        to_date,
        include_unconfirmed=include_unconfirmed,
    )
    rows = _filter_interview_rows(
        rows,
        viewer_reference=viewer_reference,
        filter_attendee=filter_attendee,
        filter_search=filter_search,
        filter_channel=filter_channel,
        filter_round=filter_round,
        filter_technology=filter_technology,
    )
    counts = _interview_attendance_counts(rows)
    if upcoming_only:
        rows = _filter_upcoming_only_rows(rows)
    rows = _enrich_interview_rows_with_slot_screenshots(rows)
    by_date: dict[str, list[dict]] = {}
    by_attendee: dict[str, int] = {}
    for row in rows:
        day = (row.get("date") or "")[:10]
        by_date.setdefault(day, []).append(row)
        att = row_interview_attendee(row)
        by_attendee[att] = by_attendee.get(att, 0) + 1
    start = (from_date or "").strip()[:10]
    end = (to_date or "").strip()[:10]
    if start > end:
        start, end = end, start
    return {
        "from": start,
        "to": end,
        "interviews": rows,
        "count": len(rows),
        **counts,
        "by_date": {day: by_date[day] for day in sorted(by_date.keys())},
        "by_attendee": dict(sorted(by_attendee.items(), key=lambda kv: kv[0].lower())),
    }


def interview_upcoming(
    *,
    days: int = 14,
    filter_search: str | None = None,
    filter_attendee: str | None = None,
    filter_channel: str | None = None,
    viewer_reference: str | None = None,
    include_today_pending: bool = True,
    phase: str | None = None,
    lookback_days: int = 30,
) -> dict:
    """Team-wide pending interviews — split by slot phase when requested.

    phase:
      - scheduled: slot end still in the future (sidebar upcoming list)
      - awaiting_status: slot finished, attendance not logged yet
      - all / None: both groups combined
    """
    from datetime import date, timedelta

    today = date.today()
    forward_end = (today + timedelta(days=max(int(days), 1))).isoformat()
    lookback = max(int(lookback_days), 0)
    range_start = (today - timedelta(days=lookback)).isoformat()
    rows = _interview_rows_for_range(range_start, forward_end)
    rows = _filter_interview_rows(
        rows,
        viewer_reference=viewer_reference,
        filter_attendee=filter_attendee,
        filter_search=filter_search,
        filter_channel=filter_channel,
    )
    scheduled, awaiting = _split_pending_interviews_by_slot_phase(rows)
    phase_key = (phase or "all").strip().lower()
    if phase_key == "scheduled":
        out_rows = scheduled
    elif phase_key in {"awaiting_status", "awaiting", "pending_status"}:
        out_rows = awaiting
    else:
        out_rows = scheduled + awaiting
        out_rows.sort(key=_slot_chronological_sort_key)
    out_rows = _enrich_interview_rows_with_slot_screenshots(out_rows)
    counts = _interview_attendance_counts(out_rows)
    return {
        "from": range_start,
        "to": forward_end,
        "interviews": out_rows,
        "count": len(out_rows),
        "scheduled_count": len(scheduled),
        "awaiting_status_count": len(awaiting),
        **counts,
    }


def public_booked_interview_slots(*, days: int = 60) -> dict:
    """Confirmed interview slots for the public submit page (name + time only)."""
    from datetime import date, timedelta

    today = date.today()
    start = today.isoformat()
    end = (today + timedelta(days=max(int(days), 1))).isoformat()
    rows = _interview_rows_for_range(start, end)
    slots: list[dict] = []
    for row in rows:
        if row_interview_attendance_status(row):
            continue
        slot_date = (row.get("date") or "").strip()[:10]
        slot_time = (row.get("time") or "").strip()
        slot_end = (row.get("time_end") or "").strip()
        if not slot_date or not slot_time:
            continue
        # Show all of today's slots (even if time passed) so user sees just-booked slot
        if slot_date != today.isoformat() and not _interview_slot_still_upcoming(slot_date, slot_time, slot_end):
            continue
        slots.append({
            "name": canonical_candidate_name((row.get("name") or "").strip()),
            "technology": row_candidate_technology(row) or row.get("technology") or "",
            "interview_round": normalise_interview_round(row.get("interview_round")),
            "date": _normalize_iso_date(slot_date),
            "time": slot_time,
            "time_end": slot_end,
            # Provenance so the page can label how the slot was booked, through
            # the same resolver Daily Ops uses so the two surfaces cannot
            # disagree. It reads the stored source, then the persisted booking
            # note for older AI-mail rows — never anything the UI knows. An
            # unresolved row yields "" and the page shows plain "Booked".
            "interview_booking_source": interview_booking_source(row),
        })
    slots.sort(key=_slot_chronological_sort_key)
    return {
        "from": start,
        "to": end,
        "slots": slots,
        "count": len(slots),
    }


def clear_future_interview_attendance(*, by: str = "system") -> int:
    """Reset wrongly-logged attendance on slots after today (restores upcoming list)."""
    from datetime import date

    today = date.today().isoformat()
    data = _load()
    rows = data.get("candidates") or []
    changed = 0
    for i, raw in enumerate(rows):
        slot_date = (raw.get("date") or "").strip()[:10]
        if not slot_date or slot_date <= today:
            continue
        status = row_interview_attendance_status(raw)
        if status not in {"attended", "not_attended"}:
            continue
        r = dict(raw)
        r["interview_attendance_status"] = ""
        r["interview_attended"] = False
        r["interview_attendance_remark"] = ""
        r["interview_attended_at"] = ""
        r["interview_attended_by"] = ""
        r["updated_at"] = _now_iso()
        rows[i] = r
        changed += 1
    if changed:
        data["candidates"] = rows
        _save(data)
    return changed


def interview_global_summary(
    from_date: str,
    to_date: str,
    *,
    viewer_reference: str | None = None,
    filter_attendee: str | None = None,
    filter_search: str | None = None,
    filter_channel: str | None = None,
    filter_round: str | None = None,
    filter_technology: str | None = None,
    include_unconfirmed: bool = False,
    upcoming_only: bool = False,
) -> dict:
    """Ops snapshot — interviews by attendee/referrer/tech + tasks (scoped per viewer)."""
    start = (from_date or "").strip()[:10]
    end = (to_date or "").strip()[:10]
    if len(start) != 10 or len(end) != 10:
        raise ValueError("from and to must be YYYY-MM-DD")
    if start > end:
        start, end = end, start

    all_rows = _interview_rows_for_range(
        "2000-01-01",
        "2100-12-31",
        include_unconfirmed=include_unconfirmed,
    )
    all_rows = _filter_interview_rows(all_rows, viewer_reference=viewer_reference)
    rows = _interview_rows_for_range(
        start,
        end,
        include_unconfirmed=include_unconfirmed,
    )
    overview_rows = _filter_interview_rows(list(rows), viewer_reference=viewer_reference)
    rows = _filter_interview_rows(
        rows,
        viewer_reference=viewer_reference,
        filter_attendee=filter_attendee,
        filter_search=filter_search,
        filter_channel=filter_channel,
        filter_round=filter_round,
        filter_technology=filter_technology,
    )
    if upcoming_only:
        rows = _filter_upcoming_only_rows(rows)
    interview_counts = _interview_attendance_counts(rows)

    overview_candidates: dict[str, dict] = {}
    overview_levels: dict[str, int] = {}
    overview_technologies: dict[str, int] = {}
    for overview_row in overview_rows:
        candidate_label = canonical_candidate_name((overview_row.get("name") or "").strip()) or "Unknown"
        level_label = normalise_interview_round(overview_row.get("interview_round")) or "Unspecified"
        technology_label = row_candidate_technology(overview_row) or "Unspecified"
        candidate_bucket = overview_candidates.setdefault(candidate_label, {"count": 0, "levels": {}, "technologies": {}})
        candidate_bucket["count"] += 1
        candidate_bucket["levels"][level_label] = candidate_bucket["levels"].get(level_label, 0) + 1
        candidate_bucket["technologies"][technology_label] = candidate_bucket["technologies"].get(technology_label, 0) + 1
        overview_levels[level_label] = overview_levels.get(level_label, 0) + 1
        overview_technologies[technology_label] = overview_technologies.get(technology_label, 0) + 1

    # Month options are offered only for days a real interview actually sits on.
    # `all_rows` has already dropped cancelled, failed and unconfirmed records;
    # what remained was a shape check loose enough to admit "2027-99", so the
    # full date is parsed and anything that is not a real calendar day is
    # ignored rather than turned into a filter nobody can use.
    month_counts: dict[str, int] = {}
    for row in all_rows:
        slot_day = str(row.get("date") or "").strip()[:10]
        try:
            parsed_day = _date.fromisoformat(slot_day)
        except ValueError:
            continue
        # Taken from the parsed date rather than sliced off the string:
        # fromisoformat also accepts the dash-less "20260804", whose first
        # seven characters are not a month at all.
        month_key = f"{parsed_day.year:04d}-{parsed_day.month:02d}"
        month_counts[month_key] = month_counts.get(month_key, 0) + 1
    current_dt = datetime.now(timezone.utc)
    current_month = current_dt.strftime("%Y-%m")
    previous_year = current_dt.year if current_dt.month > 1 else current_dt.year - 1
    previous_month = current_dt.month - 1 if current_dt.month > 1 else 12
    month_counts.setdefault(current_month, 0)
    month_counts.setdefault(f"{previous_year:04d}-{previous_month:02d}", 0)
    month_names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    interview_months = []
    for month_key in sorted(month_counts, reverse=True):
        year, month = month_key.split("-")
        interview_months.append({
            "value": month_key,
            "label": f"{month_names[int(month) - 1]} {year}",
            "count": month_counts[month_key],
            "is_current": month_key == current_month,
        })

    def _empty_bucket() -> dict[str, int]:
        # "scheduled" is the booking state -- every row in the bucket -- and
        # the rest are the attendance statuses, taken from the same set the
        # counters use so a new one cannot silently land in "pending".
        return {
            "scheduled": 0,
            "pending": 0,
            **{status: 0 for status in sorted(INTERVIEW_ATTENDANCE_STATUSES)},
        }

    by_attendee: dict[str, dict[str, int]] = {}
    by_referrer: dict[str, dict[str, int]] = {}
    by_candidate: dict[str, dict[str, int]] = {}
    by_technology: dict[str, dict[str, int]] = {}

    def _bump(bucket: dict[str, dict[str, int]], key: str, status: str) -> None:
        label = (key or "").strip() or "Unknown"
        entry = bucket.setdefault(label, _empty_bucket())
        entry["scheduled"] += 1
        # Pending is the absence of a stored status. Naming four statuses here
        # and sweeping the rest into "pending" counted every Re-Service row as
        # pending in each of these four breakdowns.
        entry[status if status in INTERVIEW_ATTENDANCE_STATUSES else "pending"] += 1

    for row in rows:
        status = row_interview_attendance_status(row)
        _bump(by_attendee, row_interview_attendee(row), status)
        _bump(by_referrer, (row.get("reference") or "").strip() or "Unknown", status)
        _bump(by_candidate, canonical_candidate_name((row.get("name") or "").strip()) or "Unknown", status)
        _bump(by_technology, row_candidate_technology(row) or "Unspecified", status)

    def _bucket_rows(bucket: dict[str, dict[str, int]]) -> list[dict]:
        return [
            {"name": name, **stats}
            for name, stats in sorted(
                bucket.items(),
                key=lambda kv: (-kv[1]["scheduled"], kv[0].lower()),
            )
        ]

    from features import operator_tasks_store

    viewer = (viewer_reference or "").strip()
    task_scope = None
    if viewer and not _is_interview_attender_reference(viewer):
        task_scope = viewer

    task_by_handler: dict[str, dict[str, int]] = {}
    task_totals = {"open": 0, "done": 0}
    for task in operator_tasks_store.list_tasks(include_done=True, reference=task_scope):
        day = (task.get("date") or "").strip()[:10]
        if day and (day < start or day > end):
            continue
        handler = (task.get("reference") or "").strip() or "Unknown"
        entry = task_by_handler.setdefault(handler, {"open": 0, "done": 0})
        if task.get("done"):
            entry["done"] += 1
            task_totals["done"] += 1
        else:
            entry["open"] += 1
            task_totals["open"] += 1

    return {
        "from": start,
        "to": end,
        "available_months": interview_months,
        "booking_overview": {
            "total": len(overview_rows),
            "by_candidate": [
                {
                    "name": name,
                    "count": stats["count"],
                    "levels": [
                        {"name": level, "count": count}
                        for level, count in sorted(stats["levels"].items(), key=lambda item: (-item[1], item[0].lower()))
                    ],
                    "technologies": [
                        {"name": technology, "count": count}
                        for technology, count in sorted(stats["technologies"].items(), key=lambda item: (-item[1], item[0].lower()))
                    ],
                }
                for name, stats in sorted(overview_candidates.items(), key=lambda item: (-item[1]["count"], item[0].lower()))
            ],
            "by_level": [
                {"name": name, "count": count}
                for name, count in sorted(overview_levels.items(), key=lambda item: (-item[1], item[0].lower()))
            ],
            "by_technology": [
                {"name": name, "count": count}
                for name, count in sorted(overview_technologies.items(), key=lambda item: (-item[1], item[0].lower()))
            ],
        },
        "interviews": {
            "count": len(rows),
            **interview_counts,
            "by_attendee": _bucket_rows(by_attendee),
            "by_referrer": _bucket_rows(by_referrer),
            "by_candidate": _bucket_rows(by_candidate),
            "by_technology": _bucket_rows(by_technology),
        },
        "tasks": {
            **task_totals,
            "by_handler": [
                {"name": name, **stats}
                for name, stats in sorted(
                    task_by_handler.items(),
                    key=lambda kv: (-(kv[1]["open"] + kv[1]["done"]), kv[0].lower()),
                )
            ],
        },
    }


def set_interview_attendance(
    cid: str,
    *,
    status: str = "",
    remark: str = "",
    attended: bool | None = None,
    attendee: str | None = None,
    feedback: str | None = None,
    by: str,
    allow_future: bool = False,
) -> dict | None:
    data = _load()
    rows = data.get("candidates") or []
    for i, r in enumerate(rows):
        if r.get("id") != cid:
            continue
        r = dict(r)
        if attended is not None and not (status or "").strip():
            resolved_status = "attended" if attended else ""
        else:
            resolved_status = normalise_interview_attendance_status(status)
        if (
            not allow_future
            and resolved_status in {"attended", "not_attended"}
            and _interview_slot_is_future(r)
        ):
            raise ValueError("Attendance can only be logged on or after the interview date")
        remark_text = _clean_str(remark)[:500]
        r["interview_attendance_status"] = resolved_status
        r["interview_attended"] = resolved_status == "attended"
        # "Re-Service" is an entitlement, not an attendance outcome: it grants
        # one free repeat interview and leaves the round's own history alone.
        if resolved_status == RE_SERVICE_STATUS:
            r["re_service_eligible"] = True
            r["re_service_consumed"] = False
            r["re_service_granted_at"] = _now_iso()
            r["re_service_granted_by"] = (by or "").strip()[:120]
            r["re_service_consumed_at"] = ""
        # A completed re-service interview burns the one-time grant, so the
        # candidate's next booking is charged normally again.
        elif resolved_status == "attended" and _coerce_bool(r.get("re_service_booking")):
            r["re_service_eligible"] = False
            r["re_service_consumed"] = True
            r["re_service_consumed_at"] = _now_iso()
        # Feedback describes how an attended interview went, so it is kept only
        # while the round stays "attended". Omitting the field leaves whatever
        # was recorded before untouched.
        if resolved_status == "attended":
            if feedback is None:
                try:
                    r["interview_feedback"] = normalise_interview_feedback(
                        r.get("interview_feedback")
                    )
                except ValueError:
                    r["interview_feedback"] = ""
            else:
                r["interview_feedback"] = normalise_interview_feedback(feedback)
        else:
            r["interview_feedback"] = ""
        if resolved_status in {"attended", "not_attended"}:
            r["interview_attendance_remark"] = remark_text
            r["interview_attended_at"] = _now_iso()
            r["interview_attended_by"] = (by or "").strip()[:120]
            if attendee is not None:
                try:
                    r["interview_attendee"] = normalise_interview_attendee_name(attendee)
                except ValueError:
                    fallback = row_interview_attendee(r) or "Tool"
                    r["interview_attendee"] = normalise_interview_attendee_name(fallback)
            else:
                r["interview_attendee"] = row_interview_attendee(r)
        elif resolved_status in {"cancelled", "rescheduled", RE_SERVICE_STATUS}:
            # No attendee: nobody sat these interviews. The note is kept
            # because the form requires one for every status change, and
            # dropping it for Re-Service discarded what an admin had just been
            # made to type — on the one status that is an admin-only grant.
            r["interview_attendance_remark"] = remark_text
            r["interview_attended_at"] = _now_iso()
            r["interview_attended_by"] = (by or "").strip()[:120]
        else:
            r["interview_attendance_remark"] = ""
            r["interview_attended_at"] = ""
            r["interview_attended_by"] = ""
        r["updated_at"] = _now_iso()
        rows[i] = r
        data["candidates"] = rows
        _save(data)
        # The grant may have been issued on a different round's row, so clear it
        # at the source too — otherwise the benefit could be spent twice.
        grant_row_id = _clean_str(r.get("re_service_grant_row_id"))
        if (
            resolved_status == "attended"
            and _coerce_bool(r.get("re_service_booking"))
            and grant_row_id
            and grant_row_id != str(cid)
        ):
            consume_re_service_grant(grant_row_id, booking_id=str(cid))
        return _with_computed(r)
    return None


def set_interview_attendee(
    cid: str,
    *,
    attendee: str = "",
    by: str = "",
) -> dict | None:
    data = _load()
    rows = data.get("candidates") or []
    for i, r in enumerate(rows):
        if r.get("id") != cid:
            continue
        r = dict(r)
        r["interview_attendee"] = normalise_interview_attendee_name(attendee)
        r["updated_at"] = _now_iso()
        rows[i] = r
        data["candidates"] = rows
        _save(data)
        return _with_computed(r)
    return None


def roster_csv_rows(rows: list[dict]) -> str:
    """Excel-friendly CSV: #, Name, Technology — quoted fields, sorted by tech then name."""
    import csv
    import io

    sorted_rows = sorted(
        rows,
        key=lambda r: (
            canonical_technology(r.get("technology") or "").lower(),
            (r.get("name") or "").strip().lower(),
        ),
    )
    buf = io.StringIO()
    writer = csv.writer(
        buf,
        lineterminator="\r\n",
        quoting=csv.QUOTE_ALL,
    )
    writer.writerow(["#", "Name", "Technology"])
    for idx, row in enumerate(sorted_rows, start=1):
        writer.writerow([
            idx,
            (row.get("name") or "").strip(),
            canonical_technology(row.get("technology") or ""),
        ])
    return buf.getvalue()


def get_candidate(cid: str) -> dict | None:
    reconcile_resume_metadata()
    for r in _load().get("candidates") or []:
        if r.get("id") == cid:
            return _with_computed(r)
    return None


def get_candidate_detail(cid: str) -> dict | None:
    """Return the Candidates-page representation, including legacy clone proofs.

    Profile candidates can have older interview-slot rows with resumes or payment
    proofs attached. The list view collapses those rows, so the edit/detail API
    must use the same collapse or its proof count will disagree with the table.
    """
    source = get_candidate(cid)
    if not source:
        return None
    if _normalise_service_type(source.get("service_type"), source) == "round_wise":
        return source
    key = _normalise_candidate_name_key(source.get("name") or "")
    if not key:
        return source
    rows = [
        _with_computed(row)
        for row in (_load().get("candidates") or [])
        if _normalise_service_type(row.get("service_type"), row) != "round_wise"
        and _normalise_candidate_name_key(row.get("name") or "") == key
    ]
    collapsed = _collapse_profile_candidates(rows)
    return collapsed[0] if collapsed else source


def candidate_identity_ids(cid: str, *, include_name_matches: bool = True) -> list[str]:
    """Return every legacy row id belonging to one displayed candidate.

    Mailboxes may have been connected before duplicate profile rows were
    collapsed in the candidate list. Include exact canonical-name profile rows
    as well as explicit, phone, email, and database identity links so all Gmail
    accounts for that one person remain visible.

    Booking safety gates disable name-only matching: distinct people may have
    the same name. Display grouping keeps its existing compatibility default.
    """
    linked_ids = {str(cid)}
    try:
        from core.db.connection import get_connection, use_postgres
        if use_postgres():
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute("""SELECT canonical_candidate_id FROM candidate_identity_links
                  WHERE alias_candidate_id=%s""",(str(cid),))
                found=cur.fetchone()
                if found:
                    cur.execute("""SELECT alias_candidate_id FROM candidate_identity_links
                      WHERE canonical_candidate_id=%s ORDER BY alias_candidate_id""",(str(found[0]),))
                    linked_ids.update(str(row[0]) for row in cur.fetchall())
    except Exception:
        pass
    rows = _load().get("candidates") or []
    source = next((row for row in rows if str(row.get("id")) == str(cid)), None)
    if not source:
        return sorted(linked_ids)
    phone_key = candidate_phone_identity(source.get("phone"))
    email_key = str(source.get("email") or "").strip().casefold()
    explicit = str(source.get("canonical_candidate_id") or source.get("profile_candidate_id") or "").strip()
    source_is_profile = _normalise_service_type(source.get("service_type"), source) != "round_wise"
    source_name_key = _normalise_candidate_name_key(canonical_candidate_name(source.get("name") or ""))
    for row in rows:
        row_id=str(row.get("id") or "")
        if not row_id:continue
        row_phone=candidate_phone_identity(row.get("phone"))
        row_email=str(row.get("email") or "").strip().casefold()
        row_explicit=str(row.get("canonical_candidate_id") or row.get("profile_candidate_id") or "").strip()
        row_is_profile = _normalise_service_type(row.get("service_type"), row) != "round_wise"
        row_name_key = _normalise_candidate_name_key(canonical_candidate_name(row.get("name") or ""))
        if (
            row_id in linked_ids
            or (phone_key and row_phone==phone_key)
            or (email_key and '@' in email_key and row_email==email_key)
            or (explicit and (row_id==explicit or row_explicit==explicit))
            or row_explicit==str(cid)
            or (include_name_matches and source_is_profile and row_is_profile and source_name_key and row_name_key==source_name_key)
        ):
            linked_ids.add(row_id)
    return sorted(linked_ids)


def canonical_candidate_identity_id(cid: str) -> str:
    """Resolve a legacy slot/profile id to the candidate currently shown in lists."""
    aliases = set(candidate_identity_ids(cid))
    current = next(
        (row for row in list_candidates() if str(row.get("id")) in aliases),
        None,
    )
    return str(current.get("id")) if current else str(cid)


def canonical_candidate_identity_ids(candidate_ids: list[str]) -> dict[str, str]:
    """Resolve many mailbox candidate ids without one DB connection per id.

    Mailbox overview is polled while sync jobs are active.  Calling the scalar
    resolver for every mailbox repeated both the identity-link query and the
    candidate-list collapse, turning one dashboard request into an N+1 DB
    workload.  This preserves the scalar resolver's matching rules while
    loading links, source candidates and visible candidates once.
    """
    requested = list(dict.fromkeys(str(value) for value in candidate_ids if value))
    if not requested:
        return {}

    links: list[tuple[str, str]] = []
    try:
        from core.db.connection import get_connection, use_postgres
        if use_postgres():
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT alias_candidate_id,canonical_candidate_id
                      FROM candidate_identity_links"""
                )
                links = [(str(alias), str(canonical)) for alias, canonical in cur.fetchall()]
    except Exception:
        links = []

    canonical_by_alias = {alias: canonical for alias, canonical in links}
    aliases_by_canonical: dict[str, set[str]] = {}
    for alias, canonical in links:
        aliases_by_canonical.setdefault(canonical, set()).add(alias)

    rows = list(_load().get("candidates") or [])
    rows_by_id = {str(row.get("id")): row for row in rows if row.get("id")}
    visible_rows = list_candidates()
    resolved: dict[str, str] = {}

    for cid in requested:
        linked_ids = {cid}
        linked_canonical = canonical_by_alias.get(cid)
        if linked_canonical:
            linked_ids.add(linked_canonical)
            linked_ids.update(aliases_by_canonical.get(linked_canonical, set()))

        source = rows_by_id.get(cid)
        if source:
            phone_key = candidate_phone_identity(source.get("phone"))
            email_key = str(source.get("email") or "").strip().casefold()
            explicit = str(
                source.get("canonical_candidate_id")
                or source.get("profile_candidate_id")
                or ""
            ).strip()
            source_is_profile = (
                _normalise_service_type(source.get("service_type"), source)
                != "round_wise"
            )
            source_name_key = _normalise_candidate_name_key(
                canonical_candidate_name(source.get("name") or "")
            )
            for row in rows:
                row_id = str(row.get("id") or "")
                if not row_id:
                    continue
                row_phone = candidate_phone_identity(row.get("phone"))
                row_email = str(row.get("email") or "").strip().casefold()
                row_explicit = str(
                    row.get("canonical_candidate_id")
                    or row.get("profile_candidate_id")
                    or ""
                ).strip()
                row_is_profile = (
                    _normalise_service_type(row.get("service_type"), row)
                    != "round_wise"
                )
                row_name_key = _normalise_candidate_name_key(
                    canonical_candidate_name(row.get("name") or "")
                )
                if (
                    row_id in linked_ids
                    or (phone_key and row_phone == phone_key)
                    or (email_key and "@" in email_key and row_email == email_key)
                    or (explicit and (row_id == explicit or row_explicit == explicit))
                    or row_explicit == cid
                    or (
                        source_is_profile
                        and row_is_profile
                        and source_name_key
                        and row_name_key == source_name_key
                    )
                ):
                    linked_ids.add(row_id)

        current = next(
            (row for row in visible_rows if str(row.get("id")) in linked_ids),
            None,
        )
        resolved[cid] = str(current.get("id")) if current else cid
    return resolved


def find_by_telegram(slot: str, user_id: int) -> dict | None:
    """Find a candidate row linked to a Telegram DM thread."""
    slot = (slot or "").strip()
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    if not slot or uid <= 0:
        return None
    for row in _load().get("candidates") or []:
        if (row.get("telegram_slot") or "").strip() != slot:
            continue
        try:
            row_uid = int(row.get("telegram_user_id") or 0)
        except (TypeError, ValueError):
            row_uid = 0
        if row_uid == uid:
            return _with_computed(row)
    return None


def create_candidate(record: dict, *, allow_slot_without_rules: bool = False) -> dict:
    data = _load()
    row = _normalise(record)
    is_dropped = row.get("stage") == "dropped"
    if not is_dropped and not allow_slot_without_rules and _normalise_service_type(row.get("service_type"), row) != "round_wise":
        key = _normalise_candidate_name_key(row.get("name") or "")
        phone_key = candidate_phone_identity(row.get("phone"))
        duplicate = next(
            (
                existing for existing in (data.get("candidates") or [])
                if existing.get("stage") == "in_progress"
                and _normalise_service_type(existing.get("service_type"), existing) != "round_wise"
                and (
                    candidate_phone_identity(existing.get("phone")) == phone_key
                    if phone_key else _normalise_candidate_name_key(existing.get("name") or "") == key
                )
            ),
            None,
        )
        if (phone_key or key) and duplicate:
            raise ValueError(
                f"An active profile already exists for phone {row.get('phone') or row.get('name')}. "
                f"Open and update {duplicate.get('name') or 'the existing candidate'} instead."
            )
    if not is_dropped and row.get("slot_confirmed") and not allow_slot_without_rules:
        reason = slot_confirm_block_reason(row)
        if reason:
            raise ValueError(reason)
    data.setdefault("candidates", []).append(row)
    _save(data)
    return _with_computed(row)


def _validate_interview_slot_times(start: str, end: str) -> None:
    slot_start = _clean_str(start)
    slot_end = _clean_str(end)
    if not slot_start:
        raise ValueError("Interview start time is required")
    if not slot_end:
        raise ValueError("Interview end time is required")
    if _interview_time_sort_key(slot_end)[0] <= _interview_time_sort_key(slot_start)[0]:
        raise ValueError("End time must be after start time")


def create_interview_slot(
    *,
    name: str,
    date: str,
    time: str,
    time_end: str = "",
    technology: str = "",
    reference: str = "",
    phone: str = "",
    notes: str = "",
    interview_round: str = "",
    interview_attendee: str = "",
) -> dict:
    """Minimal ops shortcut — name, date, time (+ optional tech/ref). Slot is confirmed immediately."""
    name = canonical_candidate_name(_clean_str(name))
    if not name:
        raise ValueError("Candidate name is required")
    day = _clean_str(date)[:10]
    if len(day) != 10:
        raise ValueError("Interview date is required (YYYY-MM-DD)")
    slot_time = _clean_str(time)
    _validate_interview_slot_times(slot_time, time_end)
    attendee = normalise_interview_attendee_name(interview_attendee) if interview_attendee else infer_interview_attendee(technology, name)
    tech = canonical_technology(_clean_str(technology))
    if tech in {"", "Unspecified"} and candidate_defaults_to_tool_attendee(name):
        tech = TOOL_PROFILE_CANDIDATE_TECHNOLOGY
    record = {
        "name": name,
        "date": day,
        "time": slot_time,
        "time_end": _clean_str(time_end),
        "technology": tech,
        "reference": _canonical_reference_name(_clean_str(reference)),
        "phone": _clean_str(phone),
        "notes": _clean_str(notes),
        "interview_attendee": attendee,
        "interview_round": normalise_interview_round(interview_round),
        "interview_booking_source": "candidate_booked",
        "service_type": "round_wise",
        "interview_scope": "external",
        "stage": "in_progress",
        "task": "in_progress",
        "slot_confirmed": True,
        "slots_group_posted": True,
    }
    return create_candidate(record, allow_slot_without_rules=True)


def _candidate_has_confirmed_slot(row: dict) -> bool:
    if not _coerce_bool(row.get("slot_confirmed")):
        return False
    day = _clean_str(row.get("date"))[:10]
    return len(day) == 10


def candidate_has_confirmed_slot(row: dict) -> bool:
    """Does this row really carry a confirmed slot?

    Public view of the same predicate the importer uses, so the booking
    boundary can refuse to report success for a row that never got one.
    """
    return _candidate_has_confirmed_slot(row)


def _duplicate_candidate_slot(
    source: dict,
    *,
    date: str,
    time: str,
    time_end: str = "",
    notes: str = "",
    interview_round: str = "",
    interview_attendee: str | None = None,
    interview_company: str = "",
    interview_role: str = "",
    interview_source_thread_id: str = "",
    interview_source_message_id: str = "",
    interview_source_timezone: str = "",
    interview_calendar_uid: str = "",
    interview_calendar_sequence: str = "",
    interview_booking_source: str = "candidate_booked",
    booking_type: str = "Interview",
    assessment_key: str = "",
) -> dict:
    """Clone an in-progress candidate so a second interview slot keeps prior rows."""
    existing_service = _normalise_service_type(source.get("service_type"), source)
    attendee = ""
    if interview_attendee is not None:
        attendee = normalise_interview_attendee_name(interview_attendee)
    else:
        attendee = row_interview_attendee(source)

    record = {
        "name": source.get("name"),
        "technology": source.get("technology"),
        "phone": source.get("phone"),
        "reference": source.get("reference"),
        "consultancy": source.get("consultancy"),
        "service_type": existing_service,
        "interview_scope": source.get("interview_scope")
        or ("external" if existing_service == "round_wise" else ""),
        "purpose": source.get("purpose"),
        "payment": source.get("payment"),
        "expected_payment": source.get("expected_payment"),
        "task": "in_progress",
        "stage": "in_progress",
        "date": date,
        "logged_date": _row_lead_date(source),
        "time": time,
        "time_end": _clean_str(time_end),
        "notes": _clean_str(notes),
        "interview_attendee": attendee,
        "interview_round": normalise_interview_round(interview_round) or normalise_interview_round(source.get("interview_round")),
        "interview_company": _clean_str(interview_company),
        "interview_role": _clean_str(interview_role),
        "interview_source_thread_id": _clean_str(interview_source_thread_id),
        "interview_calendar_uid": _clean_str(interview_calendar_uid),
        "interview_calendar_sequence": _clean_str(interview_calendar_sequence),
        "interview_source_message_id": _clean_str(interview_source_message_id),
        "interview_source_timezone": _clean_str(interview_source_timezone),
        "interview_booking_source": _clean_str(interview_booking_source).lower() or "candidate_booked",
        "booking_type": _clean_str(booking_type) or "Interview",
        "assessment_key": _clean_str(assessment_key),
        "slot_confirmed": True,
        "slots_group_posted": True,
        "interview_attendance_status": "",
        "interview_attended": False,
        "payment_proofs": list(source.get("payment_proofs") or []),
        "slot_screenshot_proofs": [],
        # A new interview starts with no evidence of its own; inheriting the
        # source row's pointer would make this booking cite another one's proof.
        "slot_screenshot_proof_id": "",
        "profile_photo": source.get("profile_photo"),
        "resumes": list(source.get("resumes") or []),
    }
    return create_candidate(record, allow_slot_without_rules=True)


def assign_interview_slot(
    *,
    candidate_id: str,
    date: str,
    time: str,
    time_end: str = "",
    notes: str = "",
    interview_round: str = "",
    interview_attendee: str | None = None,
    interview_company: str = "",
    interview_role: str = "",
    interview_source_thread_id: str = "",
    interview_source_message_id: str = "",
    interview_source_timezone: str = "",
    interview_calendar_uid: str = "",
    interview_calendar_sequence: str = "",
    interview_booking_source: str = "candidate_booked",
    booking_type: str = "Interview",
    assessment_key: str = "",
) -> dict:
    """Schedule an existing candidate — first slot updates the record; later slots clone."""
    cid = _clean_str(candidate_id)
    if not cid:
        raise ValueError("Select a candidate")
    existing = get_candidate(cid)
    if not existing:
        raise ValueError("Candidate not found")
    day = _clean_str(date)[:10]
    if len(day) != 10:
        raise ValueError("Interview date is required (YYYY-MM-DD)")
    slot_time = _clean_str(time)
    _validate_interview_slot_times(slot_time, time_end)

    if _candidate_has_confirmed_slot(existing):
        clone = _duplicate_candidate_slot(
            existing,
            date=day,
            time=slot_time,
            time_end=time_end,
            notes=notes,
            interview_round=interview_round,
            interview_attendee=interview_attendee,
            interview_company=interview_company,
            interview_role=interview_role,
            interview_source_thread_id=interview_source_thread_id,
            interview_source_message_id=interview_source_message_id,
            interview_source_timezone=interview_source_timezone,
            # Without these the clone loses the calendar identity, so a later
            # revision cannot find it and a re-run books the event twice.
            interview_calendar_uid=interview_calendar_uid,
            interview_calendar_sequence=interview_calendar_sequence,
            interview_booking_source=interview_booking_source,
            # An assessment occupies the roster like an interview and must stay
            # distinguishable on it, so the type and the identity that makes a
            # reminder idempotent survive the clone.
            booking_type=booking_type,
            assessment_key=assessment_key,
        )
        # Report the slot only once storage actually holds it.
        assert_slot_persisted(
            str(clone.get("id") or ""), date=day, time=slot_time, time_end=time_end,
        )
        return clone

    existing_service = _normalise_service_type(existing.get("service_type"), existing)
    patch: dict = {
        "time": slot_time,
        "time_end": _clean_str(time_end),
        "service_type": existing_service,
        "interview_scope": existing.get("interview_scope") or ("external" if existing_service == "round_wise" else ""),
        "stage": "in_progress",
        "task": "in_progress",
        "slot_confirmed": True,
        "slots_group_posted": True,
    }
    logged = _clean_str(existing.get("logged_date"))[:10]
    existing_day = _clean_str(existing.get("date"))[:10]
    if len(logged) != 10:
        # Preserve the original date before overwriting with slot day.
        # Use existing date if available, otherwise fall back to created_at.
        if len(existing_day) == 10:
            patch["logged_date"] = existing_day
        else:
            created = _clean_str(existing.get("created_at"))[:10]
            if len(created) == 10:
                patch["logged_date"] = created
    patch["date"] = day
    extra = sanitize_candidate_notes(_clean_str(notes))
    if extra:
        prev = _clean_str(existing.get("notes"))
        patch["notes"] = f"{prev}\n{extra}".strip() if prev else extra
    if interview_attendee is not None:
        patch["interview_attendee"] = normalise_interview_attendee_name(interview_attendee)
    else:
        patch["interview_attendee"] = row_interview_attendee(existing)
    rnd = normalise_interview_round(interview_round)
    if rnd:
        patch["interview_round"] = rnd
    patch.update({
        "interview_company": _clean_str(interview_company),
        "interview_role": _clean_str(interview_role),
        "interview_source_thread_id": _clean_str(interview_source_thread_id),
        "interview_calendar_uid": _clean_str(interview_calendar_uid),
        "interview_calendar_sequence": _clean_str(interview_calendar_sequence),
        "interview_source_message_id": _clean_str(interview_source_message_id),
        "interview_source_timezone": _clean_str(interview_source_timezone),
        "interview_booking_source": _clean_str(interview_booking_source).lower() or "candidate_booked",
        "booking_type": _clean_str(booking_type) or "Interview",
        "assessment_key": _clean_str(assessment_key),
    })
    booked = update_candidate(cid, patch, allow_slot_without_rules=True)
    # Report the slot only once storage actually holds it.
    assert_slot_persisted(cid, date=day, time=slot_time, time_end=time_end)
    return booked


def update_interview_slot(
    *,
    candidate_id: str,
    date: str,
    time: str,
    time_end: str = "",
    notes: str = "",
    interview_round: str = "",
    technology: str | None = None,
    interview_attendee: str | None = None,
    interview_company: str | None = None,
    interview_role: str | None = None,
    interview_source_thread_id: str | None = None,
    interview_source_message_id: str | None = None,
    interview_source_timezone: str | None = None,
    interview_calendar_uid: str | None = None,
    interview_calendar_sequence: str | None = None,
    interview_booking_source: str | None = None,
) -> dict:
    """Reschedule an existing confirmed slot — updates date/time and optional notes."""
    cid = _clean_str(candidate_id)
    if not cid:
        raise ValueError("Candidate is required")
    existing = get_candidate(cid)
    if not existing:
        raise ValueError("Candidate not found")
    if not _coerce_bool(existing.get("slot_confirmed")):
        raise ValueError("This candidate has no confirmed interview slot")
    day = _clean_str(date)[:10]
    if len(day) != 10:
        raise ValueError("Interview date is required (YYYY-MM-DD)")
    slot_time = _clean_str(time)
    _validate_interview_slot_times(slot_time, time_end)
    patch: dict = {
        "date": day,
        "time": slot_time,
        "time_end": _clean_str(time_end),
        "slot_confirmed": True,
        "slots_group_posted": True,
    }
    if notes is not None:
        patch["notes"] = sanitize_candidate_notes(_clean_str(notes))
    if technology is not None:
        tech = canonical_technology(_clean_str(technology))
        if tech == "Unspecified":
            raise ValueError("Interview technology is required")
        patch["technology"] = tech
    if interview_attendee is not None:
        patch["interview_attendee"] = normalise_interview_attendee_name(interview_attendee)
    elif candidate_defaults_to_tool_attendee(existing.get("name") or ""):
        patch["interview_attendee"] = "Tool"
    rnd = normalise_interview_round(interview_round)
    if rnd:
        patch["interview_round"] = rnd
    for key, value in (
        ("interview_company", interview_company),
        ("interview_role", interview_role),
        ("interview_source_thread_id", interview_source_thread_id),
        ("interview_source_message_id", interview_source_message_id),
        ("interview_source_timezone", interview_source_timezone),
        ("interview_calendar_uid", interview_calendar_uid),
        ("interview_calendar_sequence", interview_calendar_sequence),
        ("interview_booking_source", interview_booking_source),
    ):
        if value is not None:
            patch[key] = _clean_str(value)
    rescheduled = update_candidate(cid, patch, allow_slot_without_rules=True)
    # A reschedule that did not survive the write must not read as rescheduled.
    assert_slot_persisted(cid, date=day, time=slot_time, time_end=time_end)
    return rescheduled


def cancel_confirmed_interview_slot_by_name(
    name: str,
    *,
    date: str = "",
    time: str = "",
    source: str = "public-upload",
    slot_image: bytes | None = None,
    slot_image_name: str = "",
    slot_image_mime: str = "",
) -> tuple[dict, str]:
    """Cancel a booked slot for a profile candidate matched by name (optional date/time hints)."""
    canon = canonical_candidate_name(_clean_str(name))
    if not canon:
        raise ValueError("Candidate name is required")

    rows = list_candidates(stage="all", month="all")
    key = _normalise_candidate_name_key(canon)
    confirmed = [
        r for r in rows
        if _normalise_candidate_name_key(r.get("name") or "") == key
        and _candidate_has_confirmed_slot(r)
    ]
    if not confirmed:
        raise ValueError(f"No booked interview slot found for {canon}.")

    day = _clean_str(date)[:10]
    slot_time = _clean_str(time)[:5]
    target: dict | None = None
    if day and slot_time:
        target = _find_existing_slot_row(rows, canon, day, slot_time)
    if target is None and day:
        same_day = [r for r in confirmed if (r.get("date") or "")[:10] == day]
        if len(same_day) == 1:
            target = same_day[0]
        elif slot_time:
            for row in same_day:
                if (_clean_str(row.get("time") or "")[:5]) == slot_time:
                    target = row
                    break
    if target is None and len(confirmed) == 1:
        target = confirmed[0]
    if target is None:
        raise ValueError(
            f"{canon} has multiple booked slots — upload a screenshot that clearly shows the date and time being cancelled."
        )

    row = cancel_interview_slot(candidate_id=str(target["id"]))
    if slot_image and row.get("id"):
        attach_public_slot_screenshot(
            str(row["id"]),
            data=slot_image,
            original_name=slot_image_name or "slot-cancellation.jpg",
            mime_type=slot_image_mime or "image/jpeg",
            source=f"Cancellation screenshot · {source}",
        )
        row = get_candidate(str(row["id"])) or row
    return row, "cancelled"


def _pick_confirmed_slot_for_name(
    name: str,
    *,
    date: str = "",
    time: str = "",
    prefer_ended: bool = False,
) -> dict:
    """Resolve one confirmed slot row for cancel / reschedule / session-complete."""
    canon = canonical_candidate_name(_clean_str(name))
    if not canon:
        raise ValueError("Candidate name is required")

    rows = list_candidates(stage="all", month="all")
    key = _normalise_candidate_name_key(canon)
    confirmed = [
        r for r in rows
        if _normalise_candidate_name_key(r.get("name") or "") == key
        and _candidate_has_confirmed_slot(r)
    ]
    if not confirmed:
        raise ValueError(f"No booked interview slot found for {canon}.")

    day = _clean_str(date)[:10]
    slot_time = _clean_str(time)[:5]
    target: dict | None = None
    if day and slot_time:
        target = _find_existing_slot_row(rows, canon, day, slot_time)
    if target is None and day:
        same_day = [r for r in confirmed if (r.get("date") or "")[:10] == day]
        if len(same_day) == 1:
            target = same_day[0]
        elif slot_time:
            for row in same_day:
                if (_clean_str(row.get("time") or "")[:5]) == slot_time:
                    target = row
                    break
    if target is None and len(confirmed) == 1:
        target = confirmed[0]
    if target is None and prefer_ended:
        ended = [
            r for r in confirmed
            if not _interview_slot_still_upcoming(
                (r.get("date") or "")[:10],
                r.get("time") or "",
                r.get("time_end") or "",
            )
        ]
        if len(ended) == 1:
            target = ended[0]
        elif ended:
            ended.sort(key=_slot_chronological_sort_key)
            target = ended[-1]
    if target is None:
        raise ValueError(
            f"{canon} has multiple booked slots — pick the slot on submit-slot or include date/time in the screenshot."
        )
    return target


def reschedule_confirmed_interview_slot_by_name(
    name: str,
    *,
    date: str,
    time: str,
    time_end: str = "",
    interview_round: str = "",
    notes: str = "",
    source: str = "public-upload",
    slot_image: bytes | None = None,
    slot_image_name: str = "",
    slot_image_mime: str = "",
) -> tuple[dict, str]:
    """Move an existing booked slot to a new date/time from a reschedule screenshot."""
    canon = canonical_candidate_name(_clean_str(name))
    if not canon:
        raise ValueError("Candidate name is required")

    slot_time = _clean_str(time)
    if not slot_time:
        raise ValueError("New interview time is required")

    target = _pick_confirmed_slot_for_name(canon)
    day = _clean_str(date)[:10]
    if len(day) != 10:
        day = (target.get("date") or "")[:10]
    if len(day) != 10:
        raise ValueError("Include the new date (e.g. tomorrow) or use submit-slot with an invite screenshot.")

    slot_end = _default_slot_time_end(slot_time, time_end)
    _validate_interview_slot_times(slot_time, slot_end)

    _resolve_public_slot_conflicts(
        candidate_name=canon,
        date=day,
        time=slot_time,
        time_end=slot_end,
        exclude_candidate_id=str(target["id"]),
    )
    note = sanitize_candidate_notes(_clean_str(notes))
    row = update_interview_slot(
        candidate_id=str(target["id"]),
        date=day,
        time=slot_time,
        time_end=slot_end,
        notes=note,
        interview_round=interview_round or normalise_interview_round(target.get("interview_round")),
    )
    if slot_image and row.get("id"):
        attach_public_slot_screenshot(
            str(row["id"]),
            data=slot_image,
            original_name=slot_image_name or "slot-reschedule.jpg",
            mime_type=slot_image_mime or "image/jpeg",
            source=f"Reschedule screenshot · {source}",
        )
        row = get_candidate(str(row["id"])) or row
    return row, "rescheduled"


def mark_session_complete_by_name(
    name: str,
    *,
    date: str = "",
    time: str = "",
    source: str = "public-upload",
    slot_image: bytes | None = None,
    slot_image_name: str = "",
    slot_image_mime: str = "",
) -> tuple[dict, str]:
    """Mark attended from a Session complete screenshot."""
    canon = canonical_candidate_name(_clean_str(name))
    target = _pick_confirmed_slot_for_name(
        canon,
        date=date,
        time=time,
        prefer_ended=True,
    )
    row = set_interview_attendance(
        str(target["id"]),
        status="attended",
        remark="Session complete screenshot",
        by="submit-slot",
    )
    if not row:
        raise ValueError("Could not update attendance for this candidate.")
    if slot_image and row.get("id"):
        attach_public_slot_screenshot(
            str(row["id"]),
            data=slot_image,
            original_name=slot_image_name or "session-complete.jpg",
            mime_type=slot_image_mime or "image/jpeg",
            source=f"Session complete · {source}",
        )
        row = get_candidate(str(row["id"])) or row
    return row, "attended"


def cancel_interview_slot(*, candidate_id: str) -> dict:
    """Remove a confirmed slot from the roster without deleting the candidate.

    Every route that takes a slot off the roster comes through here -- the
    candidates screen, the low-priority conflict bump, and the auto-booking
    cancel classification -- so this is where a mail alert still claiming that
    booking has to be told. Without it the alert went on reporting "Auto
    Booked" for a slot Confirmed Slots and Daily Ops no longer showed.
    """
    cid = _clean_str(candidate_id)
    if not cid:
        raise ValueError("Candidate is required")
    data = _load()
    rows = data.get("candidates") or []
    for i, r in enumerate(rows):
        if r.get("id") != cid:
            continue
        r = dict(r)
        r["date"] = ""
        r["time"] = ""
        r["time_end"] = ""
        r["slot_confirmed"] = False
        r["slot_confirmed_at"] = ""
        r["interview_attendance_status"] = ""
        r["interview_attended"] = False
        r["interview_attendance_remark"] = ""
        r["interview_attended_at"] = ""
        r["interview_attended_by"] = ""
        r["updated_at"] = _now_iso()
        rows[i] = r
        data["candidates"] = rows
        _save(data)
        # After the write, never before: a mail claim is only wrong once the
        # slot is actually gone. A failure here must not undo the cancellation,
        # so it is logged and the read path catches what it missed.
        try:
            from core import recruitment_mail_store

            recruitment_mail_store.release_booking_claims(cid, reason="slot_cancelled")
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not release mail booking claims for %s", cid
            )
        return _with_computed(r)
    raise ValueError("Candidate not found")


def _default_slot_time_end(start: str, end: str = "") -> str:
    """Use parsed end, or start + 30 minutes when end is missing or invalid."""
    start = _clean_str(start)
    end = _clean_str(end)
    # Parse via the roster sort key so 12-hour input ("12:30 PM") works too.
    start_min = _interview_time_sort_key(start)[0]
    if end and end != start and _interview_time_sort_key(end)[0] > start_min:
        return end
    if start_min >= 24 * 60:  # blank or unparseable start — nothing to derive from
        return start
    total = start_min + 30
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _find_existing_slot_row(rows: list[dict], name: str, date: str, time: str) -> dict | None:
    canon = canonical_candidate_name(name)
    key = _normalise_candidate_name_key(canon)
    day = _clean_str(date)[:10]
    slot_time = _clean_str(time)[:5]
    for row in rows:
        if _normalise_candidate_name_key(row.get("name") or "") != key:
            continue
        if (row.get("date") or "")[:10] != day:
            continue
        if (_clean_str(row.get("time") or "")[:5]) != slot_time:
            continue
        return row
    return None


def _find_assignable_profile_row(rows: list[dict], name: str) -> dict | None:
    """Profile row without a confirmed slot — must have a scheduled interview date."""
    key = _normalise_candidate_name_key(canonical_candidate_name(name))
    matches: list[dict] = []
    for row in rows:
        if _normalise_candidate_name_key(row.get("name") or "") != key:
            continue
        if _normalise_service_type(row.get("service_type"), row) == "round_wise":
            continue
        if _candidate_has_confirmed_slot(row):
            continue
        matches.append(row)
    if not matches:
        return None
    return matches[0]


def attach_public_slot_screenshot(
    candidate_id: str,
    *,
    data: bytes,
    original_name: str = "",
    mime_type: str = "",
    source: str = "public-upload",
) -> dict | None:
    """Save the candidate's slot confirmation screenshot on their roster row."""
    cid = _clean_str(candidate_id)
    if not cid or not data:
        return None
    caption = f"Interview slot screenshot · {source}"[:200]
    entry = add_slot_screenshot_proof(
        cid,
        data=data,
        original_name=original_name or "slot-screenshot.jpg",
        mime_type=mime_type or "image/jpeg",
        note=caption,
        metadata={
            "source_module": "slot_booking",
            "source_endpoint": "/candidates/{cid}/slot-screenshot",
            "upload_context": source,
            "booking_id": cid,
        },
    )
    if not entry:
        return None
    # Only the evidence pointer moves. The booking's own fields are not part of
    # this write at all, so attaching a screenshot cannot regress a slot that
    # was confirmed moments earlier.
    _patch_row_fields(cid, {"slot_screenshot_proof_id": entry["id"]})
    return entry


def _finish_public_slot_import(
    row: dict,
    action: str,
    *,
    technology: str = "",
    phone: str = "",
    interview_round: str = "",
    slot_image: bytes | None = None,
    slot_image_name: str = "",
    slot_image_mime: str = "",
    source: str = "public-upload",
) -> tuple[dict, str]:
    tech = canonical_technology(_clean_str(technology))
    if tech and tech not in {"", "Unspecified"}:
        existing = row_candidate_technology(row) or (row.get("technology") or "")
        if not str(existing).strip() or str(existing).strip() in {"", "Unspecified"}:
            row = update_candidate(str(row["id"]), {"technology": tech}, allow_slot_without_rules=True)
    normalized_phone = _clean_str(phone)
    if normalized_phone and row.get("id"):
        row = update_candidate(
            str(row["id"]),
            {"phone": normalized_phone},
            allow_slot_without_rules=True,
        )
    rnd = normalise_interview_round(interview_round)
    if rnd and row.get("id"):
        row = update_candidate(
            str(row["id"]),
            {"interview_round": rnd},
            allow_slot_without_rules=True,
        )
    if slot_image and row.get("id"):
        attach_public_slot_screenshot(
            str(row["id"]),
            data=slot_image,
            original_name=slot_image_name,
            mime_type=slot_image_mime,
            source=source,
        )
        row = get_candidate(str(row["id"])) or row
    return row, action


def import_confirmed_interview_slot(**kwargs) -> tuple[dict, str]:
    """Book the slot, then record whether it consumed a Re-Service grant.

    The grant is resolved before booking because the booking itself may create
    a fresh round row; stamping the provenance afterwards lets the completed
    interview burn the benefit on whichever row originally carried it.
    """
    grant = find_re_service_grant(
        name=_clean_str(kwargs.get("name") or ""),
        phone=_clean_str(kwargs.get("phone") or ""),
        interview_round=_clean_str(kwargs.get("interview_round") or ""),
        candidate_id=_clean_str(kwargs.get("candidate_id") or ""),
    )
    row, action = _import_confirmed_interview_slot(**kwargs)
    if grant and isinstance(row, dict) and row.get("id"):
        row = _mark_re_service_booking(
            str(row["id"]), grant_row_id=str(grant.get("id") or "")
        ) or row
    return row, action


def _mark_re_service_booking(cid: str, *, grant_row_id: str) -> dict | None:
    """Stamp Re-Service provenance straight onto the stored row.

    This deliberately bypasses update_candidate(): the normaliser there rebuilds
    rows from known columns, which would silently drop the marker and leave the
    one-time benefit unconsumable.

    It runs immediately after a booking, so it uses the targeted row patch for
    the same reason the evidence attachment does — a whole-store write here
    would carry a pre-booking snapshot back over the slot it just stamped.
    """
    return _patch_row_fields(
        cid,
        {"re_service_booking": True, "re_service_grant_row_id": grant_row_id},
    )


def _import_confirmed_interview_slot(
    *,
    name: str,
    date: str,
    time: str,
    time_end: str = "",
    notes: str = "",
    technology: str = "",
    phone: str = "",
    interview_round: str = "",
    service_type: str = "round_wise",
    source: str = "public-upload",
    payment_proof_id: str | None = None,
    pending_payment_proof: tuple[str, dict] | None = None,
    pending_payment_proofs: list[tuple[str, dict]] | None = None,
    payment_reuse: dict | None = None,
    candidate_id: str = "",
    idempotency_key: str = "",
    slot_image: bytes | None = None,
    slot_image_name: str = "",
    slot_image_mime: str = "",
) -> tuple[dict, str]:
    """Create or assign a confirmed interview slot for a profile candidate."""
    canon = canonical_candidate_name(_clean_str(name))
    if not canon:
        raise ValueError("Candidate name is required")
    if excluded_from_public_slot_booking(canon):
        raise ValueError(f"{canon} is no longer booking interview slots.")

    is_round_wise = _normalise_service_type(service_type, {}) == "round_wise"
    booking_key = _clean_str(idempotency_key)
    if booking_key:
        # Only a row that actually carries the confirmed slot may satisfy the
        # retry. The key is written to the candidate row before the slot is
        # applied, so a confirm that was blocked afterwards leaves the key on a
        # row with no date, no time and slot_confirmed false. Matching on the
        # key alone then returned that row as a success for every later attempt
        # at the same slot — the caller saw "confirmed" while nothing was ever
        # booked, and the slot could never be re-booked because the poisoned key
        # short-circuited each retry.
        previous = next(
            (
                row
                for row in list_candidates(stage="all", month="all")
                if _clean_str(row.get("booking_idempotency_key")) == booking_key
                and _candidate_has_confirmed_slot(row)
            ),
            None,
        )
        if previous:
            return previous, "skip_exists"
    re_service_grant = find_re_service_grant(
        name=canon,
        phone=phone,
        interview_round=interview_round,
        candidate_id=candidate_id,
    )
    # A split payment presents several proofs; one is simply the common case.
    payment_proofs = list(
        pending_payment_proofs
        if pending_payment_proofs is not None
        else ([pending_payment_proof] if pending_payment_proof else [])
    )
    pay_block = None
    if not payment_proofs and not re_service_grant:
        pay_block = slot_booking_payment_block_reason(
            canon,
            payment_proof_id=payment_proof_id,
            require_payment_proof=is_round_wise,
            phone=phone,
            interview_round=interview_round,
        )
    if pay_block:
        due = merged_balance_due_for_name(canon)
        if is_round_wise and due <= 0:
            due = baseline_for_service("round_wise")
        raise PaymentDueError(name=canon, balance_due=due, needs_proof=not payment_proof_id)

    day = _clean_str(date)[:10]
    if len(day) != 10:
        raise ValueError("Interview date is required (YYYY-MM-DD)")
    slot_time = _clean_str(time)
    slot_end = _default_slot_time_end(slot_time, time_end)
    _validate_interview_slot_times(slot_time, slot_end)

    tech = canonical_technology(_clean_str(technology))
    normalized_phone = _clean_str(phone) if is_round_wise else ""
    rnd = normalise_interview_round(interview_round)
    if "Candidate" in source and not rnd:
        raise ValueError("Select the interview round (L1, L2, etc.)")

    rows = list_candidates(stage="all", month="all")
    reuse = dict(payment_reuse or {})
    if is_round_wise and reuse.get("reuse_allowed"):
        previous_booking_id = _clean_str(reuse.get("previousBookingId"))
        reused_payment_id = _clean_str(reuse.get("reusedPaymentId"))
        previous_booking = next(
            (row for row in rows if _clean_str(row.get("id")) == previous_booking_id),
            None,
        )
        if not previous_booking or not reused_payment_id:
            from features.payment_fraud_detection import PAYMENT_REUSE_BLOCKED_MESSAGE
            raise ValueError(PAYMENT_REUSE_BLOCKED_MESSAGE)
        _resolve_public_slot_conflicts(
            candidate_name=canon,
            date=day,
            time=slot_time,
            time_end=slot_end,
            exclude_candidate_id=previous_booking_id,
        )
        note = sanitize_candidate_notes(_clean_str(notes))
        row = _duplicate_candidate_slot(
            previous_booking,
            date=day,
            time=slot_time,
            time_end=slot_end,
            notes=note,
            interview_round=rnd,
        )
        row = update_candidate(
            str(row["id"]),
            {
                "technology": tech,
                "phone": normalized_phone,
                "previousBookingId": previous_booking_id,
                "reusedPaymentId": reused_payment_id,
            },
            allow_slot_without_rules=True,
        )
        return _finish_public_slot_import(
            row,
            "rebooked_with_reused_payment",
            technology=tech,
            phone=normalized_phone,
            interview_round=rnd,
            slot_image=slot_image,
            slot_image_name=slot_image_name,
            slot_image_mime=slot_image_mime,
            source=source,
        )
    proof_owner = _payment_proof_owner_for_slot_name(canon, payment_proof_id)
    existing = _find_existing_slot_row(rows, canon, day, slot_time)
    if existing and _candidate_has_confirmed_slot(existing):
        patch: dict = {}
        existing_end = _clean_str(existing.get("time_end"))
        if slot_end and slot_end != existing_end:
            patch["time_end"] = slot_end
        if rnd and rnd != normalise_interview_round(existing.get("interview_round")):
            patch["interview_round"] = rnd
        if patch:
            existing = update_candidate(str(existing["id"]), patch, allow_slot_without_rules=True)
        return _finish_public_slot_import(
            existing,
            "skip_exists" if not patch else "updated",
            technology=tech,
            phone=normalized_phone,
            interview_round=rnd,
            slot_image=slot_image,
            slot_image_name=slot_image_name,
            slot_image_mime=slot_image_mime,
            source=source,
        )

    _resolve_public_slot_conflicts(
        candidate_name=canon,
        date=day,
        time=slot_time,
        time_end=slot_end,
    )

    note = sanitize_candidate_notes(_clean_str(notes))

    # A round-wise receipt belongs to one independent ledger row. Book that
    # exact row so an older round or profile balance cannot be reused.
    if is_round_wise and proof_owner:
        paid_row, _proof = proof_owner
        if not _candidate_has_confirmed_slot(paid_row):
            row = assign_interview_slot(
                candidate_id=paid_row["id"],
                date=day,
                time=slot_time,
                time_end=slot_end,
                notes=note,
                interview_round=rnd,
            )
            return _finish_public_slot_import(
                row,
                "assigned_round_payment",
                technology=tech,
                phone=normalized_phone,
                interview_round=rnd,
                slot_image=slot_image,
                slot_image_name=slot_image_name,
                slot_image_mime=slot_image_mime,
                source=source,
            )

    if existing and not _candidate_has_confirmed_slot(existing):
        row = assign_interview_slot(
            candidate_id=existing["id"],
            date=day,
            time=slot_time,
            time_end=slot_end,
            notes=note,
            interview_round=rnd,
        )
        return _finish_public_slot_import(
            row,
            "assigned",
            technology=tech,
            phone=normalized_phone,
            interview_round=rnd,
            slot_image=slot_image,
            slot_image_name=slot_image_name,
            slot_image_mime=slot_image_mime,
            source=source,
        )

    profile_row = _find_assignable_profile_row(rows, canon)
    if profile_row:
        row = assign_interview_slot(
            candidate_id=profile_row["id"],
            date=day,
            time=slot_time,
            time_end=slot_end,
            notes=note,
            interview_round=rnd,
        )
        return _finish_public_slot_import(
            row,
            "assigned_profile",
            technology=tech,
            phone=normalized_phone,
            interview_round=rnd,
            slot_image=slot_image,
            slot_image_name=slot_image_name,
            slot_image_mime=slot_image_mime,
            source=source,
        )

    occupied = next(
        (
            r for r in rows
            if _normalise_candidate_name_key(r.get("name") or "") == _normalise_candidate_name_key(canon)
            and _candidate_has_confirmed_slot(r)
        ),
        None,
    )
    if occupied:
        row = _duplicate_candidate_slot(
            occupied,
            date=day,
            time=slot_time,
            time_end=slot_end,
            notes=note,
            interview_round=rnd,
        )
        return _finish_public_slot_import(
            row,
            "cloned",
            technology=tech,
            phone=normalized_phone,
            interview_round=rnd,
            slot_image=slot_image,
            slot_image_name=slot_image_name,
            slot_image_mime=slot_image_mime,
            source=source,
        )

    # Round-wise candidates without a confirmed slot can still book new rounds.
    # Find any existing row by name and clone it for the new interview slot.
    if _normalise_service_type(service_type, {}) == "round_wise":
        any_match = next(
            (
                r for r in rows
                if _normalise_candidate_name_key(r.get("name") or "") == _normalise_candidate_name_key(canon)
            ),
            None,
        )
        if any_match:
            row = _duplicate_candidate_slot(
                any_match,
                date=day,
                time=slot_time,
                time_end=slot_end,
                notes=note,
                interview_round=rnd,
            )
            return _finish_public_slot_import(
                row,
                "cloned",
                technology=tech,
                phone=normalized_phone,
                interview_round=rnd,
                slot_image=slot_image,
                slot_image_name=slot_image_name,
                slot_image_mime=slot_image_mime,
                source=source,
            )

    # Never create a candidate or a confirmed Daily Ops slot from an unmatched
    # public/import name.  A real candidate must exist first; otherwise a
    # malformed upload can silently book someone who has no interview.
    # EXCEPTIONS: preset slot bookers (PUBLIC_SLOT_BOOKER_NAMES) and round-wise
    # bookings — round-wise clients type the name themselves per round, so a
    # first-time name must not be blocked.
    canon_key = _normalise_candidate_name_key(canon)
    is_preset = any(
        _normalise_candidate_name_key(n) == canon_key
        for n in PUBLIC_SLOT_BOOKER_NAMES
    )
    is_round_wise = _normalise_service_type(service_type, {}) == "round_wise"
    if is_preset or is_round_wise:
        # Auto-create the candidate record, then book the slot through the normal
        # assign path so it lands as a confirmed slot (slot_confirmed, booking
        # source, attendee) exactly like a booking for an existing candidate.
        auto_tech = tech or row_candidate_technology({"name": canon})
        auto_ref = "Thrilok"  # default reference for auto-created slot bookers
        new_candidate = create_candidate({
            "name": canon,
            "technology": auto_tech or "Unspecified",
            "phone": normalized_phone,
            "reference": auto_ref,
            "stage": "in_progress",
            "service_type": service_type or "round_wise",
        })
        new_candidate = assign_interview_slot(
            candidate_id=new_candidate["id"],
            date=day,
            time=slot_time,
            time_end=slot_end,
            notes=note,
            interview_round=rnd,
            interview_booking_source="candidate_booked",
        )
        return _finish_public_slot_import(
            new_candidate,
            "auto_created",
            technology=auto_tech,
            phone=normalized_phone,
            interview_round=rnd,
            slot_image=slot_image,
            slot_image_name=slot_image_name,
            slot_image_mime=slot_image_mime,
            source=source,
        )

    raise ValueError(
        f"No existing candidate matched {canon}. Add/select the candidate before booking an interview slot."
    )


def finalize_public_booking_payment(
    row: dict,
    *,
    pending_payment_proof: tuple[str, dict] | None = None,
    pending_payment_proofs: list[tuple[str, dict]] | None = None,
    payment_reuse: dict | None = None,
    idempotency_key: str = "",
) -> dict:
    """Attach temporary payment evidence only after booking confirmation.

    A fee settled in instalments arrives as several proofs. Each is attached in
    turn and the recorded payment is then derived from everything on the row,
    so 2,000 + 1,000 + 2,000 is recorded as the 5,000 it is.
    """
    cid = _clean_str(row.get("id"))
    if not cid:
        raise ValueError("Confirmed candidate record is missing")

    booking_key = _clean_str(idempotency_key)
    current = get_candidate(cid) or row
    patch: dict = {}
    if booking_key and _clean_str(current.get("booking_idempotency_key")) != booking_key:
        patch["booking_idempotency_key"] = booking_key
    reuse = dict(payment_reuse or {})
    if reuse.get("reuse_allowed"):
        previous_booking_id = _clean_str(reuse.get("previousBookingId"))
        reused_payment_id = _clean_str(reuse.get("reusedPaymentId"))
        if not previous_booking_id or not reused_payment_id:
            from features.payment_fraud_detection import PAYMENT_REUSE_BLOCKED_MESSAGE
            raise ValueError(PAYMENT_REUSE_BLOCKED_MESSAGE)
        patch["previousBookingId"] = previous_booking_id
        patch["reusedPaymentId"] = reused_payment_id

    proofs = list(
        pending_payment_proofs
        if pending_payment_proofs is not None
        else ([pending_payment_proof] if pending_payment_proof else [])
    )
    # Linking money is all-or-nothing. A split payment that attaches its
    # first instalment and then fails on the second used to leave the first
    # one spent: the row kept a claim on a receipt for a booking that never
    # completed, and the payer could not retry with it.
    attached_now: list[str] = []
    try:
        for path, pending in proofs:
            pending_id = _clean_str(pending.get("id"))
            existing_proof = next(
                (
                    proof
                    for proof in (current.get("payment_proofs") or [])
                    if _clean_str(proof.get("pending_proof_id")) == pending_id
                ),
                None,
            )
            if not existing_proof:
                with open(path, "rb") as handle:
                    raw = handle.read()
                verification = dict(pending.get("verification") or {})
                fraud_check = dict(pending.get("fraud_check") or {})
                entry = add_payment_proof(
                    cid,
                    data=raw,
                    original_name=_clean_str(pending.get("original_name")) or "payment.jpg",
                    mime_type=_clean_str(pending.get("mime_type")) or "image/jpeg",
                    note=_clean_str(pending.get("note"))
                    or "Verified payment proof · submit-slot",
                    metadata={
                        "pending_proof_id": pending_id,
                        "sha256": pending.get("sha256") or fraud_check.get("sha256") or "",
                        "fraud_decision": fraud_check.get("decision") or "",
                        "fraud_reasons": fraud_check.get("reasons") or [],
                        "fraud_warnings": fraud_check.get("warnings") or [],
                        "utr_number": str(
                            verification.get("utr_number")
                            or verification.get("reference_number")
                            or verification.get("transaction_id")
                            or ""
                        ),
                        "transaction_id": verification.get("transaction_id") or "",
                        "payment_status": verification.get("status") or "",
                        "company_payment_verified": bool(
                            verification.get("company_payment_verified")
                        ),
                        "booking_eligible": bool(verification.get("booking_eligible")),
                        "verification_state": verification.get("verification_state") or "",
                        "receiver_name": verification.get("receiver_name") or "",
                        "receiver_upi_id": verification.get("receiver_upi_id") or "",
                        "receiver_phone": verification.get("receiver_phone") or "",
                        "receiver_account": verification.get("receiver_account") or "",
                        "receiver_type": verification.get("receiver_type") or "company",
                        "verified_amount": int(verification.get("amount") or 0),
                        "payment_scope": verification.get("payment_scope") or "",
                        "source_module": "public_slot_confirmation",
                    },
                )
                if not entry:
                    raise ValueError("Could not attach verified payment proof")
                attached_now.append(_clean_str(entry.get("id")))
                current = get_candidate(cid) or current

        if proofs:
            # The proofs decide the amount, not the invoice. Booking used to add
            # the amount *due* and clamp the running total to the expected figure,
            # so a candidate who paid ₹6,000 against a ₹5,000 minimum was recorded
            # as having paid ₹5,000. Expected is a floor, not a ceiling. Summing
            # over every attached proof is also what makes a split payment add up;
            # verified_proof_total counts one transaction once, however many times
            # its screenshot was uploaded.
            patch["payment"] = payment_receipts.verified_proof_total(
                partition_candidate_attachments(current)["payment_proofs"]
            )

        if patch:
            current = update_candidate(cid, patch, allow_slot_without_rules=True)
        if reuse.get("reuse_allowed"):
            previous = get_candidate(_clean_str(reuse.get("previousBookingId")))
            if not previous:
                from features.payment_fraud_detection import PAYMENT_REUSE_BLOCKED_MESSAGE
                raise ValueError(PAYMENT_REUSE_BLOCKED_MESSAGE)
            existing_rebooking_id = _clean_str(previous.get("paymentReusedByBookingId"))
            if existing_rebooking_id and existing_rebooking_id != cid:
                from features.payment_fraud_detection import PAYMENT_REUSE_BLOCKED_MESSAGE
                raise ValueError(PAYMENT_REUSE_BLOCKED_MESSAGE)
            update_candidate(
                str(previous["id"]),
                {"paymentReusedByBookingId": cid},
                allow_slot_without_rules=True,
            )
    except Exception:
        detach_booking_payment_proofs(cid, attached_now)
        raise
    return current


def detach_booking_payment_proofs(cid: str, proof_ids) -> dict | None:
    """Undo payment linkage for a booking that did not complete.

    Attaching a proof is what consumes a payment: the fraud check scans the
    payment evidence attached to candidate rows, so a receipt that reaches a
    row is spent from then on. A confirmation that attaches evidence and then
    fails leaves the payer unable to retry with the receipt they actually paid
    with -- refused, correctly by its own logic, as "already linked to an
    active or completed booking" for a booking that never happened.

    So the linkage has to be undoable for exactly as long as the booking is
    still in doubt. This removes the attachment records named by `proof_ids`
    and re-derives the recorded payment from what is left, which is what makes
    a failed attempt leave no trace on the money.

    The uploaded file itself is deliberately kept: it is evidence, it is
    already mirrored into the managed evidence store, and nothing reads an
    orphaned file. Only the row's claim on it goes.
    """
    target = _clean_str(cid)
    wanted = {_clean_str(pid) for pid in (proof_ids or []) if _clean_str(pid)}
    if not target or not wanted:
        return None

    def _detach(row: dict) -> dict:
        kept = [
            proof
            for proof in (row.get("payment_proofs") or [])
            if _clean_str(proof.get("id")) not in wanted
        ]
        return {"payment_proofs": kept}

    row = _patch_row_fields(target, _detach)
    if row is None:
        return None
    # The amount is derived from the evidence, so it has to be re-derived once
    # the evidence is gone -- otherwise the row keeps the money it no longer
    # has proof of.
    remaining = partition_candidate_attachments(row)["payment_proofs"]
    return update_candidate(
        target,
        {"payment": payment_receipts.verified_proof_total(remaining)},
        allow_slot_without_rules=True,
    ) or row


def _slot_picker_dedupe_key(row: dict) -> str:
    return _normalise_candidate_name_key(
        canonical_candidate_name((row.get("name") or "").strip())
    )


def _slot_picker_row_score(row: dict, *, prefer_react_js: bool = False) -> tuple:
    """Prefer React JS profile, then rows without a date, then contact/payment."""
    tech = _technology_key(row_candidate_technology(row) or "")
    react_js = 1 if prefer_react_js and tech == "react js" else 0
    has_date = 1 if (row.get("date") or "").strip() else 0
    has_phone = 1 if (row.get("phone") or "").strip() else 0
    payment = int(row.get("payment") or 0)
    return (react_js, 0 if has_date else 1, has_phone, payment)


def _public_slot_booking_excluded_keys() -> frozenset[str]:
    return frozenset(
        _normalise_candidate_name_key(canonical_candidate_name(n))
        for n in PUBLIC_SLOT_BOOKING_EXCLUDED
    )


def excluded_from_public_slot_booking(name: str) -> bool:
    key = _normalise_candidate_name_key(canonical_candidate_name(_clean_str(name)))
    return key in _public_slot_booking_excluded_keys()


def _public_slot_url() -> str:
    base = (os.environ.get("OPERATIONS_PUBLIC_URL") or "").strip().rstrip("/")
    return f"{base}/submit-slot" if base else "the configured Operations submit-slot page"


def resolve_public_slot_candidate_from_hint(hint: str) -> tuple[str | None, str]:
    """Map WhatsApp display name / caption to a single public slot booker name."""
    raw = re.sub(r"^~\s*", "", _clean_str(hint))
    if not raw:
        return None, "Could not identify candidate — use the submit-slot link and pick your name."

    direct = canonical_candidate_name(raw)
    if direct and not excluded_from_public_slot_booking(direct):
        for preset in PUBLIC_SLOT_BOOKER_NAMES:
            if _normalise_candidate_name_key(preset) == _normalise_candidate_name_key(direct):
                return canonical_candidate_name(preset), ""

    matches: list[str] = []
    lower = raw.lower()
    for hint_key, name_parts in _CANDIDATE_SEARCH_HINTS.items():
        if len(hint_key) >= 4 and hint_key in lower:
            for preset in PUBLIC_SLOT_BOOKER_NAMES:
                if excluded_from_public_slot_booking(preset):
                    continue
                pk = _normalise_candidate_name_key(preset)
                if any(part in pk for part in name_parts):
                    matches.append(canonical_candidate_name(preset))
    for preset in PUBLIC_SLOT_BOOKER_NAMES:
        if excluded_from_public_slot_booking(preset):
            continue
        if candidate_matches_search(preset, raw):
            matches.append(canonical_candidate_name(preset))
    uniq = []
    seen: set[str] = set()
    for name in matches:
        key = _normalise_candidate_name_key(name)
        if key and key not in seen:
            seen.add(key)
            uniq.append(name)
    if len(uniq) == 1:
        return uniq[0], ""
    if len(uniq) > 1:
        return None, (
            f"Multiple candidates match “{raw}”. "
            f"Book via {_public_slot_url()} and select your name."
        )
    return None, (
        f"Could not match “{raw}” to a candidate. "
        f"Use {_public_slot_url()} and pick your name from the list."
    )


def interview_slot_picker_rows(
    *,
    reference: str | None = None,
    attendee_reference: str | None = None,
    channel: str | None = None,
) -> list[dict]:
    """In-progress candidates for the slot dropdown — one entry per profile name."""
    rows = list_candidates(stage="in_progress", month="all", reference=reference)
    ch = (channel or "").strip().lower()
    profile_channel = ch in {"", "profile", "profile_service"}
    if ch == "round_wise":
        rows = [
            r for r in rows
            if _normalise_service_type(r.get("service_type"), r) == "round_wise"
        ]
    elif profile_channel:
        rows = [
            r for r in rows
            if _normalise_service_type(r.get("service_type"), r) != "round_wise"
        ]
    viewer = (attendee_reference or "").strip()
    if viewer and _is_interview_attender_reference(viewer):
        key = viewer.lower()
        rows = [
            r for r in rows
            if _reference_key(row_interview_attendee(r)) == key
        ]

    best: dict[str, dict] = {}
    for row in rows:
        if excluded_from_public_slot_booking(row.get("name") or ""):
            continue
        dedupe = _slot_picker_dedupe_key(row)
        prev = best.get(dedupe)
        score = _slot_picker_row_score(row, prefer_react_js=profile_channel)
        if prev is None or score > _slot_picker_row_score(prev, prefer_react_js=profile_channel):
            best[dedupe] = row
    rows = list(best.values())
    rows.sort(key=lambda r: (r.get("name") or "").lower())
    out: list[dict] = []
    for r in rows:
        canon = canonical_candidate_name((r.get("name") or "").strip())
        due = merged_balance_due_for_name(canon)
        out.append({
            "id": r.get("id"),
            "name": canon,
            "technology": row_candidate_technology(r) or r.get("technology") or "",
            "phone": r.get("phone") or "",
            "date": r.get("date") or "",
            "time": r.get("time") or "",
            "service_type": r.get("service_type") or "",
            "balance_due": due,
            "needs_payment_proof": due > 0,
            "payment_blocked": False,
        })
    out.sort(key=lambda r: (r.get("name") or "").lower())
    return out


def interview_candidate_filter_options(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    channel: str | None = None,
    viewer_reference: str | None = None,
    filter_attendee: str | None = None,
) -> list[dict]:
    """Unique candidate names for roster filters — same store rows as interview monitor."""
    start = (from_date or "").strip()[:10]
    end = (to_date or "").strip()[:10]
    if len(start) == 10 and len(end) == 10:
        rows = _interview_rows_for_range(start, end)
        rows = _filter_interview_rows(
            rows,
            viewer_reference=viewer_reference,
            filter_attendee=filter_attendee,
            filter_channel=channel,
        )
    else:
        rows = [
            _with_computed(raw)
            for raw in (_load().get("candidates") or [])
            if raw.get("stage") == "in_progress"
        ]
        rows = _filter_interview_rows(
            rows,
            viewer_reference=viewer_reference,
            filter_attendee=filter_attendee,
            filter_channel=channel,
        )

    seen: set[str] = set()
    options: list[dict] = []
    for row in rows:
        display = canonical_candidate_name((row.get("name") or "").strip())
        if not display:
            continue
        key = _normalise_candidate_name_key(display)
        if key in seen:
            continue
        seen.add(key)
        options.append({"value": display, "label": display})

    options.sort(key=lambda item: item["label"].lower())
    return options


def update_candidate(
    cid: str,
    patch: dict,
    *,
    allow_slot_without_rules: bool = False,
) -> dict | None:
    data = _load()
    rows = data.get("candidates") or []
    for i, r in enumerate(rows):
        if r.get("id") == cid:
            original_phone_key = candidate_phone_identity(r.get("phone"))
            original_name_key = _normalise_candidate_name_key(r.get("name") or "")
            profile_identity_ids = {
                str(other.get("id"))
                for other in rows
                if other.get("id")
                and _normalise_service_type(other.get("service_type"), other) != "round_wise"
                and (
                    (
                        original_phone_key
                        and candidate_phone_identity(other.get("phone")) == original_phone_key
                    )
                    or (
                        original_name_key
                        and _normalise_candidate_name_key(other.get("name") or "")
                        == original_name_key
                    )
                )
            }
            allowed_patch = {k: v for k, v in patch.items() if k in _ALLOWED_FIELDS}
            # A typed Received amount is not authoritative. Where verified proof
            # evidence exists the proofs decide the total, so a manual edit
            # cannot quietly disagree with them.
            #
            # But `has_proof_evidence` is true as soon as a proof is ATTACHED,
            # not once one is verified, and `verified_proof_total` counts only
            # verified ones. A row whose proofs are all pending or under review
            # therefore had its recorded amount forced to the verified total,
            # which is zero -- so uploading two genuine screenshots that the
            # engine could not confirm erased a recorded 20,000.
            #
            # `recalculate_received_total` already refuses that exact reduction,
            # in those words: on a row that was never under proof control a
            # shortfall almost always means the payment predates proof capture,
            # and reducing the total would delete real money. The same rule
            # applies here. Proofs still win when they raise the figure, and
            # they win outright once the row is genuinely proof-controlled.
            if "payment" in allowed_patch:
                existing_proofs = partition_candidate_attachments(r)["payment_proofs"]
                if payment_receipts.has_proof_evidence(existing_proofs):
                    proof_total = payment_receipts.verified_proof_total(existing_proofs)
                    recorded = int(r.get("payment") or 0)
                    controlled = _coerce_bool(r.get("payment_proof_controlled"))
                    if controlled or proof_total > 0:
                        # Proofs speak. They may raise the total freely; they may
                        # lower it only once the row is genuinely proof-
                        # controlled, because on any other row a shortfall
                        # almost always means the payment predates proof
                        # capture and reducing it would delete real money --
                        # the rule recalculate_received_total already applies.
                        allowed_patch["payment"] = (
                            proof_total
                            if controlled or proof_total >= recorded
                            else recorded
                        )
                    else:
                        # Every attached proof is pending, queued, under review
                        # or refused. A verified total of zero is not a
                        # statement that zero rupees arrived; it is the absence
                        # of a statement, so the proofs decide nothing here.
                        #
                        # The submitted figure is accepted only where it does
                        # not reduce what is on record. Letting it reduce is
                        # what erased a recorded 20,000, and leaving that open
                        # to any caller -- not only the edit form -- is what
                        # made the erasure possible in the first place. A
                        # genuine reduction needs the proofs adjudicated, which
                        # puts the row under proof control and lets the branch
                        # above apply it.
                        typed = int(allowed_patch.get("payment") or 0)
                        allowed_patch["payment"] = max(typed, recorded)
            preview = _normalise(allowed_patch, existing=r)
            is_dropped = preview.get("stage") == "dropped"
            phone_key = candidate_phone_identity(preview.get("phone"))
            name_key = _normalise_candidate_name_key(preview.get("name") or "")
            if not is_dropped and phone_key and _normalise_service_type(preview.get("service_type"), preview) != "round_wise":
                conflict = next(
                    (
                        other for other in rows
                        if other.get("id") != cid
                        and other.get("stage") == "in_progress"
                        and _normalise_service_type(other.get("service_type"), other) != "round_wise"
                        and candidate_phone_identity(other.get("phone")) == phone_key
                        and _normalise_candidate_name_key(other.get("name") or "") != name_key
                    ),
                    None,
                )
                # Renaming an existing profile must not conflict with its own
                # legacy slot clones, which intentionally share the same phone.
                same_existing_identity = bool(
                    original_phone_key and phone_key == original_phone_key
                )
                if conflict and not same_existing_identity:
                    raise ValueError(
                        f"Phone {preview.get('phone')} already belongs to active candidate {conflict.get('name')}."
                    )
            if not is_dropped and preview.get("slot_confirmed") and not _coerce_bool(r.get("slot_confirmed")):
                if not allow_slot_without_rules:
                    reason = slot_confirm_block_reason(_with_computed(preview))
                    if reason:
                        raise ValueError(reason)
            rows[i] = _normalise(allowed_patch, existing=r)
            # Profile-service slot clones represent one commercial agreement.
            # Keep shared financial fields identical so list-page consolidation
            # cannot resurrect an older, higher value after an edit.
            shared_keys = {
                "name", "stage", "phone", "email", "technology",
                "payment", "expected_payment", "follow_up", "reference",
                "consultancy", "bgv_certificates", "ctc_percentage",
            }
            shared_patch = {k: rows[i].get(k) for k in shared_keys if k in allowed_patch}
            if shared_patch and _normalise_service_type(rows[i].get("service_type"), rows[i]) != "round_wise":
                for j, clone in enumerate(rows):
                    if j == i or _normalise_service_type(clone.get("service_type"), clone) == "round_wise":
                        continue
                    if str(clone.get("id")) not in profile_identity_ids:
                        continue
                    rows[j] = _normalise(shared_patch, existing=clone)
            data["candidates"] = rows
            _save(data)
            return _with_computed(rows[i])
    return None


def delete_candidate(cid: str) -> bool:
    data = _load()
    before = data.get("candidates") or []
    after = [r for r in before if r.get("id") != cid]
    if len(after) == len(before):
        return False
    data["candidates"] = after
    _save(data)
    return True


def _row_month(row: dict) -> str:
    """Extract a 'YYYY-MM' bucket from a row's lead date. Empty string if the
    date is missing or unparseable — those rows go into the 'undated' bin
    and only show up when month filter is 'all'."""
    raw = _row_lead_date(row)
    if not raw:
        return ""
    # Already normalised on insert (YYYY-MM-DD) so a slice is enough.
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    return ""


def _row_display_month(row: dict) -> str:
    """Month the candidate was registered (logged_date).

    Slot booking must NOT move a candidate to a different month.
    Uses logged_date (when the lead was first added) as the canonical month.
    Falls back to date only if logged_date is not available.
    """
    logged = _clean_str(row.get("logged_date"))[:10]
    if len(logged) >= 7 and logged[4] == "-":
        return logged[:7]
    # Fallback to date if logged_date missing
    visible = _clean_str(row.get("date"))[:10]
    if len(visible) >= 7 and visible[4] == "-":
        return visible[:7]
    return ""


def _row_in_month(row: dict, month: str) -> bool:
    """Match only the visible display date (the 'date' column in the table).

    The month filter should show/hide rows based on what the user sees
    in the Date column — not internal logged_date metadata.
    """
    if not month or month == "all":
        return True
    return _row_display_month(row) == month


def _handler_reference_options(
    all_rows: list[dict],
    *,
    month: str | None,
    scope_key: str | None = None,
) -> list[dict]:
    """All distinct referrers for handler filter dropdowns.

    Includes handlers with zero candidates in the active month so admins can
    still pick any referrer while a month filter is applied."""
    month_rows = all_rows
    if month and month != "all":
        month_rows = [r for r in all_rows if _row_in_month(r, month)]

    # The filter badge must describe the same consolidated profile rows that
    # the Candidates table renders.  Counting raw interview-slot duplicates
    # here made labels such as "Referrer One · 4" disagree with a 3-row table.
    month_rows = _collapse_profile_candidates(month_rows)

    month_counts: dict[str, int] = {}
    for r in month_rows:
        ref_raw = (r.get("reference") or "").strip()
        if not ref_raw or ref_raw.lower() == "unknown":
            continue
        name = _canonical_reference_name(ref_raw)
        key = _reference_key(name)
        month_counts[key] = month_counts.get(key, 0) + 1

    display_names: dict[str, str] = {}
    total_counts: dict[str, int] = {}
    for preset in HANDLER_REFERENCE_PRESETS:
        name = _canonical_reference_name(preset)
        if not name:
            continue
        key = _reference_key(name)
        display_names[key] = name
        total_counts.setdefault(key, 0)
    for r in _collapse_profile_candidates(all_rows):
        ref_raw = (r.get("reference") or "").strip()
        if not ref_raw or ref_raw.lower() == "unknown":
            continue
        name = _canonical_reference_name(ref_raw)
        key = _reference_key(name)
        display_names[key] = _prefer_reference_display(display_names.get(key, name), ref_raw)
        total_counts[key] = total_counts.get(key, 0) + 1

    keys = list(display_names.keys())
    if scope_key:
        keys = [k for k in keys if k == scope_key]

    options = [
        {
            "name": display_names[key],
            "month_count": month_counts.get(key, 0),
            "total_count": total_counts.get(key, 0),
        }
        for key in keys
    ]
    options.sort(
        key=lambda item: (
            -item["month_count"],
            -item["total_count"],
            item["name"].lower(),
        ),
    )
    return options


def available_months(rows: list[dict] | None = None) -> list[dict]:
    """Return YYYY-MM buckets, sorted newest first. Each entry has
    {value, label, count, is_current}.

    The current calendar month is ALWAYS included at the top of the list,
    even when it has zero candidates — that way the operator can switch
    to "this month" right after adding a new row without having to
    refresh the page. Same goes for the previous month (helps with
    end-of-month edge cases when working across timezones)."""
    if rows is None:
        rows = list_candidates()
    counts: dict[str, int] = {}
    for r in rows:
        m = _row_display_month(r)
        if m:
            counts[m] = counts.get(m, 0) + 1

    # Ensure current month + last month are present even when empty.
    today = datetime.now(timezone.utc)
    current = today.strftime("%Y-%m")
    counts.setdefault(current, 0)

    prev_year = today.year if today.month > 1 else today.year - 1
    prev_month = today.month - 1 if today.month > 1 else 12
    counts.setdefault(f"{prev_year:04d}-{prev_month:02d}", 0)

    sorted_months = sorted(counts.keys(), reverse=True)
    out = []
    month_names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    for m in sorted_months:
        try:
            year, mo = m.split("-")
            label = f"{month_names[int(mo) - 1]} {year}"
        except (ValueError, IndexError):
            label = m
        out.append({
            "value": m,
            "label": label,
            "count": counts[m],
            "is_current": m == current,
        })
    return out


def _carry_forward_balances(
    target_month: str,
    scope_key: str | None = None,
    service_type_filter: str | None = None,
) -> dict[str, dict]:
    """Compute cumulative candidate earnings and payouts made for each handler
    across ALL months strictly BEFORE `target_month`.

    Returns {handler_key: {"prior_commission": int, "prior_paid": int}}

    This is READ-ONLY — no writes, no data mutations.
    """
    if not target_month or target_month == "all":
        return {}

    # ── Cumulative commission and complimentary amounts from prior months ──
    store_data = _load()
    all_rows = [_with_computed(r) for r in (store_data.get("candidates") or [])]
    if service_type_filter and service_type_filter != "all":
        all_rows = [r for r in all_rows if _normalise_service_type(r.get("service_type"), r) == service_type_filter]

    # Only consider candidates from months strictly before target_month
    prior_rows = [r for r in all_rows if _row_display_month(r) and _row_display_month(r) < target_month]
    # Exclude April & May 2026 — those months are treated as fully settled
    prior_rows = [r for r in prior_rows if _row_display_month(r) not in ("2026-04", "2026-05")]
    # Deduplicate using the same logic as stats
    prior_rows = _stats_rows_deduped(prior_rows)

    # Which prior months actually moved each handler's balance. The UI states a
    # reason for the opening balance, and "carried from June" is only honest if
    # it names the months that really contributed.
    prior_months: dict[str, set[str]] = {}

    def _note_month(key: str, month_value: str | None) -> None:
        if key and month_value:
            prior_months.setdefault(key, set()).add(month_value)

    # Profile-closure complimentary is earned differently from ordinary
    # commission — it can even come from another handler's candidate — so it is
    # tracked separately. A carried balance that turns out to be an unpaid
    # closure complimentary should say so rather than read as generic arrears.
    prior_complimentary: dict[str, int] = {}
    prior_complimentary_count: dict[str, int] = {}

    prior_commission: dict[str, int] = {}
    for r in prior_rows:
        for ref_key, handler_share in handler_earning_allocations(r).items():
            if scope_key and ref_key != scope_key:
                continue
            prior_commission[ref_key] = prior_commission.get(ref_key, 0) + handler_share
            if handler_share:
                _note_month(ref_key, _row_display_month(r))

        closure_admin_key = _reference_key(PROFILE_CLOSURE_ADMIN_REFERENCE)
        row_reference = _reference_key(r.get("reference") or "")
        for comp_key, comp_amount in (
            (closure_admin_key, admin_complimentary_amount(r)),
            (row_reference, referrer_complimentary_amount(r)),
        ):
            if not comp_amount or not comp_key or comp_key == "unknown":
                continue
            if scope_key and comp_key != scope_key:
                continue
            prior_complimentary[comp_key] = (
                prior_complimentary.get(comp_key, 0) + comp_amount
            )
            prior_complimentary_count[comp_key] = (
                prior_complimentary_count.get(comp_key, 0) + 1
            )
            # Credit the month the profile actually closed, so the reason shown
            # against an opening balance names the closure month rather than
            # the month the lead happened to be registered in.
            _note_month(comp_key, _row_closure_month(r))

    # Every source below subtracts from — or adds to — a real payable balance.
    # If one cannot be read, the arithmetic still completes and still looks
    # confident, which is how a missing store once turned into overstated money
    # on screen. Record the failure instead of absorbing it.
    unreconciled_sources: list[str] = []

    # ── Cumulative salary from prior months ──
    prior_salary: dict[str, int] = {}
    try:
        from features import handler_salaries as _hs
        if not _hs.store_available():
            raise AccountingSourceUnavailable("handler_salaries")
        # Compute salary for each prior month individually
        # salary_owed_by_handler with month=None returns all-time; we need per-month
        # We'll estimate: if a handler has a monthly salary, multiply by # of prior months
        # Actually safer to call with each prior month, but that's expensive.
        # Instead, gather the distinct prior months and sum salary for each.
        prior_month_set = sorted(set(_row_display_month(r) for r in prior_rows if _row_display_month(r)))
        for pm in prior_month_set:
            if pm >= target_month:
                continue
            # Skip April & May 2026 — treated as settled
            if pm in ("2026-04", "2026-05"):
                continue
            sal = _hs.salary_owed_by_handler(month=pm)
            for key, sbucket in sal.items():
                if scope_key and key != scope_key:
                    continue
                owed_here = int(sbucket.get("owed") or 0)
                prior_salary[key] = prior_salary.get(key, 0) + owed_here
                if owed_here:
                    _note_month(key, pm)
    except Exception as exc:
        unreconciled_sources.append("handler_salaries")
        _log.error(
            "carry-forward: salary source unavailable, balances are unreconciled (%s)",
            exc.__class__.__name__,
        )

    # ── Cumulative payouts from prior months ──
    prior_paid: dict[str, int] = {}
    try:
        from features import handler_expenses as _he
        if not _he.store_available():
            raise AccountingSourceUnavailable("handler_expenses")
        # Get ALL expenses, then filter to months < target_month
        all_expenses = _he.list_expenses()
        for exp in all_expenses:
            exp_month = (exp.get("date") or "")[:7] if len(exp.get("date") or "") >= 7 else ""
            if not exp_month or exp_month >= target_month:
                continue
            # Skip April & May 2026 — treated as settled
            if exp_month in ("2026-04", "2026-05"):
                continue
            ref = (exp.get("reference") or "").strip()
            if not ref:
                continue
            key = ref.lower()
            if scope_key and key != scope_key:
                continue
            amount = int(exp.get("amount") or 0)
            prior_paid[key] = prior_paid.get(key, 0) + amount
            if amount:
                _note_month(key, exp_month)
    except Exception as exc:
        unreconciled_sources.append("handler_expenses")
        _log.error(
            "carry-forward: payout source unavailable, balances are unreconciled (%s)",
            exc.__class__.__name__,
        )

    # Sponsored candidate payments received by a referrer are recovered from
    # that referrer's future commission.
    prior_recoveries: dict[str, int] = {}
    try:
        from features.payment_verification_engine import ledger_available, ledger_entries
        if not ledger_available():
            raise AccountingSourceUnavailable("payment_verification_ledger")
        for entry in ledger_entries(action="referrer_recovery"):
            entry_month = str(entry.get("payment_date") or entry.get("created_at") or "")[:7]
            if not entry_month or entry_month >= target_month or entry_month in ("2026-04", "2026-05"):
                continue
            key = _reference_key(entry.get("referrer") or entry.get("receiver_registry_name") or "")
            if not key or (scope_key and key != scope_key):
                continue
            recovered_here = int(entry.get("amount") or 0)
            prior_recoveries[key] = prior_recoveries.get(key, 0) + recovered_here
            if recovered_here:
                _note_month(key, entry_month)
    except Exception as exc:
        unreconciled_sources.append("payment_verification_ledger")
        _log.error(
            "carry-forward: recovery ledger unavailable, balances are unreconciled (%s)",
            exc.__class__.__name__,
        )

    unreconciled_sources = sorted(set(unreconciled_sources))

    # Merge into result
    all_keys = (
        set(prior_commission.keys())
        | set(prior_salary.keys())
        | set(prior_paid.keys())
        | set(prior_recoveries.keys())
    )
    result: dict[str, dict] = {}
    for key in all_keys:
        comm = prior_commission.get(key, 0)
        sal = prior_salary.get(key, 0)
        paid = prior_paid.get(key, 0)
        recovery = prior_recoveries.get(key, 0)
        # For April & May 2026, prior months are considered settled
        # so don't carry anything forward from those months
        result[key] = {
            "prior_commission": comm,
            "prior_salary": sal,
            "prior_owed": comm + sal,
            "prior_paid": paid,
            "prior_recoveries": recovery,
            "prior_balance": (comm + sal) - paid - recovery,
            "prior_months": sorted(prior_months.get(key) or ()),
            # Part of prior_commission, not on top of it.
            "prior_complimentary": prior_complimentary.get(key, 0),
            "prior_complimentary_count": prior_complimentary_count.get(key, 0),
            # True when a source this figure depends on could not be read, so
            # the balance is arithmetically complete but factually incomplete.
            "unreconciled": bool(unreconciled_sources),
            "unreconciled_sources": list(unreconciled_sources),
        }
    return result


def stats(
    month: str | None = None,
    reference: str | None = None,
    *,
    service_type: str | None = None,
    _all_rows: list[dict] | None = None,
    _skip_pending_works: bool = False,
) -> dict:
    """Quick KPIs for the dashboard header.

    `month` is either:
      - None / "" / "all" → compute over ALL candidates (no filter)
      - 'YYYY-MM' (e.g. '2025-03') → only count candidates whose `date`
        falls in that calendar month.

    `reference` when set limits every aggregate to one handler/referrer —
    used so referrers never see other people's revenue.

    `service_type` filters by service channel: 'profile_service' or 'round_wise'.
    """
    scope_key: str | None = None
    if reference and str(reference).strip().lower() not in ("", "all"):
        scope_key = _reference_key(str(reference).strip())

    store_data = _load()
    if _all_rows is not None:
        unscoped_all_rows = list(_all_rows)
    else:
        unscoped_all_rows = [
            _with_computed(r) for r in (store_data.get("candidates") or [])
        ]
    all_rows = list(unscoped_all_rows)
    if scope_key:
        all_rows = [
            r for r in all_rows
            if _reference_key(r.get("reference") or "") == scope_key
        ]
    # Apply service_type filter before computing stats
    if service_type and service_type != "all":
        all_rows = [r for r in all_rows if _normalise_service_type(r.get("service_type"), r) == service_type]

    if month and month != "all":
        # Use list_candidates (the exact same function the breakdown modal calls)
        # to ensure the stat card revenue matches the breakdown total exactly.
        rows = list_candidates(month=month, reference=reference, service_type=service_type)
    else:
        rows = _stats_rows_deduped(all_rows)

    # Thrilok's admin complimentary amount is earned on every completed
    # profile, including profiles referred by someone else. Keep those rows
    # out of scoped candidate/revenue data and import only the ₹5k allocation.
    admin_extra_rows: list[dict] = []
    if scope_key == _reference_key(PROFILE_CLOSURE_ADMIN_REFERENCE):
        if month and month != "all":
            admin_source_rows = list_candidates(
                month=month,
                reference=None,
                service_type=service_type,
            )
        else:
            admin_source_rows = _stats_rows_deduped(unscoped_all_rows)
            if service_type and service_type != "all":
                admin_source_rows = [
                    row for row in admin_source_rows
                    if _normalise_service_type(row.get("service_type"), row) == service_type
                ]
        admin_extra_rows = [
            row for row in admin_source_rows
            if _reference_key(row.get("reference") or "") != scope_key
        ]

    total = len(rows)
    by_stage = {s: 0 for s in VALID_STAGES}
    revenue = 0
    revenue_by_tech: dict[str, int] = {}
    company_by_tech: dict[str, int] = {}
    company_revenue = 0
    company_revenue_completed = 0
    referral_commission = 0
    completed_revenue = 0
    expected_total = 0
    pending_total = 0
    pending_count = 0
    pending_no_remark = 0  # rows that still owe money AND have no follow-up note yet
    consultancy_count   = 0
    consultancy_revenue = 0
    direct_count        = 0
    direct_revenue      = 0

    perf: dict[str, dict] = {}
    _service_type_param = service_type  # preserve original param before loop overwrites it
    for r in rows:
        st = r.get("stage") or "in_progress"
        by_stage[st] = by_stage.get(st, 0) + 1
        amt = int(r.get("payment") or 0)
        is_consultancy = bool(r.get("consultancy"))
        service_type = _normalise_service_type(r.get("service_type"), r)
        interview_scope = _normalise_interview_scope(r.get("interview_scope"), r)
        expected = effective_expected_payment(r)
        balance = max(0, expected - amt)
        # Base commission stays at 50% of eligible cash. Closed profile-service
        # rows add ₹5k to the referrer and ₹5k to Thrilok as admin.
        base_commission = referrer_commission_amount(r)
        referrer_bonus = referrer_complimentary_amount(r)
        admin_bonus = admin_complimentary_amount(r)
        handler_share = base_commission + referrer_bonus + admin_bonus
        company_share = amt - handler_share
        revenue += amt
        referral_commission += handler_share
        company_revenue += company_share
        expected_total += expected
        if is_consultancy:
            consultancy_count   += 1
            consultancy_revenue += amt
        else:
            direct_count   += 1
            direct_revenue += amt
        tech = (r.get("technology") or "Unspecified").strip() or "Unspecified"
        revenue_by_tech[tech] = revenue_by_tech.get(tech, 0) + amt
        company_by_tech[tech] = company_by_tech.get(tech, 0) + company_share
        if st == "completed":
            completed_revenue += amt
            company_revenue_completed += company_share

        ref_raw = (r.get("reference") or "Unknown").strip() or "Unknown"
        ref_key = _reference_key(ref_raw)
        bucket = perf.get(ref_key)
        if bucket is None:
            bucket = {
                "ref_key": ref_key,
                "name": _canonical_reference_name(ref_raw) if ref_raw != "Unknown" else "Unknown",
                "count": 0, "completed": 0, "in_progress": 0,
                "fail": 0, "dropped": 0, "revenue_total": 0, "revenue_completed": 0,
                "pending_total": 0, "pending_count": 0,
                "auto_earnings_total": 0, "auto_earnings_completed": 0,
                "base_commission_total": 0,
                "referrer_complimentary_total": 0,
                "admin_complimentary_total": 0,
                "referrer_complimentary_count": 0,
                "admin_complimentary_count": 0,
                "company_revenue_total": 0, "company_revenue_completed": 0,
                "consultancy_count": 0,
            }
            perf[ref_key] = bucket
        else:
            bucket["name"] = _prefer_reference_display(bucket["name"], ref_raw)
        bucket["count"] += 1
        if st in bucket:
            bucket[st] += 1
        bucket["revenue_total"] += amt
        bucket["company_revenue_total"] += company_share
        referrer_earnings = base_commission + referrer_bonus
        bucket["auto_earnings_total"] += referrer_earnings
        bucket["base_commission_total"] += base_commission
        bucket["referrer_complimentary_total"] += referrer_bonus
        if referrer_bonus:
            bucket["referrer_complimentary_count"] += 1
        if is_consultancy:
            bucket["consultancy_count"] += 1
        if st == "completed":
            bucket["revenue_completed"] += amt
            bucket["company_revenue_completed"] += company_share
            bucket["auto_earnings_completed"] += referrer_earnings

        if admin_bonus and (not scope_key or scope_key == _reference_key(PROFILE_CLOSURE_ADMIN_REFERENCE)):
            admin_key = _reference_key(PROFILE_CLOSURE_ADMIN_REFERENCE)
            admin_bucket = perf.get(admin_key)
            if admin_bucket is None:
                admin_bucket = {
                    "ref_key": admin_key,
                    "name": PROFILE_CLOSURE_ADMIN_REFERENCE,
                    "count": 0, "completed": 0, "in_progress": 0,
                    "fail": 0, "dropped": 0,
                    "revenue_total": 0, "revenue_completed": 0,
                    "pending_total": 0, "pending_count": 0,
                    "auto_earnings_total": 0, "auto_earnings_completed": 0,
                    "base_commission_total": 0,
                    "referrer_complimentary_total": 0,
                    "admin_complimentary_total": 0,
                    "referrer_complimentary_count": 0,
                    "admin_complimentary_count": 0,
                    "company_revenue_total": 0, "company_revenue_completed": 0,
                    "consultancy_count": 0,
                }
                perf[admin_key] = admin_bucket
            admin_bucket["auto_earnings_total"] += admin_bonus
            admin_bucket["admin_complimentary_total"] += admin_bonus
            admin_bucket["admin_complimentary_count"] += 1
            admin_bucket["auto_earnings_completed"] += admin_bonus

    # A Thrilok-scoped request intentionally excludes other handlers' candidate
    # rows. Import only Thrilok's admin allocation from those rows so private
    # candidate/revenue details remain out of the scoped response.
    if admin_extra_rows:
        admin_key = _reference_key(PROFILE_CLOSURE_ADMIN_REFERENCE)
        admin_bucket = perf.get(admin_key)
        if admin_bucket is None:
            admin_bucket = {
                "ref_key": admin_key,
                "name": PROFILE_CLOSURE_ADMIN_REFERENCE,
                "count": 0, "completed": 0, "in_progress": 0,
                "fail": 0, "dropped": 0,
                "revenue_total": 0, "revenue_completed": 0,
                "pending_total": 0, "pending_count": 0,
                "auto_earnings_total": 0, "auto_earnings_completed": 0,
                "base_commission_total": 0,
                "referrer_complimentary_total": 0,
                "admin_complimentary_total": 0,
                "referrer_complimentary_count": 0,
                "admin_complimentary_count": 0,
                "company_revenue_total": 0, "company_revenue_completed": 0,
                "consultancy_count": 0,
            }
            perf[admin_key] = admin_bucket
        for row in admin_extra_rows:
            admin_bonus = admin_complimentary_amount(row)
            if not admin_bonus:
                continue
            admin_bucket["auto_earnings_total"] += admin_bonus
            admin_bucket["auto_earnings_completed"] += admin_bonus
            admin_bucket["admin_complimentary_total"] += admin_bonus
            admin_bucket["admin_complimentary_count"] += 1

    pending_total, pending_count, pending_no_remark, pending_by_ref = (
        _pending_collections_from_rows(rows)
    )
    for ref_key, pb in pending_by_ref.items():
        bucket = perf.get(ref_key)
        if bucket is not None:
            bucket["pending_total"] = pb["pending_total"]
            bucket["pending_count"] = pb["pending_count"]

    # Join in the handler_expenses ledger — which now represents money the
    # operator has ALREADY PAID OUT (commission disbursements, travel,
    # food, etc.). The handler's earnings are auto-computed above from the
    # 50% rule, so the ledger is no longer split into "earning vs
    # deduction" — every row is a payout against what they're owed.
    try:
        from features import handler_expenses as _he
        expense_summary = _he.summary_by_handler(
            month=month if month and month != "all" else None,
        )
    except Exception:
        expense_summary = {}
    if scope_key:
        expense_summary = {
            k: v for k, v in expense_summary.items()
            if k == scope_key or _reference_matches_scope(v.get("name") or k, scope_key)
        }

    try:
        from features.payment_verification_engine import recovery_summary_by_referrer
        recovery_summary = recovery_summary_by_referrer(
            month=month if month and month != "all" else None,
        )
        # That helper keys on referrer.lower(); the carry-forward keys through
        # _reference_key(), which resolves registry aliases. The mismatch made a
        # recovery recorded under an alias invisible in the month it happened
        # while still reducing the next month's opening balance, so the two
        # months disagreed by the recovery amount. Re-key here so a recovery is
        # counted once, in its own month, against the canonical handler.
        merged: dict[str, dict] = {}
        for raw_key, bucket in recovery_summary.items():
            key = _reference_key(bucket.get("name") or raw_key)
            target = merged.get(key)
            if target is None:
                merged[key] = dict(bucket)
                continue
            for field in (
                "total",
                "count",
                "commission_already_received",
                "recoverable_company_share",
            ):
                target[field] = int(target.get(field) or 0) + int(bucket.get(field) or 0)
        recovery_summary = merged
    except Exception:
        recovery_summary = {}
    if scope_key:
        recovery_summary = {
            k: v for k, v in recovery_summary.items()
            if k == scope_key or _reference_matches_scope(v.get("name") or k, scope_key)
        }

    # Join in the per-handler salary store. A handler can be on a hybrid
    # pay model: a fixed monthly salary (this) PLUS 50% commission on
    # their candidates' payments (computed above). Handlers with a
    # salary but no candidates this period still need to appear in
    # top_performers — we add a bucket for them below.
    try:
        from features import handler_salaries as _hs
        salary_summary = _hs.salary_owed_by_handler(
            month=month if month and month != "all" else None,
        )
    except Exception:
        salary_summary = {}
    if scope_key:
        salary_summary = {
            k: v for k, v in salary_summary.items()
            if k == scope_key or _reference_matches_scope(v.get("name") or k, scope_key)
        }

    # Make sure every salaried handler has a perf bucket, even those who
    # didn't refer anyone this period (otherwise their salary obligation
    # would silently disappear from the Top Performers panel).
    for key, sbucket in salary_summary.items():
        ref = sbucket.get("name") or key
        if _payout_excluded_handler(ref):
            continue
        if not _reference_matches_scope(ref, scope_key):
            continue
        ref_key = _reference_key(ref)
        if ref_key not in perf:
            perf[ref_key] = {
                "ref_key": ref_key,
                "name": _canonical_reference_name(ref) or ref,
                "count": 0, "completed": 0, "in_progress": 0,
                "fail": 0, "dropped": 0, "revenue_total": 0, "revenue_completed": 0,
                "pending_total": 0, "pending_count": 0,
                "auto_earnings_total": 0, "auto_earnings_completed": 0,
                "base_commission_total": 0,
                "referrer_complimentary_total": 0,
                "admin_complimentary_total": 0,
                "referrer_complimentary_count": 0,
                "admin_complimentary_count": 0,
                "company_revenue_total": 0, "company_revenue_completed": 0,
                "consultancy_count": 0,
            }
        else:
            perf[ref_key]["name"] = _prefer_reference_display(perf[ref_key]["name"], ref)

    total_handler_commission    = 0
    total_handler_base_commission = 0
    total_handler_referrer_complimentary = 0
    total_handler_admin_complimentary = 0
    total_handler_salary        = 0
    total_handler_paid_out      = 0
    total_handler_recoveries    = 0
    total_commission_already_received = 0
    total_company_share_recoverable = 0

    # Probed independently of the carry-forward so the warning still reaches
    # the client when there are no prior-month rows to attach it to.
    _unreconciled_sources = unavailable_accounting_sources()
    if _unreconciled_sources:
        _log.error(
            "earnings for %s are unreconciled — unreadable accounting stores: %s",
            month or "all", ", ".join(_unreconciled_sources),
        )

    # ── Carry-forward: compute cumulative balances from prior months ──
    carry_fwd: dict[str, dict] = {}
    if month and month != "all":
        carry_fwd = _carry_forward_balances(
            target_month=month,
            scope_key=scope_key,
            service_type_filter=_service_type_param if _service_type_param and _service_type_param != "all" else None,
        )
        # Make sure every handler with a non-zero carry-forward balance
        # has a perf bucket, even those with no candidates this month.
        for key, cf_data in carry_fwd.items():
            prior_bal = int(cf_data.get("prior_balance") or 0)
            if prior_bal == 0:
                continue
            if _payout_excluded_handler(key):
                continue
            ref_key = key  # already lowercased
            if ref_key not in perf:
                perf[ref_key] = {
                    "ref_key": ref_key,
                    "name": _canonical_reference_name(key) or key.title(),
                    "count": 0, "completed": 0, "in_progress": 0,
                    "fail": 0, "dropped": 0, "revenue_total": 0, "revenue_completed": 0,
                    "pending_total": 0, "pending_count": 0,
                    "auto_earnings_total": 0, "auto_earnings_completed": 0,
                    "base_commission_total": 0,
                    "referrer_complimentary_total": 0,
                    "admin_complimentary_total": 0,
                    "referrer_complimentary_count": 0,
                    "admin_complimentary_count": 0,
                    "company_revenue_total": 0, "company_revenue_completed": 0,
                    "consultancy_count": 0,
                }

    for p in perf.values():
        p["conversion_pct"] = (
            round((p["completed"] / p["count"]) * 100) if p["count"] else 0
        )
        key = (p.get("ref_key") or _reference_key(p.get("name"))).strip().lower()
        exp_bucket    = expense_summary.get(key, {})
        salary_bucket = salary_summary.get(key, {})
        recovery_bucket = recovery_summary.get(key, {})

        commission = int(p["auto_earnings_total"])
        base_commission = int(p.get("base_commission_total") or 0)
        referrer_complimentary = int(p.get("referrer_complimentary_total") or 0)
        admin_complimentary = int(p.get("admin_complimentary_total") or 0)
        complimentary = referrer_complimentary + admin_complimentary
        salary     = int(salary_bucket.get("owed") or 0)
        owed       = commission + salary
        paid_out   = int(exp_bucket.get("total") or 0)
        recoveries = int(recovery_bucket.get("total") or 0)
        commission_already_received = int(
            recovery_bucket.get("commission_already_received") or 0
        )
        company_share_recoverable = int(
            recovery_bucket.get("recoverable_company_share") or 0
        )

        # ── Carry-forward: only the NET BALANCE from prior months ──
        cf = carry_fwd.get(key, {})
        prior_balance = int(cf.get("prior_balance") or 0)

        # Salary-side fields — show THIS month's values only.
        p["commission_total"]  = commission
        p["complimentary_total"] = complimentary
        p["salary_total"]      = salary
        p["salary_monthly"]    = int(salary_bucket.get("monthly_salary") or 0)
        p["salary_active"]     = bool(salary_bucket.get("monthly_salary"))

        # Owed = commission + salary (THIS month only).
        p["auto_earnings_total"] = owed

        # Paid out = THIS month's payouts only.
        p["paid_out_total"]    = paid_out
        p["paid_out_count"]    = int(exp_bucket.get("count") or 0)
        p["recoveries_total"]  = recoveries
        p["recoveries_count"]  = int(recovery_bucket.get("count") or 0)
        p["commission_already_received_directly"] = commission_already_received
        p["recoverable_company_share"] = company_share_recoverable
        p["approved_expenses_total"] = paid_out
        p["month_end_commission_payout"] = commission - recoveries - paid_out

        # Month-end payout = commission/salary - recoveries - approved expenses.
        p["net_payable"]       = (owed - recoveries - paid_out) + prior_balance
        p["cash_payout"]       = max(0, p["net_payable"])
        p["carry_forward_receivable"] = max(0, -p["net_payable"])
        p["commission_pct"]    = HANDLER_COMMISSION_PCT

        # Carry-forward detail fields so the UI can state WHY an opening
        # balance exists instead of showing a bare figure.
        p["prior_balance"]     = prior_balance
        p["prior_commission"]  = int(cf.get("prior_commission") or 0)
        p["prior_salary"]      = int(cf.get("prior_salary") or 0)
        p["prior_owed"]        = int(cf.get("prior_owed") or 0)
        p["prior_paid"]        = int(cf.get("prior_paid") or 0)
        p["prior_recoveries"]  = int(cf.get("prior_recoveries") or 0)
        p["prior_months"]      = list(cf.get("prior_months") or [])
        p["prior_complimentary"] = int(cf.get("prior_complimentary") or 0)
        p["prior_complimentary_count"] = int(cf.get("prior_complimentary_count") or 0)
        p["unreconciled"] = bool(_unreconciled_sources)
        p["unreconciled_sources"] = list(_unreconciled_sources)

        # ── April & May 2026: treat as fully settled for all handlers ──
        if month in ("2026-04", "2026-05"):
            p["net_payable"] = 0
            p["prior_balance"] = 0
            p["cash_payout"] = 0
            p["carry_forward_receivable"] = 0
            # No balance is carried, so there is nothing to explain either.
            p["prior_commission"] = 0
            p["prior_salary"] = 0
            p["prior_owed"] = 0
            p["prior_paid"] = 0
            p["prior_recoveries"] = 0
            p["prior_months"] = []
            p["prior_complimentary"] = 0
            p["prior_complimentary_count"] = 0

        # Backwards-compat aliases so older client bundles keep rendering
        # something sensible until the next refresh:
        p["earnings_total"]    = owed
        p["deductions_total"]  = paid_out + recoveries
        p["net_earning"]       = (owed - recoveries - paid_out) + prior_balance
        p["expenses_total"]    = paid_out
        p["expenses_count"]    = int(exp_bucket.get("count") or 0)
        p["net_completed"]     = int(p.get("revenue_completed") or 0) - paid_out

        if _payout_excluded_handler(key) or _payout_excluded_handler(p.get("name") or ""):
            p["payout_excluded"] = True
            p["commission_total"] = 0
            p["base_commission_total"] = 0
            p["referrer_complimentary_total"] = 0
            p["admin_complimentary_total"] = 0
            p["complimentary_total"] = 0
            p["referrer_complimentary_count"] = 0
            p["admin_complimentary_count"] = 0
            p["salary_total"] = 0
            p["salary_monthly"] = 0
            p["salary_active"] = False
            p["auto_earnings_total"] = 0
            p["paid_out_total"] = 0
            p["paid_out_count"] = 0
            p["recoveries_total"] = 0
            p["recoveries_count"] = 0
            p["commission_already_received_directly"] = 0
            p["recoverable_company_share"] = 0
            p["approved_expenses_total"] = 0
            p["month_end_commission_payout"] = 0
            p["net_payable"] = 0
            p["cash_payout"] = 0
            p["carry_forward_receivable"] = 0
            p["prior_balance"] = 0
            p["prior_commission"] = 0
            p["prior_salary"] = 0
            p["prior_owed"] = 0
            p["prior_paid"] = 0
            p["prior_recoveries"] = 0
            p["prior_months"] = []
            p["prior_complimentary"] = 0
            p["prior_complimentary_count"] = 0
            p["earnings_total"] = 0
            p["deductions_total"] = 0
            p["net_earning"] = 0
            p["expenses_total"] = 0
            p["expenses_count"] = 0
            continue

        total_handler_commission += commission
        total_handler_base_commission += base_commission
        total_handler_referrer_complimentary += referrer_complimentary
        total_handler_admin_complimentary += admin_complimentary
        total_handler_salary     += salary
        total_handler_paid_out   += paid_out
        total_handler_recoveries += recoveries
        total_commission_already_received += commission_already_received
        total_company_share_recoverable += company_share_recoverable

    total_handler_auto_earnings = total_handler_commission + total_handler_salary
    # Sum of all handlers' prior balances for the global net payout
    total_prior_balance = sum(int(p.get("prior_balance") or 0) for p in perf.values() if not p.get("payout_excluded"))

    # ── April & May 2026: force global handler payout to settled ──
    if month in ("2026-04", "2026-05"):
        total_handler_auto_earnings = total_handler_paid_out

    top_tech = sorted(company_by_tech.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_performers = sorted(
        perf.values(),
        key=lambda b: (b["revenue_completed"], b["revenue_total"], b["count"]),
        reverse=True,
    )
    if scope_key:
        top_performers = [
            p for p in top_performers
            if (p.get("ref_key") or _reference_key(p.get("name") or "")) == scope_key
        ]

    result = {
        "total": total,
        "by_stage": by_stage,
        "revenue_total": revenue,
        "revenue_completed": completed_revenue,
        # Company revenue = client cash in minus commission and complimentary amounts.
        "client_collections_total": revenue,
        "referral_commission_total": referral_commission,
        "company_revenue_total": company_revenue,
        "company_revenue_completed": company_revenue_completed,
        "expected_total": expected_total,
        "pending_total": pending_total,
        "pending_count": pending_count,
        "pending_no_remark": pending_no_remark,
        "default_expected_payment":           DEFAULT_EXPECTED_PAYMENT,
        "consultancy_expected_payment":       CONSULTANCY_EXPECTED_PAYMENT,
        # Channel split — direct (₹20k baseline) vs consultancy (₹15k baseline)
        "consultancy_count":   consultancy_count,
        "consultancy_revenue": consultancy_revenue,
        "direct_count":        direct_count,
        "direct_revenue":      direct_revenue,
        # Handler-payout view (new model):
        #   owed  = 50% commissions + completed-profile complimentary amounts
        #   paid  = sum of every handler_expenses ledger row
        #   net   = owed − paid (positive = operator still owes the handler)
        "commission_pct":               HANDLER_COMMISSION_PCT,
        "handler_auto_earnings_total":  total_handler_auto_earnings,
        "handler_commission_total":     total_handler_commission,
        "handler_base_commission_total": total_handler_base_commission,
        "handler_referrer_complimentary_total": total_handler_referrer_complimentary,
        "handler_admin_complimentary_total": total_handler_admin_complimentary,
        "handler_complimentary_total": (
            total_handler_referrer_complimentary + total_handler_admin_complimentary
        ),
        "handler_salary_total":         total_handler_salary,
        "handler_paid_out_total":       total_handler_paid_out,
        "handler_recoveries_total":      total_handler_recoveries,
        "commission_already_received_directly_total": total_commission_already_received,
        "recoverable_company_share_total": total_company_share_recoverable,
        "handler_approved_expenses_total": total_handler_paid_out,
        "month_end_commission_payout":   total_handler_commission - total_handler_recoveries - total_handler_paid_out,
        "net_handler_payout":           (total_handler_auto_earnings - total_handler_recoveries - total_handler_paid_out) + total_prior_balance,
        "handler_cash_payout": max(
            0,
            (total_handler_auto_earnings - total_handler_recoveries - total_handler_paid_out)
            + total_prior_balance,
        ),
        "handler_carry_forward_receivable": max(
            0,
            -(
                (total_handler_auto_earnings - total_handler_recoveries - total_handler_paid_out)
                + total_prior_balance
            ),
        ),
        # When a required accounting store cannot be read, every payable figure
        # above is arithmetically sound and factually incomplete. Say so, so no
        # caller presents it as a settled amount owed.
        "earnings_unreconciled": bool(_unreconciled_sources),
        "earnings_unreconciled_sources": _unreconciled_sources,
        # Backwards-compat fields (older client builds expect these names).
        "handler_earnings_total":   total_handler_auto_earnings,
        "handler_deductions_total": total_handler_paid_out + total_handler_recoveries,
        "handler_expenses_total":   total_handler_paid_out,
        "net_completed":            completed_revenue - total_handler_paid_out,
        "month": month or "all",
        # Distinct months across ALL rows (so the picker doesn't change as
        # the user navigates between months).
        "available_months": available_months(all_rows),
        "top_technologies": [{"name": k, "revenue": v} for k, v in top_tech],
        "top_technologies_gross": [
            {"name": k, "revenue": v}
            for k, v in sorted(revenue_by_tech.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ],
        # Kept for backwards-compat with anything still consuming the old
        # by-count list; new UI uses `top_performers`.
        "top_references": [
            {"name": p["name"], "count": p["count"]}
            for p in sorted(perf.values(), key=lambda b: b["count"], reverse=True)[:5]
        ],
        "top_performers": top_performers,
        # Build selector counts from the same final, merged records returned
        # by /candidates.  Raw stats rows can retain an old referrer on a
        # duplicate profile, which makes a badge disagree with the table.
        "handler_references": _handler_reference_options(
            list_candidates(month="all"),
            month=None,
            scope_key=scope_key,
        ),
        "updated_at": store_data.get("updated_at"),
    }
    if _skip_pending_works:
        return result
    _pw = pending_works(reference=reference)
    return _attach_pending_work_stats(result, _pw)


def bootstrap_data(
    *,
    stage: str | None = None,
    task: str | None = None,
    search: str | None = None,
    month: str | None = None,
    pending_only: bool = False,
    reference: str | None = None,
    include_global_stats: bool = False,
) -> dict:
    """Single-pass list + stats for the Candidates page (one DB read, one enrich pass)."""
    scope_key: str | None = None
    if reference and str(reference).strip().lower() not in ("", "all"):
        scope_key = _reference_key(str(reference).strip())

    store_data = _load()
    all_rows = [_with_computed(r) for r in (store_data.get("candidates") or [])]
    scoped_rows = all_rows
    if scope_key:
        scoped_rows = [
            r for r in all_rows
            if _reference_key(r.get("reference") or "") == scope_key
        ]

    list_rows = _apply_list_filters(
        scoped_rows,
        stage=stage,
        task=task,
        search=search,
        month=month,
        pending_only=pending_only,
        reference=reference,
    )
    stats_payload = stats(
        month=month,
        reference=reference,
        _all_rows=scoped_rows,
        _skip_pending_works=True,
    )
    # Consolidate legacy profile clones before applying the active-stage filter.
    # Otherwise an old in-progress clone keeps a newly dropped candidate in the
    # Pending works banner.
    pw = _pending_works_core(
        _in_progress_rows(_collapse_profile_candidates(scoped_rows), None),
    )
    stats_payload = _attach_pending_work_stats(stats_payload, pw)

    payload: dict = {
        "candidates": [_slim_list_row(r) for r in list_rows],
        "count": len(list_rows),
        "stats": stats_payload,
    }
    if include_global_stats:
        global_stats = stats(
            month=month,
            reference=None,
            _all_rows=all_rows,
            _skip_pending_works=True,
        )
        pw_global = _pending_works_core(
            _in_progress_rows(_collapse_profile_candidates(all_rows), None),
        )
        payload["global_stats"] = _attach_pending_work_stats(global_stats, pw_global)
    return payload


# ── Payment-proof helpers ───────────────────────────────────────────────────

def _proof_dir(cid: str) -> str:
    return os.path.join(PROOFS_DIR, cid)


def _attachment_dir(cid: str, attachment_type: AttachmentType) -> str:
    return os.path.join(PROOFS_DIR, cid, attachment_type.value)


def _ext_from_mime(mime: str, fallback_name: str = "") -> str:
    mime = (mime or "").lower().split(";")[0].strip()
    if mime in _ALLOWED_MIME:
        return _ALLOWED_MIME[mime]
    # Last-resort guess from the filename extension.
    if fallback_name and "." in fallback_name:
        ext = fallback_name.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif"}:
            return "jpeg" if ext == "jpeg" else ext
    return ""


def _store_typed_attachment(
              cid: str, *, attachment_type: AttachmentType | str,
              data: bytes, original_name: str, mime_type: str,
              note: str = "", metadata: dict | None = None) -> dict | None:
    """Persist one explicitly typed candidate attachment."""
    kind = parse_attachment_type(attachment_type)
    if not data:
        raise ValueError("Empty upload")
    if len(data) > MAX_PROOF_BYTES:
        raise ValueError(f"File too large (max {MAX_PROOF_BYTES // (1024*1024)} MB)")
    ext = _ext_from_mime(mime_type, original_name)
    if not ext:
        raise ValueError("Only image files (jpg / png / webp / gif / heic) are allowed")

    # Refuse an unknown candidate before writing a file that nothing would own.
    # The row is read again at write time, so this is only an early exit.
    if not any(
        r.get("id") == cid for r in (_load().get("candidates") or [])
    ):
        return None

    pid = uuid.uuid4().hex[:12]
    filename = f"{pid}.{ext}"
    folder = _attachment_dir(cid, kind)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    # Write atomically so we never serve a half-flushed screenshot.
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    entry = {
        "id":            pid,
        "filename":      filename,
        "original_name": (original_name or filename)[:160],
        "mime_type":     mime_type or f"image/{ext}",
        "size":          len(data),
        "note":          _clean_str(note)[:200],
        "uploaded_at":   _now_iso(),
        "attachment_type": kind.value,
        "url":           f"/candidates/{cid}/attachments/{kind.value}/{pid}",
    }
    if metadata:
        for key in (
            "sha256", "utr_number", "transaction_id", "payment_status",
            "fraud_decision", "fraud_reasons", "fraud_warnings", "fraud_checked_at",
            "company_payment_verified", "receiver_name", "receiver_upi_id",
            "receiver_phone", "verified_amount", "receiver_account",
            "vision_amount", "ocr_amount", "amount_mismatch_reason",
            "receiver_type", "ledger_entry_id", "ledger_action",
            "ledger_status", "source_module", "booking_eligible",
            "verification_state", "payment_id", "evidence_id",
            "entitlement_id", "payment_scope",
            "booking_id", "source_endpoint", "upload_context",
            "pending_proof_id",
        ):
            if key in metadata:
                entry[key] = metadata[key]
    field = ATTACHMENT_FIELDS[kind]

    def _attach(row: dict) -> dict:
        if kind == AttachmentType.PROFILE_PHOTO:
            value = entry
        else:
            value = list(row.get(field) or []) + [entry]
        return {field: value, "attachment_schema_version": 2}

    # Storing an attachment used to rewrite every row in the store from one
    # in-memory snapshot. On the booking path that snapshot could predate the
    # slot being booked, so saving the evidence cleared the very booking it was
    # evidence for. The write is now confined to this row and these keys.
    if _patch_row_fields(cid, _attach) is None:
        return None
    if kind == AttachmentType.PAYMENT_PROOF:
        # Mirror payment evidence into the managed store so both upload paths
        # share one durable, checksum-verified home for financial evidence.
        try:
            from features import payment_evidence_store
            payment_evidence_store.store(
                data,
                mime_type=mime_type,
                original_filename=original_name,
                candidate_id=cid,
                proof_id=str(entry.get("id") or ""),
                upload_source="candidate_payment_proof",
                transaction_reference=str(entry.get("utr_number") or ""),
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not mirror candidate payment evidence"
            )
        recalculate_received_total(
            cid,
            trigger="proof_added",
            proof_change="added",
            proof_id=str(entry.get("id") or ""),
            reason=f"Payment proof {entry.get('id')} uploaded.",
        )
    return entry


def correct_proof_amount(
    cid: str,
    pid: str,
    *,
    corrected_amount: int,
    reason: str,
    reviewer: str,
    extractor_version: str = "",
) -> dict | None:
    """Replace a proof's extracted amount, keeping the superseded one on record.

    The old reading is appended to the proof rather than overwritten, so the
    evidence trail shows what was believed, what it became, and why. Re-running
    with the same amount is a no-op, so a repeated repair cannot inflate a
    total or stack history entries.
    """
    corrected_amount = int(corrected_amount)
    if corrected_amount <= 0:
        raise ValueError("A corrected amount must be positive")
    cdata = _load()
    rows = cdata.get("candidates") or []
    idx = next((i for i, r in enumerate(rows) if r.get("id") == cid), None)
    if idx is None:
        return None
    # Uploads that predate typed attachments still sit in the legacy `proofs`
    # list. `partition_candidate_attachments` surfaces both, so a repair that
    # only searched `payment_proofs` would silently miss exactly the historical
    # records most likely to need correcting.
    field = "payment_proofs"
    proofs = list(rows[idx].get("payment_proofs") or [])
    position = next((i for i, p in enumerate(proofs) if str(p.get("id")) == pid), None)
    if position is None:
        legacy = list(rows[idx].get("proofs") or [])
        position = next(
            (i for i, p in enumerate(legacy) if str(p.get("id")) == pid), None
        )
        if position is None:
            return None
        field, proofs = "proofs", legacy
    proof = dict(proofs[position])
    previous = int(proof.get("verified_amount") or 0)
    if previous == corrected_amount:
        return dict(proof)
    history = list(proof.get("amount_corrections") or [])
    history.append({
        "corrected_at": _now_iso(),
        "previous_amount": previous,
        "new_amount": corrected_amount,
        "previous_verification_state": proof.get("verification_state"),
        "reviewer": reviewer,
        "reason": reason,
        "extractor_version": extractor_version,
    })
    proof["amount_corrections"] = history
    proof["verified_amount"] = corrected_amount
    proof["amount_source"] = "literal_text_correction"
    proofs[position] = proof
    rows[idx][field] = proofs
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    return dict(proof)


def clear_false_duplicate(
    cid: str, pid: str, *, reason: str, reviewer: str
) -> dict | None:
    """Release a proof wrongly flagged as a duplicate of another candidate's.

    A screenshot first uploaded against the wrong profile used to stay latched
    to that profile even after the attempt there was rejected and the profile
    deleted, leaving the candidate who actually paid stuck at zero. The engine
    no longer creates that state, but rows already carrying it need releasing.

    The claim is checked rather than taken on trust: the release happens only
    when no payment anywhere in the ledger actually credited this transaction to
    someone else. A real double-spend still refuses.
    """
    from features.payment_verification_engine import (
        _credited_payment,
        _load_ledger,
        _norm_text,
    )

    if not str(reason or "").strip() or not str(reviewer or "").strip():
        raise ValueError("Clearing a duplicate needs both a reviewer and a reason")

    cdata = _load()
    rows = cdata.get("candidates") or []
    idx, field, located = _locate_proof(rows, cid, pid)
    if idx is None or located is None:
        return None
    proofs, position = located
    proof = dict(proofs[position])
    if str(proof.get("verification_state") or "").upper() != "DUPLICATE_PAYMENT":
        return dict(proof)

    references = {
        _norm_text(proof.get(key)).replace(" ", "")
        for key in ("utr_number", "transaction_id", "reference_number")
        if _norm_text(proof.get(key)).replace(" ", "")
    }
    for payment in _load_ledger().get("payments") or []:
        if not _credited_payment(payment):
            continue
        if str(payment.get("source_entity_id") or "") == str(cid):
            continue
        held = {
            str(value or "")
            for value in (payment.get("transaction_references") or {}).values()
        }
        if references & held:
            raise ValueError(
                f"Proof {pid} really is a duplicate: payment "
                f"{payment.get('payment_id')} already credited this transaction "
                f"to {payment.get('source_entity_id')}"
            )

    history = list(proof.get("duplicate_releases") or [])
    history.append({
        "released_at": _now_iso(),
        "previous_verification_state": "DUPLICATE_PAYMENT",
        # What the fixed engine produces for this evidence: the receiver
        # question is still open, so it credits nothing until answered.
        "new_verification_state": "INCOMPLETE_PAYMENT_EVIDENCE",
        "checked_references": sorted(references),
        "reviewer": reviewer,
        "reason": reason,
    })
    proof["duplicate_releases"] = history
    proof["verification_state"] = "INCOMPLETE_PAYMENT_EVIDENCE"
    proof["ledger_status"] = "unposted"
    proofs[position] = proof
    rows[idx][field] = proofs
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    return dict(proof)


CONFIRMABLE_RECEIVER_TYPES = {
    "company": "VERIFIED_COMPANY_PAYMENT",
    "referrer": "VERIFIED_REFERRER_PAYMENT",
}

# Only a proof the engine declined for want of a stable receiver identifier may
# be confirmed this way. Anything rejected, failed, duplicated or unreadable was
# refused for a different reason, and a confirmation of *who* was paid says
# nothing about those.
RECEIVER_CONFIRMABLE_STATES = {
    "INCOMPLETE_PAYMENT_EVIDENCE",
    "UNKNOWN_RECEIVER",
    "PENDING_MANUAL_REVIEW",
}


def confirm_proof_receiver(
    cid: str,
    pid: str,
    *,
    receiver_type: str,
    receiver_name: str,
    reason: str,
    reviewer: str,
) -> dict | None:
    """Record an out-of-band confirmation of who received one payment.

    Payment apps redact the payee handle — PhonePe prints it as
    ``XXXXXX4573@ybl`` — so verification has no stable identifier to match and
    correctly refuses to credit on a name alone. Someone who owns the receiving
    account can settle that question; nothing else can.

    The confirmation is deliberately scoped to this one proof and is never
    written to the receiver registry, so it cannot make some future masked
    handle verify by itself. It does not touch the extracted amount either: it
    answers only who was paid, and the screenshot still says how much.
    """
    receiver_type = str(receiver_type or "").strip().lower()
    if receiver_type not in CONFIRMABLE_RECEIVER_TYPES:
        raise ValueError(
            f"receiver_type must be one of {sorted(CONFIRMABLE_RECEIVER_TYPES)}"
        )
    if not str(reason or "").strip() or not str(reviewer or "").strip():
        raise ValueError("A receiver confirmation needs both a reviewer and a reason")

    cdata = _load()
    rows = cdata.get("candidates") or []
    idx, field, located = _locate_proof(rows, cid, pid)
    if idx is None or located is None:
        return None
    proofs, position = located
    proof = dict(proofs[position])

    previous_state = str(proof.get("verification_state") or "").strip().upper()
    confirmed_state = CONFIRMABLE_RECEIVER_TYPES[receiver_type]
    if previous_state == confirmed_state:
        return dict(proof)
    if previous_state not in RECEIVER_CONFIRMABLE_STATES:
        raise ValueError(
            f"Proof {pid} is in {previous_state or 'no state'}, which was not a "
            "receiver question; re-adjudicate it before confirming a receiver"
        )
    if payment_receipts.file_availability(proof) in (
        payment_receipts.FILE_STATES_BLOCKING_VERIFICATION
    ):
        raise ValueError(
            f"Proof {pid} cannot be re-read, so its amount cannot be relied on"
        )

    history = list(proof.get("receiver_confirmations") or [])
    history.append({
        "confirmed_at": _now_iso(),
        "previous_verification_state": previous_state,
        "new_verification_state": confirmed_state,
        "receiver_type": receiver_type,
        "receiver_name": receiver_name,
        "reviewer": reviewer,
        "reason": reason,
        "is_original_system_capture": False,
    })
    proof["receiver_confirmations"] = history
    proof["verification_state"] = confirmed_state
    proof["receiver_type"] = receiver_type
    proof["receiver_confirmed_by"] = reviewer
    proof["receiver_match"] = "administrator_confirmation"
    proof["company_payment_verified"] = receiver_type == "company"
    proofs[position] = proof
    rows[idx][field] = proofs
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    return dict(proof)


def _locate_proof(rows: list, cid: str, pid: str):
    """Find a proof in whichever list holds it, typed or legacy."""
    idx = next((i for i, r in enumerate(rows) if r.get("id") == cid), None)
    if idx is None:
        return None, None, None
    for field in ("payment_proofs", "proofs"):
        proofs = list(rows[idx].get(field) or [])
        position = next(
            (i for i, p in enumerate(proofs) if str(p.get("id")) == pid), None
        )
        if position is not None:
            return idx, field, (proofs, position)
    return idx, None, None


def set_proof_file_availability(
    cid: str, pid: str, file_state: str, reason: str, reviewer: str
) -> dict | None:
    """Record what happened to a proof's file, leaving its amount alone.

    A file going missing or being archived says nothing about whether the
    payment occurred, so this never touches the verified amount.
    """
    cdata = _load()
    rows = cdata.get("candidates") or []
    idx, field, found = _locate_proof(rows, cid, pid)
    if not found:
        return None
    proofs, position = found
    proof = dict(proofs[position])
    previous = proof.get("file_availability") or "AVAILABLE"
    if previous == file_state:
        return dict(proof)
    proof.setdefault("file_availability_history", []).append({
        "recorded_at": _now_iso(), "previous": previous, "new": file_state,
        "reviewer": reviewer, "reason": reason,
    })
    proof["file_availability"] = file_state
    proofs[position] = proof
    rows[idx][field] = proofs
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    return dict(proof)


def apply_replacement_proof(
    cid: str, pid: str, replacement: dict, reason: str, reviewer: str
) -> dict | None:
    """Point an existing proof record at freshly uploaded evidence.

    The proof keeps its id so everything referring to it still resolves, and the
    superseded capture is recorded rather than discarded — the fact that the
    original was lost is itself part of the audit trail.
    """
    cdata = _load()
    rows = cdata.get("candidates") or []
    idx, field, found = _locate_proof(rows, cid, pid)
    if not found:
        return None
    proofs, position = found
    proof = dict(proofs[position])
    proof.setdefault("replacement_history", []).append({
        "replaced_at": _now_iso(),
        "previous_checksum": proof.get("sha256"),
        "previous_filename": proof.get("original_name"),
        "previous_verified_amount": proof.get("verified_amount"),
        "previous_verification_state": proof.get("verification_state"),
        "new_checksum": replacement.get("sha256"),
        "reviewer": reviewer, "reason": reason,
    })
    for key, value in replacement.items():
        if value not in (None, ""):
            proof[key] = value
    proof["replaced_at"] = _now_iso()
    proofs[position] = proof
    rows[idx][field] = proofs
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    return dict(proof)


def recalculate_received_total(
    cid: str,
    *,
    trigger: str,
    reason: str,
    reviewer: str = "system",
    proof_change: str = "",
    proof_id: str = "",
) -> dict | None:
    """Re-derive one candidate's received total from their payment proofs.

    Called whenever proof evidence changes — added, replaced, rejected or
    deleted — so the stored figure never drifts from the evidence. Rows with no
    proof at all are left alone: their recorded amount is the only record of a
    payment made before proofs were captured.
    """
    cdata = _load()
    rows = cdata.get("candidates") or []
    idx = next((i for i, r in enumerate(rows) if r.get("id") == cid), None)
    if idx is None:
        return None
    row = rows[idx]
    proofs = partition_candidate_attachments(row)["payment_proofs"]
    if not payment_receipts.has_proof_evidence(proofs):
        return None
    previous = int(row.get("payment") or 0)
    new_total = payment_receipts.verified_proof_total(proofs)
    already_controlled = _coerce_bool(row.get("payment_proof_controlled"))
    if new_total < previous and not already_controlled:
        # Proofs total less than what is recorded. On a row that was never under
        # proof control this almost always means the earlier payments were made
        # before proofs were captured, so reducing the total here would delete
        # real money. Surface it for reconciliation instead.
        return None
    if new_total == previous:
        rows[idx]["payment_proof_controlled"] = True
        cdata["candidates"] = rows
        _save(cdata)
        return None
    unique = payment_receipts.unique_verified_proofs(proofs)
    rows[idx]["payment"] = new_total
    rows[idx]["payment_proof_controlled"] = True
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    from features import payment_recalculation_audit
    payment_recalculation_audit.record_recalculation(
        candidate_id=cid,
        candidate_name=str(row.get("name") or ""),
        previous_total=previous,
        new_total=new_total,
        trigger=trigger,
        proof_change=proof_change,
        proof_id=proof_id,
        reviewer=reviewer,
        reason=reason,
        proof_ids=[str(p.get("id") or "") for p in unique],
        utr=", ".join(
            str(p.get("utr_number") or "") for p in unique if p.get("utr_number")
        ),
        verified_amount=new_total,
    )
    return get_candidate(cid)


def add_proof(cid: str, *, attachment_type: AttachmentType | str | None = None,
              data: bytes, original_name: str, mime_type: str,
              note: str = "", metadata: dict | None = None) -> dict | None:
    """Deprecated compatibility path. A valid explicit type is mandatory."""
    return _store_typed_attachment(
        cid,
        attachment_type=parse_attachment_type(attachment_type),
        data=data,
        original_name=original_name,
        mime_type=mime_type,
        note=note,
        metadata=metadata,
    )


def add_payment_proof(cid: str, **kwargs) -> dict | None:
    return _store_typed_attachment(
        cid, attachment_type=AttachmentType.PAYMENT_PROOF, **kwargs
    )


def add_slot_screenshot_proof(cid: str, **kwargs) -> dict | None:
    return _store_typed_attachment(
        cid, attachment_type=AttachmentType.SLOT_SCREENSHOT_PROOF, **kwargs
    )


def set_profile_photo(cid: str, **kwargs) -> dict | None:
    return _store_typed_attachment(
        cid, attachment_type=AttachmentType.PROFILE_PHOTO, **kwargs
    )


def list_attachments(cid: str, attachment_type: AttachmentType | str) -> list[dict] | None:
    kind = parse_attachment_type(attachment_type)
    for r in _load().get("candidates") or []:
        if r.get("id") == cid:
            value = partition_candidate_attachments(r)[ATTACHMENT_FIELDS[kind]]
            if kind == AttachmentType.PROFILE_PHOTO:
                return [value] if value else []
            return list(value or [])
    return None


def list_proofs(cid: str) -> list[dict] | None:
    return list_attachments(cid, AttachmentType.PAYMENT_PROOF)


def get_attachment(cid: str, pid: str,
                   attachment_type: AttachmentType | str) -> tuple[str, dict] | None:
    """Locate the proof's on-disk path + metadata for serving. Returns
    (absolute_path, entry) or None when either id doesn't resolve."""
    kind = parse_attachment_type(attachment_type)
    for r in _load().get("candidates") or []:
        if r.get("id") != cid:
            continue
        for p in list_attachments(cid, kind) or []:
            if p.get("id") == pid:
                folder = _proof_dir(cid) if p.get("legacy_storage") else _attachment_dir(cid, kind)
                path = os.path.join(folder, p["filename"])
                if not os.path.exists(path):
                    return None
                return path, dict(p)
        return None
    return None


def get_proof(cid: str, pid: str) -> tuple[str, dict] | None:
    return get_attachment(cid, pid, AttachmentType.PAYMENT_PROOF)


def delete_proof(cid: str, pid: str) -> bool:
    """Remove a proof from the candidate + delete its file from disk.
    Also searches slot-clone rows with the same name in case proof was merged from another row."""
    cdata = _load()
    rows = cdata.get("candidates") or []
    # First try the exact row
    target_row = next((r for r in rows if r.get("id") == cid), None)
    if target_row:
        proofs = list(target_row.get("payment_proofs") or [])
        for i, p in enumerate(proofs):
            if p.get("id") == pid:
                path = os.path.join(_attachment_dir(cid, AttachmentType.PAYMENT_PROOF), p["filename"])
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
                proofs.pop(i)
                target_row["payment_proofs"] = proofs
                target_row["updated_at"] = _now_iso()
                cdata["candidates"] = rows
                _save(cdata)
                recalculate_received_total(
                    cid,
                    trigger="proof_deleted",
                    proof_change="deleted",
                    proof_id=pid,
                    reason=f"Payment proof {pid} deleted.",
                )
                return True
    # If not found on the target row, search all rows with the same name (slot clones)
    if target_row:
        name_key = _normalise_candidate_name_key(target_row.get("name") or "")
        for r in rows:
            if r.get("id") == cid:
                continue
            if _normalise_candidate_name_key(r.get("name") or "") != name_key:
                continue
            proofs = list(r.get("payment_proofs") or [])
            for i, p in enumerate(proofs):
                if p.get("id") == pid:
                    path = os.path.join(_attachment_dir(r["id"], AttachmentType.PAYMENT_PROOF), p["filename"])
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except OSError:
                        pass
                    proofs.pop(i)
                    r["payment_proofs"] = proofs
                    r["updated_at"] = _now_iso()
                    cdata["candidates"] = rows
                    _save(cdata)
                    recalculate_received_total(
                        str(r["id"]),
                        trigger="proof_deleted",
                        proof_change="deleted",
                        proof_id=pid,
                        reason=f"Payment proof {pid} deleted from slot-clone row.",
                    )
                    return True
    return False


def update_proof_note(cid: str, pid: str, note: str) -> dict | None:
    """Operator can tag a proof after upload (e.g. '₹10k UPI · 26 May')."""
    cdata = _load()
    rows = cdata.get("candidates") or []
    for r in rows:
        if r.get("id") != cid:
            continue
        for p in (r.get("payment_proofs") or []):
            if p.get("id") == pid:
                p["note"] = _clean_str(note)[:200]
                r["updated_at"] = _now_iso()
                cdata["candidates"] = rows
                _save(cdata)
                return dict(p)
        return None
    return None


# ── Resume helpers ────────────────────────────────────────────────────────────

def _resume_dir(cid: str) -> str:
    return os.path.join(RESUMES_DIR, cid)


def reconcile_resume_metadata() -> int:
    """Restore metadata for resume files that survived an incomplete restore.

    Earlier deployments preserved the documents in ``candidates_resumes`` but
    dropped their JSON records.  Recreate a minimal version record only when a
    folder belongs to an existing candidate; unknown folders are left untouched
    rather than risk assigning a document to the wrong person.
    """
    if not os.path.isdir(RESUMES_DIR):
        return 0
    data = _load()
    changed = 0
    for row in data.get("candidates") or []:
        cid = _clean_str(row.get("id"))
        if not cid:
            continue
        folder = _resume_dir(cid)
        if not os.path.isdir(folder):
            continue
        entries = list(row.get("resumes") or [])
        known = {_clean_str(item.get("filename")) for item in entries}
        row_changed = False
        for filename in sorted(os.listdir(folder)):
            path = os.path.join(folder, filename)
            if not os.path.isfile(path) or filename in known:
                continue
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            mime = {
                "pdf": "application/pdf",
                "doc": "application/msword",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "txt": "text/plain",
            }.get(ext)
            if not mime:
                continue
            entries.append({
                "id": filename.rsplit(".", 1)[0][:32],
                "filename": filename,
                "original_name": filename,
                "mime_type": mime,
                "size": os.path.getsize(path),
                "note": "",
                "uploaded_at": datetime.fromtimestamp(
                    os.path.getmtime(path), timezone.utc
                ).isoformat(),
                "url": f"/candidates/{cid}/resumes/{filename.rsplit('.', 1)[0][:32]}",
            })
            known.add(filename)
            changed += 1
            row_changed = True
        if row_changed:
            row["resumes"] = entries
    if changed:
        _save(data)
    return changed


def _resume_storage_candidate_id(candidate_id: str, entry: dict) -> str:
    """Find the folder that actually owns a stored resume file.

    Profile de-duplication can give a candidate a new visible ID while their
    older resume record keeps its original URL.  Keep that legacy folder link
    intact so existing files remain viewable instead of looking "missing".
    """
    stored = _clean_str(entry.get("storage_candidate_id"))
    if stored:
        return stored
    match = re.search(r"/candidates/([^/]+)/resumes/", _clean_str(entry.get("url")))
    return match.group(1) if match else candidate_id


def _ext_from_resume_mime(mime: str, fallback_name: str = "") -> str:
    mime = (mime or "").lower().split(";")[0].strip()
    if mime in _ALLOWED_RESUME_MIME:
        return _ALLOWED_RESUME_MIME[mime]
    if fallback_name and "." in fallback_name:
        ext = fallback_name.rsplit(".", 1)[-1].lower()
        if ext in {"pdf", "doc", "docx", "txt"}:
            return ext
    return ""


def add_resume(cid: str, *, data: bytes, original_name: str, mime_type: str,
               note: str = "") -> dict | None:
    """Persist an updated resume for `cid`. Each upload is kept as a version."""
    if not data:
        raise ValueError("Empty upload")
    if len(data) > MAX_RESUME_BYTES:
        raise ValueError(f"File too large (max {MAX_RESUME_BYTES // (1024*1024)} MB)")
    ext = _ext_from_resume_mime(mime_type, original_name)
    if not ext:
        raise ValueError("Only PDF, Word (.doc/.docx), or plain text files are allowed")

    cdata = _load()
    rows = cdata.get("candidates") or []
    idx = next((i for i, r in enumerate(rows) if r.get("id") == cid), -1)
    if idx < 0:
        return None

    existing = list(rows[idx].get("resumes") or [])
    digest = hashlib.sha256(data).hexdigest()

    # Re-uploading the same file is not a new version of anything. One
    # candidate accumulated eight byte-identical copies over eighty minutes
    # because every attempt appended a row.
    for item in existing:
        if item.get("sha256") == digest:
            return dict(item)

    rid = uuid.uuid4().hex[:12]
    filename = f"{rid}.{ext}"
    folder = _resume_dir(cid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    entry = {
        "id":            rid,
        "filename":      filename,
        "original_name": (original_name or filename)[:160],
        "mime_type":     mime_type or "application/octet-stream",
        "size":          len(data),
        "sha256":        digest,
        "note":          _clean_str(note)[:200],
        "uploaded_at":   _now_iso(),
        "url":           f"/candidates/{cid}/resumes/{rid}",
    }
    # A candidate has one current resume. A newer one supersedes the last
    # rather than joining a pile nobody can pick the right file out of; the
    # superseded file is removed so the list cannot drift back into a stack.
    for item in existing:
        superseded = os.path.join(
            _resume_dir(_resume_storage_candidate_id(cid, item)),
            str(item.get("filename") or ""),
        )
        try:
            if item.get("filename") and os.path.exists(superseded):
                os.remove(superseded)
        except OSError:
            # A file we cannot delete must not stop the new resume landing.
            pass
    rows[idx]["resumes"] = [entry]
    rows[idx]["updated_at"] = _now_iso()
    cdata["candidates"] = rows
    _save(cdata)
    return entry


def _resume_owner_rows(cid: str) -> list[dict]:
    """Every row that can legitimately hold this candidate's resumes.

    One person often has several rows — a new one is cloned for each interview
    slot — and the resume dialog lists all of their files together. A link
    opened from that list therefore names whichever row was on screen, which
    is frequently not the row the file was uploaded against.
    """
    rows = _load().get("candidates") or []
    try:
        identity = {str(value) for value in candidate_identity_ids(cid) if value}
    except Exception:
        identity = {str(cid)}
    identity.add(str(cid))

    row = next((r for r in rows if str(r.get("id")) == str(cid)), None)
    phone_key = candidate_phone_identity(row.get("phone")) if row else ""
    name_key = _normalise_candidate_name_key(row.get("name") or "") if row else ""

    owners = []
    for candidate in rows:
        if str(candidate.get("id")) in identity:
            owners.append(candidate)
            continue
        # Same person, different row: match the identity the candidate list
        # itself uses to collapse duplicates.
        if phone_key and candidate_phone_identity(candidate.get("phone")) == phone_key:
            owners.append(candidate)
        elif name_key and _normalise_candidate_name_key(candidate.get("name") or "") == name_key:
            owners.append(candidate)
    return owners


def find_resume(cid: str, rid: str) -> tuple[str, dict, str] | None:
    """Locate a resume by its immutable id. Returns (path, entry, owner_id).

    The resume id is the stable handle; the candidate id in a URL only says
    which row the reader was looking at. Resolving on the id alone is what
    stops a preview answering "Resume not found" for a file that is present.
    """
    for row in _resume_owner_rows(cid):
        for item in (row.get("resumes") or []):
            if item.get("id") != rid:
                continue
            owner = str(row.get("id") or cid)
            storage_cid = _resume_storage_candidate_id(owner, item)
            path = os.path.join(_resume_dir(storage_cid), str(item.get("filename") or ""))
            if not item.get("filename") or not os.path.exists(path):
                return None
            return path, dict(item), owner
    return None


def get_resume(cid: str, rid: str) -> tuple[str, dict] | None:
    found = find_resume(cid, rid)
    return (found[0], found[1]) if found else None


def delete_resume(cid: str, rid: str) -> bool:
    cdata = _load()
    rows = cdata.get("candidates") or []
    owner_ids = {str(row.get("id")) for row in _resume_owner_rows(cid)}
    for r in rows:
        if str(r.get("id")) not in owner_ids:
            continue
        resumes = list(r.get("resumes") or [])
        for i, item in enumerate(resumes):
            if item.get("id") == rid:
                # The row holding the entry owns the file, not the row the
                # request came in on.
                storage_cid = _resume_storage_candidate_id(str(r.get("id") or cid), item)
                path = os.path.join(_resume_dir(storage_cid), item["filename"])
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
                resumes.pop(i)
                r["resumes"] = resumes
                r["updated_at"] = _now_iso()
                cdata["candidates"] = rows
                _save(cdata)
                return True
        # Keep looking: the person's other rows may hold it.
    return False


def update_resume_note(cid: str, rid: str, note: str) -> dict | None:
    cdata = _load()
    rows = cdata.get("candidates") or []
    # Renaming touches the display note only. The stored filename and the
    # folder it lives in are never derived from what the reader typed, so a
    # rename cannot detach a record from its file.
    owner_ids = {str(row.get("id")) for row in _resume_owner_rows(cid)}
    for r in rows:
        if str(r.get("id")) not in owner_ids:
            continue
        for item in (r.get("resumes") or []):
            if item.get("id") == rid:
                item["note"] = _clean_str(note)[:200]
                r["updated_at"] = _now_iso()
                cdata["candidates"] = rows
                _save(cdata)
                return dict(item)
    return None


def bulk_replace(rows: list[dict]) -> int:
    """Replace the entire list (used by the one-shot seed importer).
    Returns the count written."""
    cleaned = [_normalise(r) for r in rows if (r.get("name") or "").strip()]
    _save({"candidates": cleaned, "updated_at": _now_iso()})
    return len(cleaned)


def bulk_upsert(rows: list[dict]) -> dict:
    """Append rows, dedup by (name, phone, date) to avoid double-imports.
    Returns counts of added / skipped."""
    data = _load()
    existing = data.get("candidates") or []
    existing_keys = {
        ((r.get("name") or "").lower(), (r.get("phone") or ""), (r.get("date") or ""))
        for r in existing
    }
    added = 0
    skipped = 0
    for raw in rows:
        if not (raw.get("name") or "").strip():
            skipped += 1
            continue
        row = _normalise(raw)
        key = (row["name"].lower(), row["phone"], row["date"])
        if key in existing_keys:
            skipped += 1
            continue
        existing.append(row)
        existing_keys.add(key)
        added += 1
    data["candidates"] = existing
    _save(data)
    return {"added": added, "skipped": skipped, "total": len(existing)}
