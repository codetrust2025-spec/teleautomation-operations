"""Persistent ledger of expenses incurred for a handler / reference
(the person who refers candidates — e.g. Thrilok, Venugopal, Referrer One).

This is intentionally separate from the per-candidate `expenses` free-text
field. Those were one-off operator notes ("12000 gym"). This new ledger is
a structured list with amount + category + date so we can:
  - aggregate spend per handler,
  - filter by month,
  - compute net contribution = revenue_completed - expenses_total.

Schema (one row):

    {
        "id":         "short id",
        "reference":  "handler / referrer name (case-insensitive match)",
        "amount":     <int rupees>,
        "category":   "commission | travel | food | gym | equipment | "
                      "marketing | software | other",
        "note":       "free text",
        "date":       "YYYY-MM-DD",
        "created_at": ISO ts,
        "updated_at": ISO ts,
    }

Stored as JSON-on-disk under `data/handler_expenses.json`, same pattern
as the rest of the project.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

from core.config import DATA_DIR
from features import transaction_identity

_FILE = os.path.join(DATA_DIR, "handler_expenses.json")
PROOFS_DIR = os.path.join(DATA_DIR, "handler_expense_proofs")
_lock = Lock()

MAX_PROOF_BYTES = 8 * 1024 * 1024  # 8 MB per screenshot

_ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/jpg":  "jpg",
    "image/png":  "png",
    "image/webp": "webp",
    "image/gif":  "gif",
    "image/heic": "heic",
    "image/heif": "heif",
}

VALID_CATEGORIES = {
    "commission", "travel", "food", "gym",
    "equipment", "marketing", "software", "other",
}

CATEGORY_LABELS = {
    "commission":  "Commission / referral fee",
    "travel":      "Travel / fuel",
    "food":        "Food / meals",
    "gym":         "Gym / health",
    "equipment":   "Equipment",
    "marketing":   "Marketing",
    "software":    "Software / tools",
    "other":       "Other",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _empty() -> dict:
    return {"expenses": [], "updated_at": None}


def _load() -> dict:
    with _lock:
        if not os.path.exists(_FILE):
            return _empty()
        try:
            with open(_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return _empty()
            data.setdefault("expenses", [])
            data.setdefault("updated_at", None)
            return data
        except (OSError, json.JSONDecodeError):
            return _empty()


def store_available() -> bool:
    """Is the payout store actually readable?

    `_load()` returns an empty store for both "no payouts recorded" and "the
    file is gone", which is safe for listing but dangerous for accounting: a
    balance computed without payouts looks confident and is wrong. Callers that
    subtract payouts must consult this first and refuse to publish a figure
    when it is False.
    """
    if not os.path.exists(_FILE):
        return False
    try:
        with open(_FILE, encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except (OSError, json.JSONDecodeError):
        return False


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    data["updated_at"] = _now_iso()
    with _lock:
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _FILE)


def _coerce_amount(value) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    s = str(value).strip().replace("₹", "").replace(",", "").replace(" ", "")
    if not s or s.lower() in {"nan", "-"}:
        return 0
    try:
        return max(0, int(float(s)))
    except ValueError:
        return 0


def _clean_str(value, *, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s or default


def _row_month(row: dict) -> str:
    raw = (row.get("date") or "").strip()
    if not raw:
        return ""
    if len(raw) >= 7 and raw[4] == "-":
        return raw[:7]
    return ""


_ALLOWED_FIELDS = {
    "reference", "amount", "category", "note", "date",
    # Identity of the underlying bank transaction, so an expense can be matched
    # against a recovery or payout recorded elsewhere for the same money.
    "external_transaction_id", "payer",
}


# A candidate payment received in a referrer's own account is a recovery, not
# an expense. When one has been filed as an expense it is reclassified rather
# than deleted, so the audit trail survives.
VOID_STATUSES = frozenset({"VOIDED_DUPLICATE", "RECLASSIFIED_TO_RECOVERY"})


def _normalise(record: dict, *, existing: dict | None = None) -> dict:
    base = dict(existing) if existing else {}
    category = _clean_str(
        record.get("category", base.get("category", "other"))
    ).lower().replace(" ", "_").replace("-", "_")
    if category not in VALID_CATEGORIES:
        category = "other"
    out = {
        "id":         base.get("id") or _new_id(),
        "reference":  _clean_str(record.get("reference", base.get("reference"))),
        "amount":     _coerce_amount(record.get("amount", base.get("amount"))),
        "category":   category,
        "note":       _clean_str(record.get("note", base.get("note")))[:240],
        "date":       _clean_str(record.get("date", base.get("date"))),
        "proofs":     base.get("proofs") or [],
        "created_at": base.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
    }
    out["external_transaction_id"] = transaction_identity.normalize_external_id(
        record.get("external_transaction_id", base.get("external_transaction_id"))
    )
    # Who the money came from. For a reimbursed expense that is the company;
    # for a misfiled candidate payment it is the candidate, which is what makes
    # the duplicate detectable against the recovery.
    out["payer"] = _clean_str(record.get("payer", base.get("payer")))[:160]
    # Hash of the first proof image, carried forward across edits.
    out["screenshot_hash"] = _clean_str(base.get("screenshot_hash"))
    # Void state. An expense is never deleted when it turns out to be a
    # duplicate or a misfiling: it keeps its row, its proof and its timestamps,
    # and simply stops counting as money paid out.
    void_status = _clean_str(record.get("void_status", base.get("void_status"))).upper()
    if void_status in VOID_STATUSES:
        out["void_status"] = void_status
        out["void_reason"] = _clean_str(
            record.get("void_reason", base.get("void_reason"))
        )[:240]
        out["voided_at"] = base.get("voided_at") or _now_iso()
        out["voided_by"] = _clean_str(record.get("voided_by", base.get("voided_by")))[:120]
        out["voided_ledger_ref"] = _clean_str(
            record.get("voided_ledger_ref", base.get("voided_ledger_ref"))
        )[:64]
    return out


def is_voided(row: dict) -> bool:
    """Voided rows stay in the file for audit but never count as money paid."""
    return _clean_str((row or {}).get("void_status")).upper() in VOID_STATUSES


# ── Public API ──────────────────────────────────────────────────────────────

def list_expenses(
    *,
    reference: str | None = None,
    month: str | None = None,
    include_voided: bool = False,
) -> list[dict]:
    """Most-recent first. Filter by reference (case-insensitive exact match)
    and/or by month ('YYYY-MM').

    Voided rows are excluded by default: every caller that sums this list is
    computing money paid out. Pass include_voided=True for audit views.
    """
    rows = list((_load().get("expenses") or []))
    if not include_voided:
        rows = [r for r in rows if not is_voided(r)]
    if reference:
        ref_lc = reference.strip().lower()
        rows = [r for r in rows if (r.get("reference") or "").strip().lower() == ref_lc]
    if month and month != "all":
        rows = [r for r in rows if _row_month(r) == month]
    rows.sort(key=lambda r: (r.get("date") or "", r.get("created_at") or ""), reverse=True)
    return rows


def proof_hashes(row: dict) -> list[str]:
    """sha256 of every proof image on this expense, newest capture last.

    Proofs uploaded before hashing was introduced carry no stored digest, so
    those are read off disk. An unreadable file is skipped rather than raised:
    a missing image must not stop a reconciliation scan.
    """
    digests: list[str] = []
    stored = _clean_str(row.get("screenshot_hash"))
    if stored:
        digests.append(stored)
    for proof in (row.get("proofs") or []):
        digest = _clean_str(proof.get("sha256"))
        if not digest:
            path = os.path.join(_proof_dir(str(row.get("id") or "")), proof.get("filename", ""))
            try:
                with open(path, "rb") as handle:
                    digest = transaction_identity.screenshot_hash(handle.read())
            except OSError:
                continue
        if digest and digest not in digests:
            digests.append(digest)
    return digests


def as_transaction(row: dict) -> dict:
    """This expense in the shape the cross-module duplicate scan compares.

    The handler is the receiver: an expense row records money that ended up
    with them, whoever actually sent it.
    """
    return {
        "record_id": row.get("id"),
        "kind": "expense",
        "source_module": "handler_expenses",
        "amount": row.get("amount"),
        "date": row.get("date"),
        "payer": row.get("payer"),
        "receiver": row.get("reference"),
        "handler": row.get("reference"),
        "external_transaction_id": row.get("external_transaction_id"),
        "screenshot_hash": row.get("screenshot_hash"),
        "voided": is_voided(row),
        "created_at": row.get("created_at"),
        "note": row.get("note"),
    }


def create_expense(record: dict) -> dict:
    """Add an expense, refusing one that double-counts money already recorded.

    A handler expense is the last step in most money flows, so it is where the
    same transaction most often lands twice — typically a candidate payment
    that the engine already posted as a recovery, re-entered by hand from the
    same screenshot.
    """
    data = _load()
    row = _normalise(record)

    # What reading the receipt just established, which the new row cannot show
    # yet: the payment the engine matched it to and the image's own hash.
    receipt_payment = _clean_str(record.get("payment_id"))
    receipt_hash = _clean_str(record.get("screenshot_hash"))
    refuse_duplicate(_transaction_of(row, payment_id=receipt_payment, screenshot_hash=receipt_hash), row["id"])
    if receipt_hash:
        row["screenshot_hash"] = receipt_hash

    data.setdefault("expenses", []).append(row)
    _save(data)
    return row


def check_new_expense(record: dict) -> None:
    """Raise DuplicateTransactionError if saving `record` now would double-count
    money already recorded. Saves nothing: it is the check create_expense makes,
    available earlier so a receipt that is already recorded is refused when it is
    attached, not after the operator has confirmed it.
    """
    row = _normalise(record)
    refuse_duplicate(
        _transaction_of(
            row,
            payment_id=_clean_str(record.get("payment_id")),
            screenshot_hash=_clean_str(record.get("screenshot_hash")),
        ),
        row["id"],
    )


def _transaction_of(
    row: dict, *, payment_id: str = "", screenshot_hash: str = "", reference: str = ""
) -> dict:
    """This expense as the duplicate check compares it, plus what is new.

    `payment_id`, `screenshot_hash` and `reference` describe a receipt that has
    been read but is not stored on the row yet.
    """
    from features import financial_reconciliation

    try:
        tx = financial_reconciliation.expense_transaction(row)
    except Exception:
        # The payment engine's ledger could not be read. Compare on what the
        # row itself carries rather than not at all.
        tx = financial_reconciliation.canonical_transaction(as_transaction(row))
        tx["screenshot_hashes"] = proof_hashes(row)
    if payment_id and not tx.get("payment_id"):
        tx["payment_id"] = payment_id
    if screenshot_hash:
        tx["screenshot_hashes"] = sorted({*(tx.get("screenshot_hashes") or []), screenshot_hash})
        tx["screenshot_hash"] = tx.get("screenshot_hash") or screenshot_hash
    if reference:
        # Listed ahead of `external_transaction_id`, so it is the one compared.
        tx["utr"] = reference
    return tx


def _recorded_elsewhere(exclude_id: str) -> list[dict]:
    """Every record this expense could double-count.

    The cross-module scan skips a source it cannot read, which suits a report.
    A check that goes quiet because one store would not load lets the same
    payment through, so this store's own rows are always compared as well.
    """
    from features import financial_reconciliation

    rows = list(financial_reconciliation.collect_transactions(exclude_expense_id=exclude_id))
    seen = {str(r.get("record_id")) for r in rows if r.get("source_module") == "handler_expenses"}
    for stored in (_load().get("expenses") or []):
        if stored.get("id") == exclude_id or str(stored.get("id")) in seen:
            continue
        tx = financial_reconciliation.canonical_transaction(as_transaction(stored))
        tx["screenshot_hashes"] = proof_hashes(stored)
        rows.append(tx)
    return rows


def refuse_duplicate(incoming: dict, exclude_id: str, *, before: dict | None = None) -> None:
    """Raise DuplicateTransactionError when `incoming` double-counts a record.

    `before` is the stored row an edit started from. A pair that already
    duplicated each other before the edit stays editable -- a note can still be
    changed -- because the edit did not create the duplicate; the edit may not
    create a new one.
    """
    clashes = transaction_identity.duplicates_of(incoming, _recorded_elsewhere(exclude_id))
    if before is not None:
        # Every clash must have been there before the edit. Judging only the
        # first would let an old duplicate hide a new one.
        was = _transaction_of(before)
        clashes = [c for c in clashes if transaction_identity.duplicate_of(was, [c]) is None]
    if clashes:
        raise transaction_identity.DuplicateTransactionError(
            transaction_identity.duplicate_message(clashes[0]), existing=clashes[0]
        )


def refuse_duplicate_proof(
    eid: str, *, screenshot_hash: str, payment_id: str = "", reference: str = ""
) -> None:
    """A screenshot attached to an existing expense must not be the receipt of
    a payment that is already recorded elsewhere.

    The edit flow's twin of the check made when an expense is created: a second
    expense could otherwise be given the first one's receipt after the fact.
    """
    row = next((r for r in (_load().get("expenses") or []) if r.get("id") == eid), None)
    if row is None or is_voided(row):
        return
    incoming = _transaction_of(
        row, payment_id=payment_id, screenshot_hash=screenshot_hash, reference=reference
    )
    refuse_duplicate(incoming, eid, before=row)


def void_expense(
    eid: str,
    *,
    status: str,
    reason: str,
    actor: str = "",
    ledger_ref: str = "",
) -> dict | None:
    """Stop an expense counting as money paid, without losing the record.

    Used when an expense turns out to duplicate another entry or to have been
    the wrong classification for the money. The row, its proofs and its
    timestamps all survive; only its effect on the balance goes away.
    """
    normalized = _clean_str(status).upper()
    if normalized not in VOID_STATUSES:
        raise ValueError(f"Unknown void status: {status!r}")
    if not _clean_str(reason):
        raise ValueError("A void needs a reason so the audit trail explains itself")
    data = _load()
    rows = data.get("expenses") or []
    for i, r in enumerate(rows):
        if r.get("id") != eid:
            continue
        if is_voided(r):
            return rows[i]
        rows[i] = {
            **r,
            "void_status": normalized,
            "void_reason": _clean_str(reason)[:240],
            "voided_at": _now_iso(),
            "voided_by": _clean_str(actor)[:120],
            "voided_ledger_ref": _clean_str(ledger_ref)[:64],
            "updated_at": _now_iso(),
        }
        data["expenses"] = rows
        _save(data)
        return rows[i]
    return None


def update_expense(eid: str, patch: dict) -> dict | None:
    data = _load()
    rows = data.get("expenses") or []
    for i, r in enumerate(rows):
        if r.get("id") == eid:
            allowed = {k: v for k, v in patch.items() if k in _ALLOWED_FIELDS}
            updated = _normalise(allowed, existing=r)
            # An edit may not turn this expense into a copy of another one.
            if not is_voided(updated):
                refuse_duplicate(_transaction_of(updated), eid, before=r)
            rows[i] = updated
            data["expenses"] = rows
            _save(data)
            return rows[i]
    return None


def delete_expense(eid: str) -> bool:
    data = _load()
    before = data.get("expenses") or []
    after = [r for r in before if r.get("id") != eid]
    if len(after) == len(before):
        return False
    data["expenses"] = after
    _save(data)
    return True


def summary_by_handler(month: str | None = None) -> dict[str, dict]:
    """Return `{reference_lowercase: {name, total, count, by_category}}`.

    The ledger no longer cares whether a row is "earning" or "deduction" —
    every entry represents money the operator has ALREADY PAID OUT for
    the handler (their commission disbursement, their fuel, their food,
    etc.). The handler's owed-amount is auto-computed elsewhere from the
    50% rule. Net payable to the handler = owed − total of this ledger.

    The lowercase key lets the candidates module match against its own
    `reference` field without worrying about casing. The display name in
    `name` is taken from the most-recently-seen casing (since operators
    sometimes type 'Thrilok' / 'THRILOK' / 'thrilok' interchangeably)."""
    rows = list_expenses(month=month)
    out: dict[str, dict] = {}
    for r in rows:
        ref = (r.get("reference") or "").strip()
        if not ref:
            continue
        key = ref.lower()
        bucket = out.setdefault(key, {
            "name": ref, "total": 0, "count": 0, "by_category": {},
        })
        amount = int(r.get("amount") or 0)
        bucket["total"] += amount
        bucket["count"] += 1
        cat = r.get("category") or "other"
        bucket["by_category"][cat] = bucket["by_category"].get(cat, 0) + amount
        bucket["name"] = ref
    return out


def total_for_handler(reference: str, *, month: str | None = None) -> int:
    """Convenience: how many rupees was spent on this handler in the
    given month (or all-time if `month` is None/'all')."""
    if not reference:
        return 0
    return sum(int(r.get("amount") or 0) for r in list_expenses(
        reference=reference, month=month,
    ))


def available_months() -> list[dict]:
    """For the modal's filter dropdown — same shape as candidate_store
    so the frontend can reuse the renderer."""
    rows = _load().get("expenses") or []
    counts: dict[str, int] = {}
    for r in rows:
        m = _row_month(r)
        if not m:
            continue
        counts[m] = counts.get(m, 0) + 1
    today = datetime.now(timezone.utc)
    current = today.strftime("%Y-%m")
    counts.setdefault(current, 0)
    sorted_months = sorted(counts.keys(), reverse=True)
    month_names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    out = []
    for m in sorted_months:
        try:
            y, mo = m.split("-")
            label = f"{month_names[int(mo) - 1]} {y}"
        except (ValueError, IndexError):
            label = m
        out.append({
            "value": m, "label": label, "count": counts[m],
            "is_current": m == current,
        })
    return out


# ── Payment-proof helpers for handler expenses ──────────────────────────────


def _proof_dir(eid: str) -> str:
    """Each expense gets its own folder for screenshots."""
    return os.path.join(PROOFS_DIR, eid)


def _ext_from_mime(mime: str, fallback_name: str = "") -> str:
    mime = (mime or "").lower().split(";")[0].strip()
    if mime in _ALLOWED_MIME:
        return _ALLOWED_MIME[mime]
    if fallback_name and "." in fallback_name:
        ext = fallback_name.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png", "webp", "gif", "heic", "heif"}:
            return "jpeg" if ext == "jpeg" else ext
    return ""


def add_proof(eid: str, *, data: bytes, original_name: str, mime_type: str,
              note: str = "") -> dict | None:
    """Persist a payment screenshot for expense `eid`. Returns the new proof
    entry or None when the expense doesn't exist. Raises ValueError on
    rejection (wrong mime, too big, empty)."""
    if not data:
        raise ValueError("Empty upload")
    if len(data) > MAX_PROOF_BYTES:
        raise ValueError(f"File too large (max {MAX_PROOF_BYTES // (1024*1024)} MB)")
    ext = _ext_from_mime(mime_type, original_name)
    if not ext:
        raise ValueError("Only image files (jpg / png / webp / gif / heic) are allowed")

    store = _load()
    rows = store.get("expenses") or []
    idx = next((i for i, r in enumerate(rows) if r.get("id") == eid), -1)
    if idx < 0:
        return None

    pid = uuid.uuid4().hex[:12]
    filename = f"{pid}.{ext}"
    folder = _proof_dir(eid)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

    digest = transaction_identity.screenshot_hash(data)
    entry = {
        "id":            pid,
        "filename":      filename,
        "original_name": (original_name or filename)[:160],
        "mime_type":     mime_type or f"image/{ext}",
        "size":          len(data),
        "sha256":        digest,
        "note":          _clean_str(note)[:200],
        "uploaded_at":   _now_iso(),
        "url":           f"/handler-expenses/{eid}/proofs/{pid}",
    }
    proofs = list(rows[idx].get("proofs") or [])
    proofs.append(entry)
    rows[idx]["proofs"] = proofs
    # The first proof is the one that documents the transaction; later uploads
    # are usually supporting context, so they do not redefine its identity.
    if not rows[idx].get("screenshot_hash"):
        rows[idx]["screenshot_hash"] = digest
    rows[idx]["updated_at"] = _now_iso()
    store["expenses"] = rows
    _save(store)
    return entry


def get_proof(eid: str, pid: str) -> tuple[str, dict] | None:
    """Locate the proof on-disk path + metadata. Returns (path, entry) or None."""
    for r in (_load().get("expenses") or []):
        if r.get("id") != eid:
            continue
        for p in (r.get("proofs") or []):
            if p.get("id") == pid:
                path = os.path.join(_proof_dir(eid), p["filename"])
                if not os.path.exists(path):
                    return None
                return path, p
    return None


def delete_proof(eid: str, pid: str) -> bool:
    """Remove a proof from an expense. Returns True on success."""
    store = _load()
    rows = store.get("expenses") or []
    for row in rows:
        if row.get("id") != eid:
            continue
        proofs = list(row.get("proofs") or [])
        for i, p in enumerate(proofs):
            if p.get("id") == pid:
                path = os.path.join(_proof_dir(eid), p["filename"])
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass
                proofs.pop(i)
                row["proofs"] = proofs
                row["updated_at"] = _now_iso()
                store["expenses"] = rows
                _save(store)
                return True
    return False
