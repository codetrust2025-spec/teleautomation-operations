"""Backward-compatible facade for central receiver-registry verification.

New upload paths use :mod:`features.payment_verification_engine`.  This module
keeps older imports working, but no longer applies the removed company-only
rule: an exact active verified referrer account is also a valid receiver.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable


SUCCESS_STATUSES = {"success", "successful", "completed", "complete", "paid"}
# Real values are configured in production via the smart-reply config / env;
# these are placeholder fallbacks only (never commit real payment identifiers).
DEFAULT_COMPANY_UPI_ID = os.environ.get("COMPANY_UPI_ID", "company@upi")


def _normalise_upi(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _normalise_phone(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else digits


def configured_company_upi_ids() -> set[str]:
    """Return the UPI IDs that are allowed to receive company payments."""
    values: list[str] = []
    values.extend(os.environ.get("COMPANY_PAYMENT_UPI_IDS", "").split(","))
    configured = {value for raw in values if (value := _normalise_upi(raw))}
    return configured or {DEFAULT_COMPANY_UPI_ID}


def configured_company_phone_numbers() -> set[str]:
    """Return phone-number aliases that identify the company payee.

    Only numbers that are actually configured. This used to fall back to a
    placeholder, 9000000001, whenever COMPANY_PAYMENT_PHONE_NUMBERS was empty --
    as it is in production -- and the receiver registry then carried a number
    that is not the company's as a verified company receiver: a receipt paid to
    it was credited as a company payment. No production payment, proof or
    record was ever matched through it, so nothing depended on it.

    With no phone configured the company is identified by its UPI IDs and
    account numbers alone. COMPANY_PAYMENT_PHONE, the older single-number
    setting, is still honoured exactly as before: when it is set and
    COMPANY_PAYMENT_PHONE_NUMBERS is not.
    """
    values: list[str] = []
    values.extend(os.environ.get("COMPANY_PAYMENT_PHONE_NUMBERS", "").split(","))
    configured = {value for raw in values if (value := _normalise_phone(raw))}
    if configured:
        return configured
    legacy = _normalise_phone(os.environ.get("COMPANY_PAYMENT_PHONE", ""))
    return {legacy} if legacy else set()


def configured_company_account_numbers() -> set[str]:
    """Return bank-account aliases allowed to identify the company payee."""
    return {
        digits
        for raw in os.environ.get("COMPANY_PAYMENT_ACCOUNT_NUMBERS", "").split(",")
        if (digits := "".join(ch for ch in raw if ch.isdigit()))
    }


def verify_company_payment(
    extraction: dict[str, Any] | None,
    amount_due: int,
    *,
    accepted_upi_ids: Iterable[str] | None = None,
    accepted_phone_numbers: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed, registry-backed verdict for legacy callers."""
    details = dict(extraction or {})
    accepted = {
        value
        for raw in (accepted_upi_ids if accepted_upi_ids is not None else configured_company_upi_ids())
        if (value := _normalise_upi(raw))
    }
    accepted_phones = {
        value
        for raw in (
            accepted_phone_numbers
            if accepted_phone_numbers is not None
            else configured_company_phone_numbers()
        )
        if (value := _normalise_phone(raw))
    }
    receiver_upi = _normalise_upi(details.get("receiver_upi_id"))
    receiver_phone = _normalise_phone(details.get("receiver_phone"))
    if not receiver_phone and receiver_upi and "@" not in receiver_upi:
        receiver_phone = _normalise_phone(receiver_upi)
    receiver_name = str(details.get("receiver_name") or "").strip()
    status = str(details.get("status") or "").strip().lower()
    amount = int(details.get("amount") or 0)
    reference = str(
        details.get("utr_number")
        or details.get("reference_number")
        or details.get("transaction_id")
        or ""
    ).strip()

    upi_matches = bool(receiver_upi and receiver_upi in accepted)
    phone_matches = bool(accepted_phones and receiver_phone in accepted_phones)
    registry_match: dict[str, Any] = {}
    if not upi_matches and not phone_matches:
        from features.payment_verification_engine import classify_receiver

        registry_match = classify_receiver(details)
    receiver_type = (
        "company"
        if upi_matches or phone_matches
        else str(registry_match.get("receiver_type") or "unknown")
    )
    stable_registry_match = bool(
        registry_match.get("receiver_match") in {"upi", "phone", "account"}
        and registry_match.get("receiver_account_active")
        and registry_match.get("receiver_account_verified")
        and not registry_match.get("receiver_match_ambiguous")
    )

    reasons: list[str] = []
    if not details.get("is_payment_screenshot"):
        reasons.append("This is not a valid payment receipt.")
    if not accepted and not accepted_phones:
        reasons.append("The company payment account is not configured. Contact your coordinator.")
    elif not upi_matches and not phone_matches and not receiver_upi and not receiver_phone:
        reasons.append(
            "The receiving UPI ID or phone number is not visible. "
            "Upload the full receipt showing who was paid."
        )
    elif not upi_matches and not phone_matches and not stable_registry_match:
        reasons.append(
            "The receiver does not exactly match an active verified company "
            "or registered referrer payment account."
        )
    if status not in SUCCESS_STATUSES:
        reasons.append("Only a successful, completed transaction can be used to book a slot.")
    if amount <= 0:
        reasons.append("The payment amount could not be verified.")
    elif amount_due > 0 and amount < amount_due:
        reasons.append(
            f"₹{amount:,} was detected, but the full ₹{amount_due:,} company payment is required."
        )

    return {
        "verified": not reasons,
        "reasons": reasons,
        "receiver_name": receiver_name,
        "receiver_upi_id": receiver_upi,
        "receiver_phone": receiver_phone,
        "receiver_type": receiver_type,
        "verification_state": (
            "VERIFIED_COMPANY_PAYMENT"
            if not reasons and receiver_type == "company"
            else "VERIFIED_REFERRER_PAYMENT"
            if not reasons and receiver_type == "referrer"
            else "PENDING_MANUAL_REVIEW"
        ),
        "amount": amount,
        "status": status,
        "transaction_reference": reference,
        "accepted_company_upi_ids": sorted(accepted),
        "accepted_company_phone_numbers": sorted(accepted_phones),
    }


def stored_proof_is_verified_company_payment(entry: dict[str, Any]) -> bool:
    """Re-check a saved proof before it can unlock a slot booking."""
    if not entry.get("company_payment_verified"):
        return False
    receiver_upi = _normalise_upi(entry.get("receiver_upi_id"))
    receiver_phone = _normalise_phone(entry.get("receiver_phone"))
    receiver_account = "".join(ch for ch in str(entry.get("receiver_account") or "") if ch.isdigit())
    return bool(
        (receiver_upi and receiver_upi in configured_company_upi_ids())
        or (receiver_phone and receiver_phone in configured_company_phone_numbers())
        or (
            receiver_account
            and any(
                receiver_account == account
                or (len(receiver_account) >= 4 and account.endswith(receiver_account[-4:]))
                for account in configured_company_account_numbers()
            )
        )
    )
