"""Stable referrer identities and administrator-managed payment accounts.

The legacy application stores referrers as free-text ``candidate.reference``
values.  This module provides stable IDs without replacing that field: current
candidate references remain the source of truth and are merged with a small
materialized registry that preserves IDs, aliases, and account relationships.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from core.config import DATA_DIR


_LOCK = threading.RLock()
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REFERRER_SEED_FILE = os.path.join(
    _PROJECT_ROOT, "config", "referrers.seed.json"
)
_ACCOUNT_SEED_FILE = os.path.join(
    _PROJECT_ROOT, "config", "payment_receiver_accounts.seed.json"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _referrers_file() -> str:
    return os.environ.get(
        "REFERRER_REGISTRY_FILE",
        os.path.join(DATA_DIR, "referrers.json"),
    )


def _accounts_file() -> str:
    return os.environ.get(
        "PAYMENT_RECEIVER_REGISTRY_FILE",
        os.path.join(DATA_DIR, "payment_receiver_accounts.json"),
    )


def normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_upi(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s*@\s*", "@", text).replace(" ", "")


def normalize_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_indian_phone(value: Any) -> str:
    """Return a complete Indian payment phone as +91XXXXXXXXXX.

    Masked, partial, and non-Indian values intentionally return an empty
    string so they can never participate in automatic receiver approval.
    """
    raw = str(value or "").strip()
    if "*" in raw or "x" in raw.lower():
        return ""
    digits = normalize_digits(raw)
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return f"+{digits}"
    return ""


def referrer_id_for_name(name: str) -> str:
    normalized = normalize_name(name)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if slug:
        return f"referrer-{slug}"
    return f"referrer-{hashlib.sha256(normalized.encode()).hexdigest()[:12]}"


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, OSError, ValueError):
        return default


def _atomic_write(path: str, payload: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".registry-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, path)
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def _materialized_referrers() -> list[dict[str, Any]]:
    payload = _read_json(
        _referrers_file(),
        _read_json(_REFERRER_SEED_FILE, {"referrers": []}),
    )
    rows = payload.get("referrers") if isinstance(payload, dict) else payload
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def _dynamic_reference_names() -> list[str]:
    try:
        from features import candidate_store

        return candidate_store.reference_dropdown_names()
    except Exception:
        return []


def list_referrers(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Return current referrers with stable IDs and legacy-name compatibility."""
    materialized = _materialized_referrers()
    by_name: dict[str, dict[str, Any]] = {}
    for row in materialized:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        row.setdefault("id", referrer_id_for_name(name))
        row.setdefault("aliases", [])
        row.setdefault("is_active", True)
        row.setdefault("created_at", "")
        row.setdefault("updated_at", "")
        by_name[normalize_name(name)] = row
        for alias in row.get("aliases") or []:
            if normalize_name(alias):
                by_name.setdefault(normalize_name(alias), row)

    for name in _dynamic_reference_names():
        key = normalize_name(name)
        if key in by_name:
            continue
        row = {
            "id": referrer_id_for_name(name),
            "name": name,
            "aliases": [],
            "is_active": True,
            "source": "candidate_reference",
            "created_at": "",
            "updated_at": "",
        }
        by_name[key] = row

    unique = {str(row["id"]): row for row in by_name.values()}
    rows = list(unique.values())
    if not include_inactive:
        rows = [row for row in rows if bool(row.get("is_active", True))]
    rows.sort(key=lambda row: normalize_name(row.get("name")))
    return rows


def resolve_referrer(value: Any) -> dict[str, Any] | None:
    needle = str(value or "").strip()
    if not needle:
        return None
    normalized = normalize_name(needle)
    for row in list_referrers(include_inactive=True):
        if needle == str(row.get("id") or ""):
            return row
        names = [row.get("name"), *(row.get("aliases") or [])]
        if normalized in {normalize_name(name) for name in names if name}:
            return row
    return None


def materialize_current_referrers(*, actor: str = "migration") -> dict[str, Any]:
    """Persist stable IDs for every current legacy reference without duplicates."""
    with _LOCK:
        now = _now()
        rows = list_referrers(include_inactive=True)
        for row in rows:
            row.setdefault("source", "candidate_reference")
            row.setdefault("created_at", now)
            row["updated_at"] = now
            row.setdefault("created_by", actor)
        _atomic_write(_referrers_file(), {"version": 1, "referrers": rows})
        return {"count": len(rows), "path": _referrers_file()}


def _account_rows() -> list[dict[str, Any]]:
    payload = _read_json(
        _accounts_file(),
        _read_json(_ACCOUNT_SEED_FILE, {"accounts": []}),
    )
    rows = payload.get("accounts") if isinstance(payload, dict) else payload
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def _normalized_account(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["upi_id"] = normalize_upi(result.get("upi_id"))
    result["normalized_upi_id"] = result["upi_id"]
    result["bank_account_identifier"] = normalize_digits(
        result.get("bank_account_identifier")
    )
    phone = normalize_indian_phone(
        result.get("normalized_payment_phone_number")
        or result.get("payment_phone_number")
    )
    result["payment_phone_number"] = phone
    result["normalized_payment_phone_number"] = phone
    result["owner_type"] = str(result.get("owner_type") or "REFERRER").upper()
    result["verification_status"] = str(
        result.get("verification_status") or "UNVERIFIED"
    ).upper()
    result["is_active"] = bool(result.get("is_active", True))
    return result


def _masked(value: str, *, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if "@" in text:
        local, handle = text.split("@", 1)
        return f"{local[:2]}{'*' * max(2, len(local) - 2)}@{handle}"
    return f"{'*' * max(2, len(text) - keep)}{text[-keep:]}"


def public_account(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalized_account(row)
    result["masked_upi_id"] = _masked(result.get("upi_id") or "")
    result["masked_bank_account_identifier"] = _masked(
        result.get("bank_account_identifier") or ""
    )
    result["masked_payment_phone_number"] = _masked(
        result.get("payment_phone_number") or ""
    )
    result.pop("upi_id", None)
    result.pop("normalized_upi_id", None)
    result.pop("bank_account_identifier", None)
    result.pop("payment_phone_number", None)
    result.pop("normalized_payment_phone_number", None)
    return result


def list_payment_accounts(
    *, referrer_id: str | None = None, include_inactive: bool = True
) -> list[dict[str, Any]]:
    rows = [_normalized_account(row) for row in _account_rows()]
    if referrer_id:
        rows = [
            row for row in rows
            if str(row.get("referrer_id") or "") == str(referrer_id)
        ]
    if not include_inactive:
        rows = [row for row in rows if row["is_active"]]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return [public_account(row) for row in rows]


def _validate_owner(row: dict[str, Any]) -> None:
    owner_type = row["owner_type"]
    company_id = str(row.get("company_id") or "")
    referrer_id = str(row.get("referrer_id") or "")
    if owner_type == "COMPANY":
        if not company_id or referrer_id:
            raise ValueError("Company account must belong only to a company.")
    elif owner_type == "REFERRER":
        if not referrer_id or company_id:
            raise ValueError("Referrer account must belong only to a referrer.")
        if resolve_referrer(referrer_id) is None:
            raise ValueError("Referrer was not found in the existing referrer list.")
    else:
        raise ValueError("Invalid payment-account owner type.")
    if not any(
        (
            row.get("upi_id"),
            row.get("bank_account_identifier"),
            row.get("payment_phone_number"),
        )
    ):
        raise ValueError("Add a UPI ID, bank account, or payment phone number.")


def _company_identifiers() -> dict[str, set[str]]:
    """The company's own receiver identifiers, normalised as referrer rows are.

    Imported here rather than at module scope: the payment engine imports both
    registries, and the company side has no reason to know this one exists.
    """
    from features.company_payment_verification import (
        configured_company_account_numbers,
        configured_company_phone_numbers,
        configured_company_upi_ids,
    )

    return {
        "upi_id": {normalize_upi(value) for value in configured_company_upi_ids()},
        "bank_account_identifier": {
            normalize_digits(value) for value in configured_company_account_numbers()
        },
        "normalized_payment_phone_number": {
            normalize_indian_phone(value) for value in configured_company_phone_numbers()
        },
    }


def _assert_unique_identifiers(
    candidate: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    exclude_id: str = "",
) -> None:
    if not candidate.get("is_active"):
        return
    identifiers = {
        "upi_id": candidate.get("upi_id"),
        "bank_account_identifier": candidate.get("bank_account_identifier"),
        "normalized_payment_phone_number": candidate.get(
            "normalized_payment_phone_number"
        ),
    }
    # One identifier, one owner. An account registered to both the company and a
    # referrer is not a duplicate the verifier can resolve: it reads as an
    # ambiguous receiver and refuses the payment, so a receipt paid to a real,
    # approved account stops being creditable the moment the second registration
    # is made. Refusing it here is the only place that is still cheap to fix.
    company = _company_identifiers()
    for field, value in identifiers.items():
        if value and value in company.get(field, set()):
            raise ValueError(
                f"{field.replace('_', ' ').title()} is already registered as a company "
                "receiver identifier. One account belongs to one owner."
            )
    for row in rows:
        normalized = _normalized_account(row)
        if str(normalized.get("id") or "") == exclude_id:
            continue
        if not normalized.get("is_active"):
            continue
        for field, value in identifiers.items():
            if value and value == normalized.get(field):
                raise ValueError(
                    f"{field.replace('_', ' ').title()} already belongs to another active payment account."
                )


def add_payment_account(
    referrer_id: str,
    values: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        referrer = resolve_referrer(referrer_id)
        if referrer is None:
            raise ValueError("Referrer was not found in the existing referrer list.")
        now = _now()
        row = _normalized_account(
            {
                "id": f"receiver-{uuid.uuid4().hex[:16]}",
                "owner_type": "REFERRER",
                "company_id": "",
                "referrer_id": referrer["id"],
                "account_holder_name": str(
                    values.get("account_holder_name") or referrer["name"]
                ).strip(),
                "upi_id": values.get("upi_id"),
                "bank_account_identifier": values.get("bank_account_identifier"),
                "payment_phone_number": values.get("payment_phone_number"),
                "provider_name": str(values.get("provider_name") or "UPI").strip(),
                "verification_status": "UNVERIFIED",
                "is_active": True,
                "valid_from": values.get("valid_from") or "",
                "valid_until": values.get("valid_until") or "",
                "notes": str(values.get("notes") or "").strip(),
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
                "verified_at": "",
                "verified_by": "",
                "history": [
                    {"action": "CREATED", "at": now, "by": actor}
                ],
            }
        )
        _validate_owner(row)
        rows = _account_rows()
        _assert_unique_identifiers(row, rows)
        rows.append(row)
        _atomic_write(_accounts_file(), {"accounts": rows})
        return public_account(row)


def update_payment_account(
    account_id: str,
    values: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    with _LOCK:
        rows = _account_rows()
        index = next(
            (
                i for i, row in enumerate(rows)
                if str(row.get("id") or "") == str(account_id)
            ),
            None,
        )
        if index is None:
            raise ValueError("Payment account was not found.")
        current = _normalized_account(rows[index])
        allowed = {
            "account_holder_name",
            "upi_id",
            "bank_account_identifier",
            "payment_phone_number",
            "provider_name",
            "verification_status",
            "is_active",
            "valid_from",
            "valid_until",
            "notes",
        }
        changed = {
            key: values[key] for key in allowed
            if key in values and values[key] != current.get(key)
        }
        merged = _normalized_account({**current, **changed})
        if merged["verification_status"] not in {"UNVERIFIED", "VERIFIED", "REJECTED"}:
            raise ValueError("Invalid verification status.")
        now = _now()
        if (
            merged["verification_status"] == "VERIFIED"
            and current.get("verification_status") != "VERIFIED"
        ):
            merged["verified_at"] = now
            merged["verified_by"] = actor
        merged["updated_at"] = now
        history = list(current.get("history") or [])
        history.append(
            {
                "action": "UPDATED",
                "at": now,
                "by": actor,
                "changes": sorted(changed),
            }
        )
        merged["history"] = history
        _validate_owner(merged)
        _assert_unique_identifiers(merged, rows, exclude_id=account_id)
        rows[index] = merged
        _atomic_write(_accounts_file(), {"accounts": rows})
        return public_account(merged)


def remove_unverified_payment_account(account_id: str, *, actor: str) -> bool:
    with _LOCK:
        rows = _account_rows()
        row = next(
            (
                item for item in rows
                if str(item.get("id") or "") == str(account_id)
            ),
            None,
        )
        if row is None:
            return False
        if str(row.get("verification_status") or "").upper() != "UNVERIFIED":
            raise ValueError(
                "Verified or rejected accounts must be deactivated to preserve history."
            )
        try:
            from features.payment_verification_engine import ledger_entries

            linked = any(
                str(entry.get("receiver_account_id") or "") == str(account_id)
                for entry in ledger_entries()
            )
        except Exception:
            linked = False
        if linked:
            raise ValueError(
                "This account is linked to financial records and cannot be removed."
            )
        remaining = [
            item for item in rows
            if str(item.get("id") or "") != str(account_id)
        ]
        _atomic_write(_accounts_file(), {"accounts": remaining})
        return True
