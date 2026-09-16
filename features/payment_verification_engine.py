"""Central payment verification, receiver classification, and audit ledger.

All screenshot entry points call this module.  Ollama Vision extracts facts;
only deterministic registry and amount/status rules decide the result.  OCR is
retained elsewhere for a future flag, but is deliberately disabled here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from threading import RLock
from typing import Any

from core.config import DATA_DIR
from features import transaction_identity

SUCCESS_STATUSES = {"success", "successful", "completed", "complete", "paid"}
FAILED_STATUSES = {"failed", "failure", "declined", "rejected", "reversed"}
VERIFICATION_STATES = {
    "UPLOADED",
    "EXTRACTION_IN_PROGRESS",
    "EXTRACTED",
    "VERIFIED_COMPANY_PAYMENT",
    "VERIFIED_REFERRER_PAYMENT",
    "PENDING_MANUAL_REVIEW",
    "INCOMPLETE_PAYMENT_EVIDENCE",
    "UNKNOWN_RECEIVER",
    "DUPLICATE_PAYMENT",
    "EXTRACTION_FAILED",
    "FAILED_PAYMENT",
    "REJECTED",
    "REVERSED",
    # A recorded amount that cannot be trusted until a human confirms it against
    # the image — used when extraction is known to have misread the figure.
    "AMOUNT_EXTRACTION_REVIEW_REQUIRED",
}

# States in which a stored payment put no money against any entity. Only a
# payment that actually credited someone can be double-counted, so only those
# may block the same evidence from being verified elsewhere.
NON_CREDITING_VERIFICATION_STATES = frozenset(
    {
        "UPLOADED",
        "EXTRACTION_IN_PROGRESS",
        "EXTRACTED",
        "PENDING_MANUAL_REVIEW",
        "INCOMPLETE_PAYMENT_EVIDENCE",
        "UNKNOWN_RECEIVER",
        "DUPLICATE_PAYMENT",
        "EXTRACTION_FAILED",
        "FAILED_PAYMENT",
        "REJECTED",
        "REVERSED",
        "AMOUNT_EXTRACTION_REVIEW_REQUIRED",
    }
)

# Whether the original evidence file is still there to be re-read. Kept apart
# from the verification verdict: a payment can be genuinely verified while its
# screenshot has since been lost, and that combination has to be visible rather
# than collapsing into either state alone.
FILE_AVAILABLE = "AVAILABLE"
FILE_MISSING = "MISSING_FILE"
FILE_CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
FILE_UNREADABLE = "UNREADABLE"
FILE_ARCHIVED = "ARCHIVED"

FILE_AVAILABILITY_STATES = {
    FILE_AVAILABLE,
    FILE_MISSING,
    FILE_CHECKSUM_MISMATCH,
    FILE_UNREADABLE,
    FILE_ARCHIVED,
}

# Anything but AVAILABLE or ARCHIVED means the evidence cannot be re-read, so
# nothing may verify automatically from it.
FILE_STATES_BLOCKING_VERIFICATION = {
    FILE_MISSING,
    FILE_CHECKSUM_MISMATCH,
    FILE_UNREADABLE,
}
SETTLEMENT_STATES = {
    "NOT_APPLICABLE",
    "PENDING",
    "PARTIALLY_SETTLED",
    "SETTLED",
    "REVERSED",
    "DISPUTED",
}
PAYMENT_PURPOSES = {
    "CANDIDATE_FEE_RECEIVED_BY_COMPANY",
    "CANDIDATE_FEE_RECEIVED_BY_REFERRER",
    "COMMISSION_PAYOUT",
    "RECOVERABLE_ADVANCE",
    "APPROVED_EXPENSE_REIMBURSEMENT",
    "PAYOUT_ADJUSTMENT",
    "REFUND",
    "REVERSAL",
}
ALLOWED_PAYMENT_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
    "image/heif",
}
_lock = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _norm_upi(value: Any) -> str:
    return "".join(str(value or "").lower().split())


def _valid_upi(value: Any) -> bool:
    normalized = _norm_upi(value)
    return bool(re.fullmatch(r"[a-z0-9._-]{2,}@[a-z][a-z0-9.-]{1,}", normalized))


# A run of any of these is a redaction, not a handle. The period is here
# because that is what actually arrives: Google Pay draws the payee as
# "••••1111@ybl", and the extractor transcribes those bullets as full stops, so
# production saw ".....1111@ybl". Without the period in this class the string was
# not recognised as masked -- and since dots are legal inside a real VPA local
# part, `_valid_upi` then accepted it as a genuine handle that matched no
# registered account, which is how a payment to a registered account was
# reported as a receiver-identity conflict.
#
# Three in a row is the threshold on purpose: single dots are ordinary in a real
# handle (john.doe@okhdfcbank), runs of them are not.
_MASK_RUN_RE = re.compile(r"[x*#•·.]{3,}")


def _is_masked_identifier(value: Any) -> bool:
    """True when the payment app redacted the payee handle instead of showing it.

    PhonePe and GPay render the payee VPA as ``XXXXXX4573@ybl``. That mask is a
    placeholder for an account, not an account: it matches no registry entry and
    every masked handle from the same bank looks alike. Treating it as a real
    identifier both suppressed the name fallback and raised a false
    ``receiver_identifier_conflict``, so a genuine company payment resolved to
    an unknown receiver.
    """
    local = _norm_upi(value).split("@", 1)[0]
    return bool(local) and bool(_MASK_RUN_RE.search(local))


# Minimum unmasked characters a masked handle must show before it can be matched
# against the registry. PhonePe leaves four; fewer is not enough to distinguish
# two accounts at the same provider.
_MASKED_VISIBLE_MIN = 4


def _masked_record_alias_match(masked: str, record: dict[str, Any]) -> str:
    """The verified identifier of this payee that a masked handle denotes.

    One payee, one identity, several accounts. The company receives at
    `raviarvind1111@ybl`, at the PhonePe handle derived from its registered
    phone `8639074573`, and at the CRED handle on the same number -- all the
    same person and the same money. A rule that could only resolve a mask
    against a same-provider UPI refused two of those three, and the receipts it
    refused were genuine.

    So the digits a mask leaves visible are compared against every verified
    identifier the record holds: a registered UPI's local part at the same
    provider, a registered phone, or a registered account number. A phone or an
    account is provider-independent by nature -- a handle derived from a number
    can exist at any PSP -- while a UPI local part is only evidence at its own
    provider.

    This never trusts a mask on its own: the caller pairs it with a receiver
    name that matches the same record, so a mask is read as this payee's account
    only when name and digits both agree. Digits that agree with nothing this
    payee holds still match nothing, which is what keeps `…9999@yescred` out.
    """
    local, _, domain = str(masked or "").partition("@")
    if not domain:
        return ""
    visible = local.lstrip("Xx*#•·. ").strip()
    if len(visible) < _MASKED_VISIBLE_MIN:
        return ""
    for registered in record.get("upi_ids") or ():
        reg_local, _, reg_domain = str(registered or "").partition("@")
        if reg_domain == domain and reg_local.endswith(visible):
            return str(registered)
    if visible.isdigit():
        for registered in list(record.get("phones") or ()) + list(record.get("accounts") or ()):
            if str(registered or "").endswith(visible):
                return str(registered)
    return ""


def _mask_is_comparable(masked: str, registered_ids) -> bool:
    """Can this registry actually check the digits the mask left visible?

    Only if the payee holds a registered handle with the same provider domain.
    With one on file, a mask that resolves to none of them is a real
    disagreement -- the receipt names an account this payee does not hold. With
    none on file there is nothing to compare against, the mask carries no
    information either way, and the payee's name remains the only evidence
    available.

    That distinction is the whole reason the name fallback can be kept safe:
    it stays reachable exactly where the mask is uncheckable.
    """
    _local, _, domain = str(masked or "").partition("@")
    if not domain:
        return False
    return any(
        str(registered or "").partition("@")[2] == domain
        for registered in registered_ids or ()
    )


def _masked_upi_alias_match(masked: str, registered_ids: Any) -> str:
    """The registered handle a masked one denotes, or "" when nothing matches.

    PhonePe renders the payee as ``XXXXXX4573@ybl``. The mask hides the prefix
    by policy, but what it leaves is not nothing: the provider domain and the
    trailing characters both survive, and together they pick out one registered
    account or none.

    This never makes a masked handle trustworthy on its own. The caller pairs it
    with a receiver-name match against the same record, so a mask is only ever
    read as the account whose name, provider and visible tail all agree with it.
    A mask whose visible tail contradicts the registered handle -- ``4573``
    against a registered ``...1111@ybl`` -- matches nothing here and stays in
    review, which is the entire point: the unmasked part is evidence too.
    """
    local, _, domain = str(masked or "").partition("@")
    if not domain:
        return ""
    visible = local.lstrip("Xx*#•·. ").strip()
    if len(visible) < _MASKED_VISIBLE_MIN:
        return ""
    for registered in registered_ids or ():
        reg_local, _, reg_domain = str(registered or "").partition("@")
        if reg_domain != domain:
            continue
        if reg_local.endswith(visible):
            return str(registered)
    return ""


def _norm_digits(value: Any, *, last: int = 0) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-last:] if last and len(digits) >= last else digits


def _norm_indian_phone(value: Any) -> str:
    raw = str(value or "").strip()
    if "*" in raw or "x" in raw.lower():
        return ""
    digits = _norm_digits(raw)
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return f"+{digits}"
    return ""


def _minor_units(value: Any) -> int:
    """Convert rupees to paise without binary floating-point arithmetic."""
    try:
        return int(
            (Decimal(str(value or 0)) * Decimal("100")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _rupees(minor_units: Any) -> int:
    try:
        return int(Decimal(int(minor_units or 0)) / Decimal("100"))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def payment_engine_v2_enabled() -> bool:
    return os.environ.get("PAYMENT_ENGINE_V2_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def payment_extraction_provider() -> str:
    provider = os.environ.get("PAYMENT_EXTRACTION_PROVIDER", "OLLAMA").strip().upper()
    return provider if provider in {"OLLAMA", "OCR", "OCR_AND_OLLAMA"} else "OLLAMA"


def _split_env(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def payment_ocr_enabled() -> bool:
    """Future switch; both global and payment-specific flags must be enabled."""
    if payment_extraction_provider() not in {"OCR", "OCR_AND_OLLAMA"}:
        return False
    enabled = os.environ.get("PAYMENT_VERIFICATION_OCR_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on", "enabled"}:
        return False
    from core.ocr_policy import ocr_enabled
    return ocr_enabled()


def _receiver_record(
    receiver_id: str,
    receiver_type: str,
    name: str,
    *,
    upi_ids: list[str] | None = None,
    phones: list[str] | None = None,
    accounts: list[str] | None = None,
    aliases: list[str] | None = None,
    active: bool = True,
    verification_status: str = "UNVERIFIED",
    valid_from: str = "",
    valid_until: str = "",
    company_id: str = "",
    referrer_id: str = "",
    provider_name: str = "",
    created_by: str = "configuration",
    verified_at: str = "",
    verified_by: str = "",
) -> dict[str, Any]:
    status = str(verification_status or "UNVERIFIED").strip().upper()
    if status not in {"UNVERIFIED", "VERIFIED", "REJECTED"}:
        status = "UNVERIFIED"
    now = _now()
    return {
        "id": receiver_id,
        "type": receiver_type,
        "owner_type": receiver_type.upper(),
        "company_id": company_id or (receiver_id if receiver_type == "company" else ""),
        "referrer_id": referrer_id or (receiver_id if receiver_type == "referrer" else ""),
        "name": name.strip(),
        "account_holder_name": name.strip(),
        "upi_ids": sorted({_norm_upi(v) for v in (upi_ids or []) if _norm_upi(v)}),
        "phones": sorted({_norm_indian_phone(v) for v in (phones or []) if _norm_indian_phone(v)}),
        "accounts": sorted({_norm_digits(v) for v in (accounts or []) if _norm_digits(v)}),
        "aliases": sorted({_norm_text(v) for v in [name, *(aliases or [])] if _norm_text(v)}),
        "active": bool(active),
        "is_active": bool(active),
        "verification_status": status,
        "provider_name": provider_name,
        "valid_from": str(valid_from or ""),
        "valid_until": str(valid_until or ""),
        "created_at": now,
        "updated_at": now,
        "created_by": created_by,
        "verified_at": verified_at,
        "verified_by": verified_by,
    }


def _receiver_registry_file() -> str:
    return os.environ.get(
        "PAYMENT_RECEIVER_REGISTRY_FILE",
        os.path.join(DATA_DIR, "payment_receiver_accounts.json"),
    )


def receiver_registry(*, referrer_hint: str = "") -> list[dict[str, Any]]:
    """Build the configured receiver registry without exposing it to Ollama."""
    from features.company_payment_verification import (
        configured_company_account_numbers,
        configured_company_phone_numbers,
        configured_company_upi_ids,
    )
    from features.referrer_registry import (
        _account_rows,
        list_referrers,
        resolve_referrer,
    )

    # One payee, several names: the bank prints "JOLLU RAVINDER", PhonePe shows
    # the display name the account carries, and the business ones appear on
    # receipts paid to the same account. They are aliases of one canonical
    # company receiver, never identifiers -- matching still requires a verified
    # UPI, phone or account on the same record.
    company_names = _split_env("COMPANY_PAYMENT_RECEIVER_NAMES") or [
        "J Ravinder", "Jollu Ravinder", "J RAVINDER", "JOLLU RAVINDER",
        "Ravinder MLR", "Interview support", "Hyd Job",
    ]
    records = [
        _receiver_record(
            "company",
            "company",
            company_names[0],
            upi_ids=list(configured_company_upi_ids()),
            phones=list(configured_company_phone_numbers()),
            accounts=list(configured_company_account_numbers()),
            aliases=company_names[1:],
            verification_status="VERIFIED",
            verified_by="company_configuration",
        )
    ]
    configured_rows = _account_rows()
    raw = os.environ.get("PAYMENT_REFERRER_RECEIVERS_JSON", "").strip()
    if raw:
        try:
            configured = json.loads(raw)
            if isinstance(configured, dict):
                configured = [
                    {"name": name, **(details if isinstance(details, dict) else {})}
                    for name, details in configured.items()
                ]
            if isinstance(configured, list):
                configured_rows.extend(configured)
        except (TypeError, ValueError):
            pass
    for item in configured_rows:
        if not isinstance(item, dict):
            continue
        owner_type = str(item.get("owner_type") or item.get("type") or "REFERRER").upper()
        receiver_type = "company" if owner_type == "COMPANY" else "referrer"
        name = str(item.get("account_holder_name") or item.get("name") or "").strip()
        if not name:
            continue
        resolved_referrer = None
        if receiver_type == "referrer":
            lookup_values = [
                item.get("referrer_id"),
                item.get("name"),
                *(item.get("aliases") or []),
                name,
            ]
            resolved_referrer = next(
                (
                    match
                    for value in lookup_values
                    if value and (match := resolve_referrer(value)) is not None
                ),
                None,
            )
        receiver_id = str(
            item.get("id")
            or f"{receiver_type}:{_norm_text(name).replace(' ', '-')}"
        )
        verification_status = str(item.get("verification_status") or "VERIFIED")
        if receiver_type == "referrer" and resolved_referrer is None:
            verification_status = "UNVERIFIED"
        records.append(
            _receiver_record(
                receiver_id,
                receiver_type,
                name,
                upi_ids=list(item.get("upi_ids") or ([item.get("upi_id")] if item.get("upi_id") else [])),
                phones=list(item.get("phones") or ([item.get("payment_phone_number")] if item.get("payment_phone_number") else [])),
                accounts=list(item.get("accounts") or ([item.get("bank_account_identifier")] if item.get("bank_account_identifier") else [])),
                aliases=list(item.get("aliases") or []),
                active=bool(item.get("is_active", item.get("active", True))),
                verification_status=verification_status,
                valid_from=str(item.get("valid_from") or ""),
                valid_until=str(item.get("valid_until") or ""),
                company_id=str(item.get("company_id") or ""),
                referrer_id=str(
                    (resolved_referrer or {}).get("id")
                    or item.get("referrer_id")
                    or ""
                ),
                provider_name=str(item.get("provider_name") or ""),
                created_by=str(item.get("created_by") or "configuration"),
                verified_at=str(item.get("verified_at") or ""),
                verified_by=str(item.get("verified_by") or ""),
            )
        )

    # The same administrator-owned account can be supplied by the registry
    # file and an environment override. Consolidate only when both rows have
    # the same registry ID and stable owner ID. Different owners that reuse an
    # identifier remain separate so conflict detection can block approval.
    consolidated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in records:
        stable_owner_id = str(
            record.get("company_id")
            if record.get("owner_type") == "COMPANY"
            else record.get("referrer_id")
            or ""
        )
        key = (
            str(record.get("id") or ""),
            str(record.get("owner_type") or ""),
            stable_owner_id,
        )
        existing = consolidated.get(key)
        if existing is None:
            consolidated[key] = record
            continue
        for field in ("upi_ids", "phones", "accounts", "aliases"):
            existing[field] = sorted(
                set(existing.get(field) or []) | set(record.get(field) or [])
            )
        for field in (
            "name",
            "account_holder_name",
            "verification_status",
            "provider_name",
            "valid_from",
            "valid_until",
            "updated_at",
            "created_by",
            "verified_at",
            "verified_by",
        ):
            if record.get(field):
                existing[field] = record[field]
        existing["active"] = bool(record.get("active"))
        existing["is_active"] = bool(record.get("is_active"))
    records = list(consolidated.values())

    known_referrers = list_referrers()
    known_names: set[str] = {
        str(row.get("name") or "").strip()
        for row in known_referrers
        if str(row.get("name") or "").strip()
    }
    if referrer_hint.strip():
        known_names.add(referrer_hint.strip())
    referrer_ids = {
        _norm_text(str(row.get("name") or "")): str(row.get("id") or "")
        for row in known_referrers
    }
    existing_aliases = {alias for row in records for alias in row["aliases"]}
    for name in sorted(known_names):
        if _norm_text(name) and _norm_text(name) not in existing_aliases:
            records.append(
                _receiver_record(
                    f"referrer:{_norm_text(name).replace(' ', '-')}",
                    "referrer",
                    name,
                    referrer_id=referrer_ids.get(_norm_text(name), ""),
                    verification_status="UNVERIFIED",
                    created_by="candidate_reference_backfill",
                )
            )
    return records


def _record_active_on(record: dict[str, Any], today: str) -> bool:
    """Whether a registry record is in force on ``today``."""
    active = bool(record.get("is_active", record.get("active", True)))
    if record.get("valid_from") and str(record["valid_from"])[:10] > today:
        active = False
    if record.get("valid_until") and str(record["valid_until"])[:10] < today:
        active = False
    return active


def _record_verified(record: dict[str, Any]) -> bool:
    return str(record.get("verification_status") or "").upper() == "VERIFIED"


def _credit_identity(record: dict[str, Any], today: str) -> tuple[Any, ...] | None:
    """Who this record would credit, and under what standing.

    Two records with equal identities are interchangeable: picking either
    produces the same authorisation outcome, so a tie between them is a
    duplicate registration rather than a genuine ambiguity.

    ``None`` means "cannot prove interchangeable" and must never be collapsed.
    A company payment credits the company whichever company record matched -
    ``company_id`` and ``receiver_registry_id`` are recorded for audit but do
    not select a ledger account. A referrer payment is different: the money is
    somebody's commission, so it collapses only on a matching non-empty
    ``referrer_id``. Two unresolved referrer rows both carrying an empty id are
    exactly the case where guessing pays the wrong person.
    """
    kind = str(record.get("type") or "")
    if kind == "company":
        key: tuple[Any, ...] = ("company",)
    elif kind == "referrer":
        referrer_id = str(record.get("referrer_id") or "").strip()
        if not referrer_id:
            return None
        key = ("referrer", referrer_id)
    else:
        return None
    return key + (_record_active_on(record, today), _record_verified(record))


def classify_receiver(
    extraction: dict[str, Any],
    *,
    referrer_hint: str = "",
) -> dict[str, Any]:
    """Deterministically match extracted receiver facts to the registry."""
    upi = _norm_upi(extraction.get("receiver_upi_id"))
    upi_masked = _is_masked_identifier(upi)
    masked_upi = upi if upi_masked else ""
    if upi_masked:
        # Drop it from matching entirely so the screenshot is treated the same
        # as one that showed no handle at all, rather than one whose handle
        # disagrees with the registry.
        upi = ""
    raw_phone = (
        extraction.get("receiver_phone_number")
        or extraction.get("receiver_phone")
    )
    phone = _norm_indian_phone(raw_phone)
    account = _norm_digits(
        extraction.get("receiver_account")
        or extraction.get("receiver_account_identifier")
    )
    name = _norm_text(extraction.get("receiver_name"))
    today = datetime.now(timezone.utc).date().isoformat()
    matches: list[tuple[int, dict[str, Any], str]] = []
    name_matches: list[dict[str, Any]] = []
    for record in receiver_registry(referrer_hint=referrer_hint):
        if name and name in record["aliases"]:
            name_matches.append(record)
        score, matched_by = 0, ""
        if upi and _valid_upi(upi) and upi in record["upi_ids"]:
            score, matched_by = 100, "upi"
        elif phone and phone in record["phones"]:
            score, matched_by = 100, "phone"
        elif account and account in record["accounts"]:
            score, matched_by = 100, "account"
        elif (
            upi_masked
            and name
            and name in record["aliases"]
            and _masked_record_alias_match(masked_upi, record)
        ):
            # Reached whatever else was extracted, deliberately. This used to
            # require that no other identifier existed at all, so one stray
            # value -- the payer's own bank account, read off the line below the
            # payee -- silently disabled the only field the screenshot actually
            # labels as the receiver's. An identifier that matches no registered
            # receiver is not a reason to ignore one that does.
            # Name, provider domain and the digits the mask left all agree with
            # one registered account. That is a registry-backed identification,
            # not a decision to trust masks in general.
            score, matched_by = 100, "masked_upi_alias"
        elif (
            not (upi or phone or account)
            and name
            and name in record["aliases"]
            and not _mask_is_comparable(masked_upi, record["upi_ids"])
            # A registered phone or account makes every mask checkable, whatever
            # provider drew it: the digits either end one of this payee's
            # numbers or they do not. With one on file the name can no longer
            # rescue a mask that matched nothing.
            and not (record.get("phones") or record.get("accounts"))
        ):
            # Name alone, and only where no identifier could be checked.
            #
            # Masked handles are dropped from identifier matching above so a
            # redacted receipt reads as one showing no handle rather than one
            # that disagrees. Once masks began resolving against the registry
            # that made an *unresolvable* mask indistinguishable from no
            # identifier at all, and this branch then credited it on the payee
            # name alone -- so a receipt naming a registered payee while paying
            # some other account ending 9999 was accepted as a company payment.
            #
            # A mask is only evidence of disagreement when there is something
            # to disagree with. If this payee holds a registered handle on the
            # same provider, the visible digits are checkable and failing to
            # match them is meaningful, so the name must not rescue it. If they
            # hold none, nothing was checkable and the name still stands.
            score, matched_by = 90, "name"
        if score:
            matches.append((score, record, matched_by))
    raw_identifier_present = bool(
        str(extraction.get("receiver_upi_id") or "").strip()
        or str(raw_phone or "").strip()
        or str(
            extraction.get("receiver_account")
            or extraction.get("receiver_account_identifier")
            or ""
        ).strip()
    )
    stable_identifier_present = bool(
        (_valid_upi(upi) if upi else False) or phone or account or upi_masked
    )
    if not matches:
        return {
            "receiver_type": "unknown",
            "receiver_registry_id": "",
            "receiver_registry_name": "",
            "receiver_match": "",
            "receiver_match_score": 0,
            "receiver_match_ambiguous": False,
            "receiver_identifier_present": raw_identifier_present,
            "receiver_identifier_complete": bool(
                (_valid_upi(upi) if upi else False) or phone or account
            ),
            "receiver_identifier_conflict": bool(stable_identifier_present and name_matches),
            "receiver_identifier_masked": upi_masked,
            "receiver_name_match_candidates": [row["id"] for row in name_matches],
            "receiver_account_active": False,
            "receiver_account_verified": False,
        }
    best_score = max(match[0] for match in matches)
    best_matches = [match for match in matches if match[0] == best_score]
    collapsed_duplicates: list[str] = []
    if len(best_matches) > 1:
        # One payee registered more than once - e.g. the company UPI present in
        # both COMPANY_PAYMENT_UPI_IDS and the receiver registry file - is not a
        # disagreement about who to credit. Collapse only when every tied record
        # would produce the same outcome; anything else falls through to the
        # ambiguous branch below, unchanged.
        identities = {_credit_identity(match[1], today) for match in best_matches}
        if len(identities) == 1 and None not in identities:
            collapsed_duplicates = sorted({match[1]["id"] for match in best_matches})
            best_matches = [min(best_matches, key=lambda match: str(match[1]["id"]))]
    if len(best_matches) != 1:
        return {
            "receiver_type": "unknown",
            "receiver_registry_id": "",
            "receiver_registry_name": "",
            "receiver_match": "",
            "receiver_match_score": best_score,
            "receiver_match_ambiguous": True,
            "receiver_match_candidates": [match[1]["id"] for match in best_matches],
            "receiver_identifier_present": raw_identifier_present,
            "receiver_identifier_complete": True,
            "receiver_identifier_conflict": False,
            "receiver_identifier_masked": upi_masked,
            "receiver_account_active": False,
            "receiver_account_verified": False,
        }
    score, record, matched_by = best_matches[0]
    currently_active = _record_active_on(record, today)
    verified = _record_verified(record)
    return {
        "receiver_type": record["type"],
        "receiver_registry_id": record["id"],
        "receiver_registry_name": record["name"],
        "receiver_match": matched_by,
        "receiver_match_score": score,
        "receiver_match_ambiguous": False,
        # Non-empty when one payee was registered more than once. The payment is
        # authorised, but the registry still wants cleaning - see
        # receiver_registry_conflicts().
        "receiver_match_duplicates": collapsed_duplicates,
        "receiver_identifier_present": stable_identifier_present,
        "receiver_identifier_complete": matched_by in {
            "upi", "phone", "account", "masked_upi_alias",
        },
        "receiver_identifier_conflict": False,
        "receiver_identifier_masked": upi_masked,
        "receiver_account_active": currently_active,
        "receiver_account_verified": verified,
        "matched_company_id": record.get("company_id") or "",
        "matched_referrer_id": record.get("referrer_id") or "",
        "receiver_verification_status": record.get("verification_status") or "UNVERIFIED",
    }


def receiver_registry_conflicts() -> list[dict[str, Any]]:
    """Return active duplicate stable identifiers; callers must not auto-pick."""
    ownership: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in receiver_registry():
        if not record.get("is_active", record.get("active", True)):
            continue
        for kind, values in (
            ("upi", record.get("upi_ids") or []),
            ("phone", record.get("phones") or []),
            ("account", record.get("accounts") or []),
        ):
            for value in values:
                ownership.setdefault((kind, value), []).append(record)
    return [
        {
            "identifier_type": kind,
            "identifier": value,
            "account_ids": [row["id"] for row in rows],
            "owner_types": sorted({row["owner_type"] for row in rows}),
        }
        for (kind, value), rows in ownership.items()
        if len({row["id"] for row in rows}) > 1
    ]


def _ledger_file() -> str:
    return os.environ.get(
        "PAYMENT_VERIFICATION_LEDGER_FILE",
        os.path.join(DATA_DIR, "payment_verification_ledger.json"),
    )


def _load_ledger() -> dict[str, Any]:
    path = _ledger_file()
    with _lock:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                data = {}
            data.setdefault("schema_version", 2)
            data.setdefault("evidence", [])
            data.setdefault("payments", [])
            data.setdefault("entries", [])
            data.setdefault("entitlements", [])
            return data
        except (OSError, ValueError):
            return {
                "schema_version": 2,
                "evidence": [],
                "payments": [],
                "entries": [],
                "entitlements": [],
            }


def ledger_available() -> bool:
    """Is the payment ledger actually readable?

    `_load_ledger()` fabricates an empty ledger when the file cannot be read.
    That keeps read paths alive, but an accounting caller subtracting
    recoveries cannot tell "nothing to recover" from "the ledger is gone".
    """
    try:
        with open(_ledger_file(), encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except (OSError, ValueError):
        return False


def _save_ledger(data: dict[str, Any]) -> None:
    path = _ledger_file()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    data["updated_at"] = _now()
    with _lock:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _upsert_ledger(entry: dict[str, Any]) -> dict[str, Any]:
    data = _load_ledger()
    rows = data.setdefault("entries", [])
    key = entry["idempotency_key"]
    existing = next((row for row in rows if row.get("idempotency_key") == key), None)
    if existing:
        sources = list(existing.get("source_modules") or [])
        for source in entry.get("source_modules") or []:
            if source not in sources:
                sources.append(source)
        existing.update({
            k: v
            for k, v in entry.items()
            if k != "id" and v not in ("", None, [], {})
        })
        existing["source_modules"] = sources
        existing["updated_at"] = _now()
        result = dict(existing)
    else:
        rows.append(entry)
        result = dict(entry)
    _save_ledger(data)
    return result


def _append_unique(
    rows: list[dict[str, Any]],
    row: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    existing = next((item for item in rows if item.get(key) == row.get(key)), None)
    if existing:
        return dict(existing)
    rows.append(row)
    return dict(row)


def _commission_pct_for(referrer: str) -> int:
    default = int(os.environ.get("PAYMENT_DEFAULT_COMMISSION_PCT", "50") or 50)
    default = min(100, max(0, default))
    raw = os.environ.get("PAYMENT_COMMISSION_RULES_JSON", "").strip()
    if not raw:
        return default
    try:
        rules = json.loads(raw)
        if isinstance(rules, dict):
            wanted = _norm_text(referrer)
            for key, value in rules.items():
                if _norm_text(key) == wanted:
                    return min(100, max(0, int(value)))
    except (TypeError, ValueError):
        pass
    return default


def _allocation_fields(
    *,
    amount_minor: int,
    receiver_type: str,
    purpose: str,
    referrer: str,
) -> dict[str, Any]:
    pct = _commission_pct_for(referrer)
    referrer_share = (amount_minor * pct) // 100
    company_share = amount_minor - referrer_share
    candidate_fee = purpose == "candidate_payment"
    direct_to_referrer = candidate_fee and receiver_type == "referrer"
    already_received = referrer_share if direct_to_referrer else 0
    recoverable_company = company_share if direct_to_referrer else 0
    return {
        "commission_pct": pct,
        "gross_amount_minor": amount_minor,
        "company_share_minor": company_share if candidate_fee else 0,
        "referrer_share_minor": referrer_share if candidate_fee else 0,
        "amount_already_received_by_referrer_minor": already_received,
        "commission_already_received_minor": already_received,
        "recoverable_company_share_minor": recoverable_company,
        "company_share_recoverable_minor": recoverable_company,
        "total_payout_adjustment_minor": (
            amount_minor if direct_to_referrer else 0
        ),
    }


def _flag_amount_anomalies(result: dict[str, Any]) -> None:
    """Raise a review flag when the amount evidence contradicts itself.

    Every visible figure on a receipt should agree. When they do not — or when
    one is exactly a factor of ten from another — the amount is decided by a
    human, never by whichever number happened to be parsed first.
    """
    amount = int(result.get("amount") or 0)
    if amount <= 0:
        return
    seen: list[int] = []
    for raw in result.get("visible_amounts") or []:
        from features.ollama_payment_extract import _normalize_amount_number
        parsed = _normalize_amount_number(raw)
        if parsed > 0:
            seen.append(parsed)
    if not seen:
        return
    result["amount_candidates"] = sorted(set(seen))
    agreeing = sum(1 for value in seen if value == amount)
    if agreeing >= 2:
        # The same figure printed in two places is the strongest evidence a
        # receipt can offer.
        result["amount_corroborated"] = True
    if any(value == amount * 10 for value in seen):
        result["amount_extraction_review_required"] = True
        result["amount_review_reason"] = (
            f"A visible amount of ₹{amount * 10:,} is exactly ten times the "
            f"parsed ₹{amount:,}. A digit was probably dropped."
        )
        return
    if any(value not in (amount,) for value in seen) and not result.get(
        "amount_corroborated"
    ):
        others = sorted({value for value in seen if value != amount})
        if others:
            result["amount_extraction_review_required"] = True
            result["amount_review_reason"] = (
                f"Visible amounts disagree: parsed ₹{amount:,}, also saw "
                + ", ".join(f"₹{value:,}" for value in others)
                + "."
            )


def _normalize_directional_extraction(
    extraction: dict[str, Any],
) -> dict[str, Any]:
    """Normalize the provider schema and select the actual receiver.

    A person shown under "Received from" is the sender.  In that case only
    the credited-to identifier is eligible for deterministic receiver
    matching.
    """
    result = dict(extraction or {})
    direction = str(result.get("direction") or "UNKNOWN").strip().upper()
    direction = {
        "PAID": "PAID_TO",
        "PAID TO": "PAID_TO",
        "RECEIVED": "RECEIVED_FROM",
        "RECEIVED FROM": "RECEIVED_FROM",
        "TRANSFERRED": "TRANSFERRED_TO",
        "TRANSFERRED TO": "TRANSFERRED_TO",
    }.get(direction, direction)
    if direction not in {"PAID_TO", "RECEIVED_FROM", "TRANSFERRED_TO"}:
        direction = "UNKNOWN"
    result["direction"] = direction

    # Amount resolution, most trustworthy source first.
    #
    # A vision model must never be asked to do arithmetic. Asking one for
    # `amount_minor` (paise) made it convert rupees itself, and it lost a zero
    # on every five-figure amount: ₹30,000 came back as 300000 instead of
    # 3000000, so the receipt read ₹3,000 — with confidence 1.0. The literal
    # text it copies off the image is reliable; its multiplication is not.
    from features.ollama_payment_extract import _normalize_amount_number

    literal = _normalize_amount_number(result.get("amount_text"))
    if literal:
        result["amount"] = literal
        result["amount_source"] = "literal_text"
    elif _normalize_amount_number(result.get("amount")):
        result["amount"] = _normalize_amount_number(result.get("amount"))
        result["amount_source"] = "model_rupees"
    elif result.get("amount_minor") is not None:
        # Legacy, untrusted. Keep the value so nothing is lost, but flag it so
        # it cannot reach a verified state on model arithmetic alone.
        try:
            result["amount"] = _rupees(int(result["amount_minor"]))
        except (TypeError, ValueError):
            result["amount"] = 0
        result["amount_source"] = "model_minor_units_untrusted"
        if result["amount"] > 0:
            result["amount_extraction_review_required"] = True
            result["amount_review_reason"] = (
                "Amount came only from the model's paise conversion, which is "
                "unreliable for five-figure values. Confirm against the "
                "printed amount before verifying."
            )
    else:
        result["amount"] = 0
        result["amount_source"] = "missing"
    result["amount_minor"] = _minor_units(result.get("amount"))
    _flag_amount_anomalies(result)
    result.setdefault("currency", "INR")
    result["status"] = str(
        result.get("payment_status") or result.get("status") or "unknown"
    ).lower()
    result["utr_number"] = result.get("utr") or result.get("utr_number") or ""
    result["payment_date"] = (
        result.get("transaction_date") or result.get("payment_date") or ""
    )
    result["payment_time"] = (
        result.get("transaction_time") or result.get("payment_time") or ""
    )
    result["receiver_phone"] = (
        result.get("receiver_phone_number")
        or result.get("receiver_phone")
        or ""
    )
    result["receiver_phone_number"] = result["receiver_phone"]
    result["receiver_account"] = (
        result.get("receiver_account_identifier")
        or result.get("receiver_account")
        or ""
    )
    result["receiver_account_identifier"] = result["receiver_account"]

    if not result.get("confidence_score") and isinstance(
        result.get("confidence"), dict
    ):
        confidence_dict = result["confidence"]
        relevant_values = []
        for field, value in confidence_dict.items():
            if not isinstance(value, (int, float)):
                continue
            if value == 0.0 and field in {
                "receiver_phone_number",
                "sender_phone_number",
                "transaction_id",
                "utr",
                "receiver_account_identifier",
                "receiver_upi_id",
            }:
                if not result.get(field):
                    continue
            relevant_values.append(float(value))
        if not relevant_values:
            relevant_values = [
                float(v) for v in confidence_dict.values() if isinstance(v, (int, float))
            ]
        if relevant_values:
            average = sum(relevant_values) / len(relevant_values)
            result["confidence_score"] = round(
                average if average > 1 else average * 100
            )

    if direction == "RECEIVED_FROM":
        credited_to = str(result.get("credited_to_identifier") or "").strip()
        explicit_sender_present = bool(
            str(result.get("sender_name") or "").strip()
            or str(result.get("sender_upi_id") or "").strip()
            or str(result.get("sender_phone_number") or "").strip()
            or str(result.get("sender_account_identifier") or "").strip()
        )
        explicit_receiver_identifier_present = bool(
            _valid_upi(_norm_upi(result.get("receiver_upi_id")))
            or _norm_indian_phone(result.get("receiver_phone_number"))
            or _norm_digits(result.get("receiver_account_identifier"))
        )
        # The current Ollama schema can explicitly identify both parties on a
        # "received from" receipt.  In that case receiver_* is the credited
        # account owner and must not be erased.  The legacy fallback below is
        # retained for older extractions that put the payer in receiver_* and
        # supplied only credited_to_identifier for the actual recipient.
        if explicit_sender_present and explicit_receiver_identifier_present:
            return result
        result["sender_name"] = (
            result.get("sender_name") or result.get("receiver_name") or ""
        )
        result["receiver_name"] = str(
            result.get("credited_to_name") or ""
        ).strip()
        result["receiver_upi_id"] = ""
        result["receiver_phone"] = ""
        result["receiver_phone_number"] = ""
        result["receiver_account"] = ""
        result["receiver_account_identifier"] = ""
        if "@" in credited_to:
            result["receiver_upi_id"] = credited_to
        elif _norm_indian_phone(credited_to):
            result["receiver_phone"] = credited_to
            result["receiver_phone_number"] = credited_to
        elif credited_to:
            result["receiver_account"] = credited_to
            result["receiver_account_identifier"] = credited_to
        else:
            missing = list(result.get("missing_fields") or [])
            if "credited_to_identifier" not in missing:
                missing.append("credited_to_identifier")
            result["missing_fields"] = missing
    return result


def _verification_state(
    *,
    is_payment_screenshot: bool,
    extraction_failed: bool,
    status: str,
    amount_readable: bool,
    amount_ok: bool,
    confidence: int,
    receiver: dict[str, Any],
    has_stable_receiver_match: bool,
    has_reference: bool,
    allow_low_confidence_exact_match: bool = False,
) -> tuple[str, list[str]]:
    codes: list[str] = []
    if extraction_failed:
        codes.append("EXTRACTION_FAILED")
    elif not is_payment_screenshot:
        codes.append("NOT_PAYMENT_RECEIPT")
    if status in FAILED_STATUSES:
        codes.append("TRANSACTION_FAILED")
    elif status not in SUCCESS_STATUSES:
        codes.append("TRANSACTION_STATUS_UNCONFIRMED")
    if not amount_readable:
        codes.append("AMOUNT_UNREADABLE")
    elif not amount_ok:
        codes.append("AMOUNT_INSUFFICIENT")
    # For payout/expense receipts, a model's self-reported confidence must not
    # overrule an exact, active, verified UPI/phone/account match. Candidate
    # booking keeps the stricter confidence gate.
    if (
        confidence < int(os.environ.get("PAYMENT_MIN_EXTRACTION_CONFIDENCE", "80") or 80)
        and not (
            allow_low_confidence_exact_match
            and has_stable_receiver_match
        )
    ):
        codes.append("LOW_EXTRACTION_CONFIDENCE")
    if receiver.get("receiver_match_ambiguous"):
        codes.append("AMBIGUOUS_RECEIVER")
    elif receiver.get("receiver_identifier_conflict"):
        codes.append("RECEIVER_IDENTIFIER_CONFLICT")
    elif receiver.get("receiver_type") in {"company", "referrer"} and not has_stable_receiver_match:
        codes.append("STABLE_RECEIVER_IDENTIFIER_REQUIRED")
    elif receiver.get("receiver_type") in {"company", "referrer"} and not receiver.get(
        "receiver_account_active"
    ):
        codes.append("RECEIVER_ACCOUNT_INACTIVE")
    elif receiver.get("receiver_type") in {"company", "referrer"} and not receiver.get(
        "receiver_account_verified"
    ):
        codes.append("RECEIVER_ACCOUNT_UNVERIFIED")
    elif receiver.get("receiver_type") == "unknown":
        if not receiver.get("receiver_identifier_present") or not receiver.get(
            "receiver_identifier_complete"
        ):
            codes.append("STABLE_RECEIVER_IDENTIFIER_REQUIRED")
        else:
            codes.append("UNKNOWN_RECEIVER")
    if not has_reference:
        codes.append("TRANSACTION_REFERENCE_MISSING")

    if "TRANSACTION_FAILED" in codes:
        return "FAILED_PAYMENT", codes
    if "EXTRACTION_FAILED" in codes:
        return "EXTRACTION_FAILED", codes
    if "NOT_PAYMENT_RECEIPT" in codes:
        return "REJECTED", codes
    if "UNKNOWN_RECEIVER" in codes:
        return "UNKNOWN_RECEIVER", codes
    if "STABLE_RECEIVER_IDENTIFIER_REQUIRED" in codes:
        return "INCOMPLETE_PAYMENT_EVIDENCE", codes
    if codes:
        return "PENDING_MANUAL_REVIEW", codes
    return (
        "VERIFIED_COMPANY_PAYMENT"
        if receiver.get("receiver_type") == "company"
        else "VERIFIED_REFERRER_PAYMENT",
        codes,
    )


def _credited_payment(payment: dict[str, Any] | None) -> bool:
    """True when a stored payment actually put money against its entity."""
    if not payment:
        return False
    state = str(payment.get("verification_state") or "")
    if state in NON_CREDITING_VERIFICATION_STATES:
        return False
    return int(payment.get("amount_minor") or 0) > 0


def _payment_scope(value: str) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"ROUND", "PROFILE", "SLOT", "OTHER"} else "OTHER"


def _record_verification_unlocked(
    *,
    digest: str,
    mime_type: str,
    extraction: dict[str, Any],
    result: dict[str, Any],
    source_module: str,
    source_entity_type: str,
    entity_id: str,
    entity_name: str,
    candidate_id: str,
    referrer_id: str,
    payment_scope: str,
    purpose: str,
    create_ledger: bool,
) -> dict[str, Any]:
    """Persist evidence, normalized payment, allocation, and entitlement atomically."""
    if not create_ledger:
        return {}
    data = _load_ledger()
    now = _now()
    references = {
        key: _norm_text(result.get(key)).replace(" ", "")
        for key in ("transaction_id", "utr_number", "reference_number")
        if _norm_text(result.get(key)).replace(" ", "")
    }
    reference = (
        references.get("utr_number")
        or references.get("transaction_id")
        or references.get("reference_number")
        or ""
    )
    idem = f"txn:{reference}" if reference else f"sha256:{digest}"
    raw_model_response = extraction.get("_raw_model_response") or ""
    normalized_extraction = {
        key: value
        for key, value in extraction.items()
        if key != "_raw_model_response"
    }
    evidence = _append_unique(
        data["evidence"],
        {
            "evidence_id": f"ev_{uuid.uuid4().hex[:16]}",
            "idempotency_key": f"evidence:{digest}",
            "sha256": digest,
            "mime_type": mime_type,
            "size": int(result.get("_image_size") or 0),
            "source_module": source_module,
            "source_entity_type": source_entity_type,
            "source_entity_id": entity_id,
            "candidate_id": candidate_id,
            "referrer_id": referrer_id,
            "provider": payment_extraction_provider(),
            "model": result.get("primary_model") or "",
            "model_version": result.get("primary_model") or "",
            "raw_ollama_response": raw_model_response,
            "normalized_extraction": normalized_extraction,
            "raw_extraction": normalized_extraction,
            "extraction_timestamp": now,
            "extraction_warnings": list(result.get("warnings") or []),
            "confidence": result.get("confidence")
            or {"overall": int(result.get("confidence_score") or 0)},
            "created_at": now,
        },
        key="idempotency_key",
    )

    def _same_transaction(row: dict[str, Any]) -> bool:
        if row.get("idempotency_key") == idem:
            return True
        if row.get("evidence_id") == evidence.get("evidence_id"):
            return True
        return bool(
            references
            and set(references.values()).intersection(
                {
                    str(value or "")
                    for value in (row.get("transaction_references") or {}).values()
                }
            )
        )

    matched = [row for row in data["payments"] if _same_transaction(row)]
    existing_payment = next(
        (
            row
            for row in matched
            if str(row.get("source_entity_id") or "") == str(entity_id or "")
        ),
        None,
    )
    # Only a payment that actually credited someone can be double-counted. A
    # rejected or unreadable earlier attempt — the same screenshot first
    # uploaded against the wrong profile, say — used to latch this evidence to
    # that profile forever, so the correct candidate could never be verified and
    # silently stayed at zero. Such an attempt is now stepped over, and this
    # entity gets its own payment row.
    duplicate_source = next(
        (
            row
            for row in matched
            if entity_id
            and row.get("source_entity_id")
            and row.get("source_entity_id") != entity_id
            and _credited_payment(row)
        ),
        None,
    )
    duplicate_cross_entity = bool(duplicate_source)
    if duplicate_cross_entity:
        existing_payment = duplicate_source
        result["verification_state"] = "DUPLICATE_PAYMENT"
        result["deterministic_verified"] = False
        result["company_payment_verified"] = False
        result["referrer_sponsored"] = False
        result["ledger_status"] = "rejected"
        result["reason_codes"] = list(
            dict.fromkeys([*(result.get("reason_codes") or []), "DUPLICATE_PAYMENT"])
        )
        result["duplicate_payment_id"] = existing_payment.get("payment_id")
        result["evidence_id"] = evidence["evidence_id"]
        _save_ledger(data)
        return {"evidence": evidence, "payment": existing_payment, "duplicate": True}

    if existing_payment:
        sources = existing_payment.setdefault("source_modules", [])
        if source_module not in sources:
            sources.append(source_module)
        payment = existing_payment
    else:
        payment = {
            "payment_id": f"pay_{uuid.uuid4().hex[:16]}",
            "idempotency_key": idem,
            "evidence_id": evidence["evidence_id"],
            "verification_state": result["verification_state"],
            "reason_codes": list(result.get("reason_codes") or []),
            "amount_minor": _minor_units(result.get("amount")),
            "currency": str(result.get("currency") or "INR").upper(),
            "receiver_type": result.get("receiver_type") or "unknown",
            "receiver_registry_id": result.get("receiver_registry_id") or "",
            "receiver_registry_name": result.get("receiver_registry_name") or "",
            "transaction_reference": reference,
            "transaction_references": references,
            "transaction_date": result.get("payment_date") or "",
            "transaction_time": result.get("payment_time") or "",
            "source_modules": [source_module],
            "source_entity_type": source_entity_type,
            "source_entity_id": entity_id,
            "source_entity_name": entity_name,
            "candidate_id": candidate_id,
            "referrer_id": referrer_id,
            "payment_scope": payment_scope,
            "purpose": purpose,
            "model": result.get("primary_model") or "",
            "confidence_score": int(result.get("confidence_score") or 0),
            "matched_account_id": result.get("receiver_registry_id") or "",
            "matched_owner_type": str(result.get("receiver_type") or "").upper(),
            "human_explanation": " ".join(result.get("deterministic_reasons") or []),
            "verified_at": now,
            "created_at": now,
        }
        data["payments"].append(payment)

    if result.get("deterministic_verified"):
        referrer = (
            result.get("receiver_registry_name")
            if result.get("receiver_type") == "referrer"
            else referrer_id
        ) or ""
        allocations = _allocation_fields(
            amount_minor=payment["amount_minor"],
            receiver_type=str(result.get("receiver_type") or ""),
            purpose=purpose,
            referrer=referrer,
        )
        transaction_type = (
            "CANDIDATE_FEE_RECEIVED_BY_COMPANY"
            if purpose == "candidate_payment" and result.get("receiver_type") == "company"
            else "CANDIDATE_FEE_RECEIVED_BY_REFERRER"
            if purpose == "candidate_payment" and result.get("receiver_type") == "referrer"
            else "COMMISSION_PAYOUT"
            if purpose == "handler_payout"
            else "APPROVED_EXPENSE_REIMBURSEMENT"
            if purpose in {"expense_reimbursement", "approved_expense_reimbursement"}
            else "RECOVERABLE_ADVANCE"
            if purpose == "recoverable_advance"
            else str(purpose or "PAYOUT_ADJUSTMENT").upper()
        )
        entry = _append_unique(
            data["entries"],
            {
                "id": uuid.uuid4().hex[:16],
                "ledger_entry_id": f"le_{uuid.uuid4().hex[:16]}",
                "idempotency_key": f"{payment['payment_id']}:{transaction_type}",
                "payment_id": payment["payment_id"],
                "evidence_id": evidence["evidence_id"],
                "transaction_type": transaction_type,
                "action": result["ledger_action"],
                "status": "posted",
                "settlement_status": (
                    "PENDING"
                    if transaction_type == "CANDIDATE_FEE_RECEIVED_BY_REFERRER"
                    else "NOT_APPLICABLE"
                ),
                "source_module": source_module,
                "source_modules": [source_module],
                "source_entity_type": source_entity_type,
                "source_entity_id": entity_id,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "candidate_id": candidate_id,
                "referrer_id": referrer_id,
                "referrer": referrer,
                "receiver_registry_name": result.get("receiver_registry_name") or "",
                "receiver_account_id": result.get("receiver_registry_id") or "",
                "purpose": purpose,
                "payment_scope": payment_scope,
                "payment_date": result.get("payment_date") or "",
                "payment_time": result.get("payment_time") or "",
                # Identity of the underlying bank transaction. The extractor has
                # always read these off the receipt; until now they were dropped
                # once verification finished, which left nothing to match a
                # ledger row against when the same receipt was filed a second
                # time in another module.
                "external_transaction_id": transaction_identity.normalize_external_id(reference),
                "payer": result.get("sender_name") or result.get("sender_upi_id") or "",
                "sender_name": result.get("sender_name") or "",
                "sender_upi_id": result.get("sender_upi_id") or "",
                "receiver": result.get("receiver_registry_name")
                or result.get("receiver_name")
                or "",
                "screenshot_hash": digest,
                "gross_amount": _rupees(allocations["gross_amount_minor"]),
                "company_share": _rupees(allocations["company_share_minor"]),
                "referrer_share": _rupees(allocations["referrer_share_minor"]),
                "amount_already_received_by_referrer": _rupees(
                    allocations["amount_already_received_by_referrer_minor"]
                ),
                "recoverable_company_share": _rupees(
                    allocations["recoverable_company_share_minor"]
                ),
                "total_payout_adjustment": _rupees(
                    allocations["total_payout_adjustment_minor"]
                ),
                "amount": _rupees(allocations["gross_amount_minor"]),
                "currency": "INR",
                **allocations,
                "recoverable_advance_minor": (
                    payment["amount_minor"]
                    if transaction_type == "RECOVERABLE_ADVANCE"
                    else 0
                ),
                "reimbursement_minor": (
                    payment["amount_minor"]
                    if transaction_type == "APPROVED_EXPENSE_REIMBURSEMENT"
                    else 0
                ),
                "recover_from_future_commission": (
                    transaction_type == "CANDIDATE_FEE_RECEIVED_BY_REFERRER"
                ),
                "reversal_of_entry_id": "",
                "created_by": "payment_verification_engine",
                "effective_date": result.get("payment_date") or now[:10],
                "audit_metadata": {
                    "verification_state": result.get("verification_state"),
                    "reason_codes": list(result.get("reason_codes") or []),
                    "model": result.get("primary_model") or "",
                    "provider": payment_extraction_provider(),
                },
                "created_at": now,
            },
            key="idempotency_key",
        )
        stored_entry = next(
            (
                row
                for row in data["entries"]
                if row.get("idempotency_key") == entry.get("idempotency_key")
            ),
            None,
        )
        if stored_entry is not None:
            sources = stored_entry.setdefault("source_modules", [])
            if source_module not in sources:
                sources.append(source_module)
            entry = dict(stored_entry)
        result["ledger_entry_id"] = entry["ledger_entry_id"]
        result["ledger_idempotency_key"] = entry["idempotency_key"]

        if purpose == "candidate_payment":
            entitlement = _append_unique(
                data["entitlements"],
                {
                    "entitlement_id": f"ent_{uuid.uuid4().hex[:16]}",
                    "idempotency_key": f"{payment['payment_id']}:{payment_scope}",
                    "payment_id": payment["payment_id"],
                    "candidate_id": candidate_id,
                    "candidate_name": entity_name,
                    "payment_scope": payment_scope,
                    "status": "ACTIVE",
                    "reusable": payment_scope == "PROFILE",
                    "usage_limit": None if payment_scope == "PROFILE" else 1,
                    "usage_count": 0,
                    "consumed_by": [],
                    "technology": "",
                    "round": "",
                    "created_at": now,
                },
                key="idempotency_key",
            )
            result["entitlement_id"] = entitlement["entitlement_id"]

    result["evidence_id"] = evidence["evidence_id"]
    result["payment_id"] = payment["payment_id"]
    _save_ledger(data)
    return {"evidence": evidence, "payment": payment, "duplicate": False}


def _record_verification(**kwargs: Any) -> dict[str, Any]:
    """Serialize the complete JSON transaction across concurrent requests."""
    with _lock:
        return _record_verification_unlocked(**kwargs)


def verify_payment_screenshot(
    image_data: bytes,
    mime_type: str = "image/jpeg",
    *,
    source_module: str,
    expected_amount: int = 0,
    entity_id: str = "",
    entity_name: str = "",
    referrer_hint: str = "",
    purpose: str = "candidate_payment",
    source_entity_type: str = "candidate",
    candidate_id: str = "",
    referrer_id: str = "",
    payment_scope: str = "OTHER",
    create_ledger: bool = True,
) -> dict[str, Any]:
    """Run Ollama extraction, deterministic authorization, and atomic accounting."""
    if not image_data:
        raise ValueError("Empty payment screenshot")
    max_bytes = int(os.environ.get("PAYMENT_EVIDENCE_MAX_BYTES", str(10 * 1024 * 1024)))
    if len(image_data) > max_bytes:
        raise ValueError(f"File too large (max {max_bytes // (1024 * 1024)} MB)")
    normalized_mime = str(mime_type or "image/jpeg").lower().split(";")[0].strip()
    if normalized_mime not in ALLOWED_PAYMENT_MIME_TYPES:
        raise ValueError("Only payment screenshot image files are allowed")
    from features.ollama_payment_extract import (
        _drop_sender_values_from_receiver_fields,
        extract_payment_with_ollama,
        verify_payment_against_due,
    )

    use_ocr = payment_ocr_enabled()
    if payment_extraction_provider() != "OLLAMA" and not use_ocr:
        raise ValueError("Configured payment extraction provider is not enabled")
    extraction = extract_payment_with_ollama(
        image_data,
        normalized_mime,
        allow_slow_ai=True,
        use_ocr=use_ocr,
        crosscheck_ocr=True,
    )
    normalized_extraction = _normalize_directional_extraction(extraction or {})
    # Strip the payer's own identifiers out of the receiver fields HERE, where
    # every extraction path converges.
    #
    # This was first put in extract_payment_with_ollama, thirteen conditionals
    # deep on a `backup_response` branch that the ordinary upload never reaches.
    # Its unit test called the helper directly, so the test passed and the live
    # path went on matching the payer's bank account as the payee's -- the exact
    # shape of failure this repository has been bitten by before: a green test
    # for a path that never runs.
    _drop_sender_values_from_receiver_fields(normalized_extraction)
    result = verify_payment_against_due(
        normalized_extraction, max(0, int(expected_amount or 0))
    )
    receiver = classify_receiver(result, referrer_hint=referrer_hint)
    result.update(receiver)
    result["ollama_receiver_type"] = str((extraction or {}).get("receiver_type") or "unknown")
    result["source_module"] = source_module
    result["ocr_used"] = use_ocr
    result["extraction_provider"] = payment_extraction_provider()
    result["verification_engine"] = (
        "central_payment_verification_v2"
        if payment_engine_v2_enabled()
        else "central_payment_verification_v1"
    )
    result["source_entity_type"] = source_entity_type
    result["source_entity_id"] = entity_id
    result["candidate_id"] = candidate_id or (entity_id if source_entity_type == "candidate" else "")
    result["referrer_id"] = referrer_id
    result["payment_scope"] = _payment_scope(payment_scope)
    result["purpose"] = purpose

    status = str(result.get("status") or "unknown").lower()
    is_payment_screenshot = bool(result.get("is_payment_screenshot"))
    amount_readable = int(result.get("amount") or 0) > 0
    amount_ok = (
        bool(result.get("amount_sufficient"))
        if expected_amount > 0
        else amount_readable
    )
    extraction_failed = bool(
        not is_payment_screenshot
        and (
            str(result.get("extraction_source") or "")
            in {"error", "ocr_fallback", "vision_failed"}
            or any(
                "model" in str(warning).lower() and "fail" in str(warning).lower()
                for warning in (result.get("warnings") or [])
            )
        )
    )
    reference = str(
        result.get("utr_number")
        or result.get("transaction_id")
        or result.get("reference_number")
        or ""
    ).strip()
    has_stable_receiver_match = (
        result.get("receiver_type") in {"company", "referrer"}
        and result.get("receiver_match") in {
            "upi", "phone", "account", "masked_upi_alias",
        }
        and int(result.get("receiver_match_score") or 0) >= 100
    )
    if (
        result.get("receiver_type") == "referrer"
        and os.environ.get("REFERRER_RECEIVER_FLOW_ENABLED", "true").strip().lower()
        in {"0", "false", "no", "off", "disabled"}
    ):
        has_stable_receiver_match = False
    verification_state, reason_codes = _verification_state(
        is_payment_screenshot=is_payment_screenshot,
        extraction_failed=extraction_failed,
        status=status,
        amount_readable=amount_readable,
        amount_ok=amount_ok,
        confidence=int(result.get("confidence_score") or 0),
        receiver=result,
        has_stable_receiver_match=has_stable_receiver_match,
        has_reference=bool(reference),
        allow_low_confidence_exact_match=purpose in {
            "handler_payout",
            "expense_reimbursement",
            "approved_expense_reimbursement",
        },
    )
    amount_mismatch_reason = str(result.get("amount_mismatch_reason") or "").strip()
    if amount_mismatch_reason:
        reason_codes = list(dict.fromkeys([*reason_codes, "AMOUNT_SOURCE_MISMATCH"]))
        verification_state = "PENDING_MANUAL_REVIEW"
        result["verified"] = False
    # An amount the model arrived at by arithmetic, or one contradicted by
    # another figure on the same receipt, cannot verify itself. Confidence is
    # no help here: every known factor-of-ten error reported 1.0.
    if result.get("amount_extraction_review_required"):
        reason_codes = list(
            dict.fromkeys([*reason_codes, "AMOUNT_EXTRACTION_REVIEW_REQUIRED"])
        )
        verification_state = "PENDING_MANUAL_REVIEW"
        result["verified"] = False
    deterministic_verified = verification_state in {
        "VERIFIED_COMPANY_PAYMENT",
        "VERIFIED_REFERRER_PAYMENT",
    }
    result["verification_state"] = verification_state
    result["reason_codes"] = reason_codes
    result["deterministic_verified"] = deterministic_verified
    result["booking_eligible"] = deterministic_verified and purpose == "candidate_payment"
    result["company_payment_verified"] = (
        verification_state == "VERIFIED_COMPANY_PAYMENT"
    )
    result["referrer_sponsored"] = (
        verification_state == "VERIFIED_REFERRER_PAYMENT"
        and purpose == "candidate_payment"
    )

    if (
        purpose in {"handler_payout", "expense_reimbursement"}
        and deterministic_verified
        and result["receiver_type"] == "referrer"
    ):
        action, ledger_status = "approved_expense", "posted"
    elif result["company_payment_verified"]:
        action, ledger_status = "company_credit", "posted"
    elif result["referrer_sponsored"]:
        action, ledger_status = "referrer_recovery", "posted"
    else:
        action, ledger_status = "unknown_pending", "pending"
    if status in FAILED_STATUSES or not is_payment_screenshot:
        ledger_status = "rejected"
    result["ledger_action"] = action
    result["ledger_status"] = ledger_status
    result["recover_from_future_commission"] = action == "referrer_recovery"

    reasons = []
    if extraction_failed:
        reasons.append("Ollama Vision could not extract a usable payment result.")
    elif not is_payment_screenshot:
        reasons.append("This is not a valid payment receipt.")
    if status not in SUCCESS_STATUSES:
        reasons.append("Only a successful, completed transaction can be accepted.")
    if expected_amount > 0 and not amount_ok:
        reasons.append(f"The verified amount does not cover the required ₹{int(expected_amount):,}.")
    if result.get("receiver_match_ambiguous"):
        reasons.append("Multiple registered receivers match this payment; manual review is required.")
    elif result.get("receiver_identifier_conflict"):
        reasons.append(
            "The receiver name resembles a registered account, but the visible "
            "payment identifier does not match it."
        )
    elif result.get("receiver_type") in {"company", "referrer"} and not result.get(
        "receiver_account_active"
    ):
        reasons.append("The matched receiver account is inactive or outside its validity period.")
    elif result.get("receiver_type") in {"company", "referrer"} and not result.get(
        "receiver_account_verified"
    ):
        reasons.append("The matched receiver account is not verified.")
    elif result["receiver_type"] not in {"company", "referrer"}:
        reasons.append("The receiver is not present in the configured receiver registry.")
    elif not has_stable_receiver_match:
        reasons.append(
            "A configured receiver UPI, phone, or account identifier must be visible; "
            "a receiver name alone is not sufficient."
        )
    if "LOW_EXTRACTION_CONFIDENCE" in reason_codes:
        reasons.append("Payment extraction confidence is below the configured threshold.")
    if "TRANSACTION_REFERENCE_MISSING" in reason_codes:
        reasons.append("The transaction or UTR reference is not visible.")
    if amount_mismatch_reason:
        reasons.append(amount_mismatch_reason)
    result["deterministic_reasons"] = reasons

    result["_image_size"] = len(image_data)
    resolved_referrer_id = (
        str(
            result.get("matched_referrer_id")
            or result.get("receiver_registry_id")
            or ""
        )
        if result.get("receiver_type") == "referrer"
        else referrer_id or referrer_hint
    )
    result["referrer_id"] = resolved_referrer_id
    _record_verification(
        digest=hashlib.sha256(image_data).hexdigest(),
        mime_type=normalized_mime,
        extraction=dict(normalized_extraction),
        result=result,
        source_module=source_module,
        source_entity_type=source_entity_type,
        entity_id=entity_id,
        entity_name=entity_name,
        candidate_id=result["candidate_id"],
        referrer_id=resolved_referrer_id,
        payment_scope=result["payment_scope"],
        purpose=purpose,
        create_ledger=create_ledger,
    )
    result.pop("_image_size", None)
    result.pop("_raw_model_response", None)
    return result


def correct_extraction_amount(
    *,
    transaction_reference: str,
    corrected_amount: int,
    reason: str,
    reviewer: str,
    extractor_version: str = "",
) -> dict[str, Any]:
    """Supersede a payment's amount after a confirmed extraction defect.

    `verify_payment_screenshot` deliberately cannot do this. Its idempotency key
    is the transaction reference, so a re-run finds the existing payment and
    leaves its amount alone — which is exactly what stops one screenshot being
    credited twice. Correcting an amount therefore needs its own door, and it
    stays narrow: it matches one existing payment by reference, records what the
    amount was before, and never creates a payment, an entitlement or a second
    ledger entry.

    Identity is untouched. UTR, transaction id, evidence, checksum and the
    original raw model response all stay exactly as captured.
    """
    reference = _norm_text(transaction_reference).replace(" ", "")
    if not reference:
        raise ValueError("A transaction reference is required")
    corrected_amount = int(corrected_amount)
    if corrected_amount <= 0:
        raise ValueError("A corrected amount must be positive")

    with _lock:
        data = _load_ledger()
        matches = [
            row
            for row in data.get("payments") or []
            if _norm_text(row.get("transaction_reference")).replace(" ", "") == reference
        ]
        if not matches:
            raise ValueError(f"No payment found for reference {transaction_reference}")
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} payments share reference {transaction_reference}; "
                "resolve the duplicate before correcting an amount"
            )
        payment = matches[0]
        previous_minor = int(payment.get("amount_minor") or 0)
        new_minor = _minor_units(corrected_amount)
        if previous_minor == new_minor:
            return {"payment": dict(payment), "changed": False, "corrections": 1}

        history = payment.setdefault("amount_corrections", [])
        history.append({
            "corrected_at": _now(),
            "previous_amount_minor": previous_minor,
            "previous_amount": _rupees(previous_minor),
            "new_amount_minor": new_minor,
            "new_amount": corrected_amount,
            "previous_verification_state": payment.get("verification_state"),
            "reviewer": reviewer,
            "reason": reason,
            "extractor_version": extractor_version,
        })
        payment["amount_minor"] = new_minor
        payment["amount_source"] = "literal_text_correction"
        payment["updated_at"] = _now()

        # The posted ledger entry carries the same figure, so it moves with the
        # payment rather than being re-posted as a second credit.
        for entry in data.get("entries") or []:
            if entry.get("payment_id") != payment.get("payment_id"):
                continue
            allocations = _allocation_fields(
                amount_minor=new_minor,
                receiver_type=str(payment.get("receiver_type") or ""),
                purpose=str(payment.get("purpose") or "candidate_payment"),
                referrer=str(payment.get("receiver_registry_name") or ""),
            )
            entry.update(allocations)
            entry["updated_at"] = _now()
            entry.setdefault("amount_corrections", []).append({
                "corrected_at": _now(),
                "previous_amount_minor": previous_minor,
                "new_amount_minor": new_minor,
                "reviewer": reviewer,
            })
        _save_ledger(data)
        return {
            "payment": dict(payment),
            "changed": True,
            "previous_amount": _rupees(previous_minor),
            "new_amount": corrected_amount,
            "corrections": len(history),
        }


def quarantine_payment(
    *,
    transaction_reference: str,
    file_state: str,
    reason: str,
    reviewer: str,
    verification_state: str = "AMOUNT_EXTRACTION_REVIEW_REQUIRED",
) -> dict[str, Any]:
    """Withdraw a payment's trusted status without touching its amount.

    Used when an amount is known to be unreliable but the evidence needed to
    correct it is gone. The recorded figure stays exactly as it is — guessing a
    replacement would be inventing money — and the previous verification state
    is preserved so the history shows what was believed and when that stopped.

    Quarantine deliberately does not recalculate anything. A candidate's
    recorded total was set from business reality, and removing trust from one
    proof is not evidence that the money never arrived.
    """
    reference = _norm_text(transaction_reference).replace(" ", "")
    if not reference:
        raise ValueError("A transaction reference is required")
    if file_state not in FILE_AVAILABILITY_STATES:
        raise ValueError(f"Unknown file availability state: {file_state}")
    if verification_state not in VERIFICATION_STATES:
        raise ValueError(f"Unknown verification state: {verification_state}")

    with _lock:
        data = _load_ledger()
        matches = [
            row
            for row in data.get("payments") or []
            if _norm_text(row.get("transaction_reference")).replace(" ", "") == reference
        ]
        if not matches:
            raise ValueError(f"No payment found for reference {transaction_reference}")
        if len(matches) > 1:
            raise ValueError(
                f"{len(matches)} payments share reference {transaction_reference}"
            )
        payment = matches[0]
        previous_state = payment.get("verification_state")
        previous_file = payment.get("file_availability", FILE_AVAILABLE)
        if previous_state == verification_state and previous_file == file_state:
            return {"payment": dict(payment), "changed": False}

        payment.setdefault("quarantine_history", []).append({
            "quarantined_at": _now(),
            "previous_verification_state": previous_state,
            "new_verification_state": verification_state,
            "previous_file_availability": previous_file,
            "new_file_availability": file_state,
            "reviewer": reviewer,
            "reason": reason,
        })
        payment["verification_state"] = verification_state
        payment["file_availability"] = file_state
        payment["blocks_automatic_reconciliation"] = True
        payment["updated_at"] = _now()

        # A posted credit stops counting while the evidence behind it is in
        # doubt, but the entry itself is kept so the history stays readable.
        for entry in data.get("entries") or []:
            if entry.get("payment_id") == payment.get("payment_id"):
                entry["status"] = "quarantined"
                entry["updated_at"] = _now()
        _save_ledger(data)
        return {
            "payment": dict(payment),
            "changed": True,
            "previous_verification_state": previous_state,
            "verification_state": verification_state,
            "file_availability": file_state,
        }


def mark_file_availability(
    *, transaction_reference: str, file_state: str, reason: str, reviewer: str
) -> dict[str, Any]:
    """Record that a payment's original file is missing, damaged or archived,
    leaving the verification verdict alone."""
    if file_state not in FILE_AVAILABILITY_STATES:
        raise ValueError(f"Unknown file availability state: {file_state}")
    reference = _norm_text(transaction_reference).replace(" ", "")
    with _lock:
        data = _load_ledger()
        matches = [
            row
            for row in data.get("payments") or []
            if _norm_text(row.get("transaction_reference")).replace(" ", "") == reference
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one payment for {transaction_reference}, "
                f"found {len(matches)}"
            )
        payment = matches[0]
        previous = payment.get("file_availability", FILE_AVAILABLE)
        if previous == file_state:
            return {"payment": dict(payment), "changed": False}
        payment.setdefault("file_availability_history", []).append({
            "recorded_at": _now(),
            "previous": previous,
            "new": file_state,
            "reviewer": reviewer,
            "reason": reason,
        })
        payment["file_availability"] = file_state
        if file_state in FILE_STATES_BLOCKING_VERIFICATION:
            payment["blocks_automatic_reconciliation"] = True
        payment["updated_at"] = _now()
        _save_ledger(data)
        return {"payment": dict(payment), "changed": True, "previous": previous}


def add_corroborating_evidence(
    *,
    transaction_reference: str,
    description: str,
    supplied_by: str,
    stated_amount: int = 0,
    source: str = "ADMINISTRATOR_CORROBORATING_EVIDENCE",
) -> dict[str, Any]:
    """Attach out-of-band evidence to a payment whose original file is gone.

    This is testimony, not a system capture, so it is stored in its own list and
    never becomes the payment's amount or verification state. Someone reading
    the record later must be able to see the difference between what the system
    captured and what a person later said about it.
    """
    reference = _norm_text(transaction_reference).replace(" ", "")
    with _lock:
        data = _load_ledger()
        matches = [
            row
            for row in data.get("payments") or []
            if _norm_text(row.get("transaction_reference")).replace(" ", "") == reference
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one payment for {transaction_reference}, "
                f"found {len(matches)}"
            )
        payment = matches[0]
        record = {
            "recorded_at": _now(),
            "source": source,
            "description": description,
            "supplied_by": supplied_by,
            "stated_amount": max(0, int(stated_amount or 0)),
            "is_original_system_capture": False,
        }
        existing = payment.setdefault("corroborating_evidence", [])
        if any(
            item.get("description") == description and item.get("source") == source
            for item in existing
        ):
            return {"payment": dict(payment), "changed": False}
        existing.append(record)
        payment["updated_at"] = _now()
        _save_ledger(data)
        return {"payment": dict(payment), "changed": True, "evidence": record}


def ledger_entries(*, month: str | None = None, action: str | None = None) -> list[dict[str, Any]]:
    rows = list(_load_ledger().get("entries") or [])
    if month and month != "all":
        rows = [row for row in rows if str(row.get("payment_date") or row.get("created_at") or "")[:7] == month]
    if action:
        rows = [row for row in rows if row.get("action") == action]
    return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)


def recovery_summary_by_referrer(*, month: str | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in ledger_entries(month=month, action="referrer_recovery"):
        if row.get("status") != "posted":
            continue
        referrer = str(row.get("referrer") or row.get("receiver_registry_name") or "").strip()
        if not referrer:
            continue
        key = referrer.lower()
        bucket = out.setdefault(
            key,
            {
                "name": referrer,
                "total": 0,
                "count": 0,
                "commission_already_received": 0,
                "recoverable_company_share": 0,
            },
        )
        already_received = int(row.get("amount_already_received_by_referrer") or 0)
        company_recoverable = int(row.get("recoverable_company_share") or 0)
        total_adjustment = int(
            row.get("total_payout_adjustment")
            or already_received + company_recoverable
            or row.get("amount")
            or 0
        )
        bucket["total"] += total_adjustment
        bucket["commission_already_received"] += already_received
        bucket["recoverable_company_share"] += company_recoverable
        bucket["count"] += 1
    return out


def entitlement_for_payment(payment_id: str) -> dict[str, Any] | None:
    return next(
        (
            dict(row)
            for row in _load_ledger().get("entitlements") or []
            if row.get("payment_id") == payment_id
        ),
        None,
    )


def stored_proof_is_booking_eligible(entry: dict[str, Any]) -> bool:
    """Re-check saved proof metadata against the immutable central ledger."""
    if not entry.get("booking_eligible"):
        # Backward-compatible company proofs retain the previous strict check.
        try:
            from features.company_payment_verification import (
                stored_proof_is_verified_company_payment,
            )

            return stored_proof_is_verified_company_payment(entry)
        except Exception:
            return False
    ledger_id = str(entry.get("ledger_entry_id") or "")
    if not ledger_id:
        return False
    row = next(
        (
            item
            for item in _load_ledger().get("entries") or []
            if item.get("ledger_entry_id") == ledger_id
        ),
        None,
    )
    return bool(
        row
        and row.get("status") == "posted"
        and row.get("transaction_type")
        in {
            "CANDIDATE_FEE_RECEIVED_BY_COMPANY",
            "CANDIDATE_FEE_RECEIVED_BY_REFERRER",
        }
    )


def consume_entitlement(
    entitlement_id: str,
    *,
    source_entity_id: str,
    technology: str = "",
    interview_round: str = "",
) -> dict[str, Any] | None:
    """Consume a round/slot entitlement; profile entitlements remain reusable."""
    data = _load_ledger()
    entitlement = next(
        (
            row
            for row in data.get("entitlements") or []
            if row.get("entitlement_id") == entitlement_id
        ),
        None,
    )
    if not entitlement:
        return None
    consumed = entitlement.setdefault("consumed_by", [])
    if source_entity_id and source_entity_id in consumed:
        return dict(entitlement)
    if entitlement.get("status") != "ACTIVE":
        return None
    if source_entity_id and source_entity_id not in consumed:
        consumed.append(source_entity_id)
    entitlement["usage_count"] = len(consumed)
    entitlement["technology"] = technology or entitlement.get("technology") or ""
    entitlement["round"] = interview_round or entitlement.get("round") or ""
    if not entitlement.get("reusable"):
        entitlement["status"] = "CONSUMED"
    entitlement["updated_at"] = _now()
    _save_ledger(data)
    return dict(entitlement)


def reverse_ledger_entry(
    ledger_entry_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Append a reversal; posted financial history is never overwritten."""
    if not actor.strip() or not reason.strip():
        raise ValueError("Reversal actor and reason are required")
    data = _load_ledger()
    original = next(
        (
            row
            for row in data.get("entries") or []
            if row.get("ledger_entry_id") == ledger_entry_id
        ),
        None,
    )
    if not original:
        raise ValueError("Ledger entry not found")
    existing = next(
        (
            row
            for row in data.get("entries") or []
            if row.get("reversal_of_entry_id") == ledger_entry_id
        ),
        None,
    )
    if existing:
        return dict(existing)
    reversal = {
        **{
            key: value
            for key, value in original.items()
            if key
            not in {
                "id",
                "ledger_entry_id",
                "idempotency_key",
                "created_at",
                "settlement_status",
                "status",
            }
        },
        "id": uuid.uuid4().hex[:16],
        "ledger_entry_id": f"le_{uuid.uuid4().hex[:16]}",
        "idempotency_key": f"reversal:{ledger_entry_id}",
        "transaction_type": "REVERSAL",
        "gross_amount_minor": -int(original.get("gross_amount_minor") or 0),
        "company_share_minor": -int(original.get("company_share_minor") or 0),
        "referrer_share_minor": -int(original.get("referrer_share_minor") or 0),
        "amount_already_received_by_referrer_minor": -int(
            original.get("amount_already_received_by_referrer_minor") or 0
        ),
        "commission_already_received_minor": -int(
            original.get("commission_already_received_minor")
            or original.get("amount_already_received_by_referrer_minor")
            or 0
        ),
        "recoverable_company_share_minor": -int(
            original.get("recoverable_company_share_minor") or 0
        ),
        "company_share_recoverable_minor": -int(
            original.get("company_share_recoverable_minor")
            or original.get("recoverable_company_share_minor")
            or 0
        ),
        "total_payout_adjustment_minor": -int(
            original.get("total_payout_adjustment_minor") or 0
        ),
        "status": "posted",
        "settlement_status": "REVERSED",
        "reversal_of_entry_id": ledger_entry_id,
        "created_by": actor.strip(),
        "reversal_reason": reason.strip(),
        "created_at": _now(),
    }
    for key in (
        "gross_amount",
        "company_share",
        "referrer_share",
        "amount_already_received_by_referrer",
        "recoverable_company_share",
        "total_payout_adjustment",
        "amount",
    ):
        reversal[key] = -int(original.get(key) or 0)
    data["entries"].append(reversal)
    _save_ledger(data)
    return dict(reversal)


def settlement_statement(
    referrer: str,
    *,
    month: str | None = None,
    gross_commission: int = 0,
    recoverable_advances: int = 0,
    other_approved_deductions: int = 0,
    unpaid_approved_reimbursements: int = 0,
    payments_already_made: int = 0,
    opening_balance: int = 0,
) -> dict[str, Any]:
    """Return an explicit month-end statement in rupees and paise."""
    key = _norm_text(referrer)
    direct_minor = 0
    company_recoverable_minor = 0
    for row in ledger_entries(month=month, action="referrer_recovery"):
        if row.get("status") != "posted" or _norm_text(row.get("referrer")) != key:
            continue
        direct_minor += int(
            row.get("amount_already_received_by_referrer_minor") or 0
        )
        company_recoverable_minor += int(
            row.get("recoverable_company_share_minor") or 0
        )
    gross_minor = _minor_units(gross_commission)
    opening_minor = _minor_units(opening_balance)
    deductions_minor = (
        direct_minor
        + company_recoverable_minor
        + _minor_units(recoverable_advances)
        + _minor_units(other_approved_deductions)
        + _minor_units(payments_already_made)
    )
    net_minor = (
        opening_minor
        + gross_minor
        + _minor_units(unpaid_approved_reimbursements)
        - deductions_minor
    )
    cash_payout_minor = max(0, net_minor)
    carry_forward_minor = min(0, net_minor)
    return {
        "referrer": referrer,
        "month": month or "all",
        "gross_commission_earned": _rupees(gross_minor),
        "commission_already_received_directly": _rupees(direct_minor),
        "recoverable_company_share": _rupees(company_recoverable_minor),
        "recoverable_advances": int(recoverable_advances or 0),
        "other_approved_deductions": int(other_approved_deductions or 0),
        "unpaid_approved_reimbursements": int(
            unpaid_approved_reimbursements or 0
        ),
        "payments_already_made": int(payments_already_made or 0),
        "opening_balance": int(opening_balance or 0),
        "net_payable": _rupees(net_minor),
        "cash_payout": _rupees(cash_payout_minor),
        "carry_forward_receivable": abs(_rupees(carry_forward_minor)),
        "closing_balance": _rupees(net_minor),
    }
