"""Operations expense, payout and salary routes."""
import asyncio
import logging
import threading
import time
import uuid
from collections import OrderedDict
from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from core import ai_activity
from features import receipt_amount, transaction_identity
from core.operations_api_helpers import require_admin as _require_fleet_admin
from core.operations_api_helpers import require_payroll_admin as _require_payroll_admin

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/handler-expenses")
async def handler_expenses_list(
    request: Request,
    reference: str | None = Query(default=None),
    month: str | None = Query(default=None),
):
    from core.dashboard_access import handler_payout_reference_scope
    from features import handler_expenses

    reference = handler_payout_reference_scope(request, reference)
    rows = handler_expenses.list_expenses(reference=reference, month=month)
    total = sum(int(r.get("amount") or 0) for r in rows)
    # Merge months from handler expenses + candidates for complete dropdown
    months_set = {m["value"] for m in handler_expenses.available_months()}
    try:
        from features import candidate_store
        for m in candidate_store.available_months():
            if isinstance(m, dict):
                months_set.add(m["value"])
            else:
                months_set.add(m)
    except Exception:
        pass
    month_names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    all_months = []
    for m in sorted(months_set, reverse=True):
        try:
            y, mo = m.split("-")
            label = f"{month_names[int(mo) - 1]} {y}"
        except (ValueError, IndexError):
            label = m
        all_months.append({"value": m, "label": label})
    return {
        "status": "ok",
        "expenses": rows,
        "count": len(rows),
        "total": total,
        "categories": handler_expenses.CATEGORY_LABELS,
        "available_months": all_months,
    }
@router.post("/handler-expenses", dependencies=[Depends(_require_fleet_admin)])
async def handler_expenses_create(
    reference: str = Form(...),
    amount: str = Form(default=""),
    category: str = Form(default="commission"),
    note: str = Form(default=""),
    date: str = Form(default=""),
    file: UploadFile = File(...),
    analysis_id: str = Form(default=""),
):
    # The screenshot is verified by an AI node before the expense exists;
    # `analysis_id` lets the dashboard follow which one. The amount saved is the
    # one read off the screenshot: `amount`, when sent, is the figure the
    # operator confirmed and must equal it.
    with ai_activity.analysis(analysis_id, kind=ai_activity.PAYMENT_ANALYSIS) as analysis:
        response = await _create_expense(reference, amount, category, note, date, file)
    return ai_activity.with_analysis(response, analysis)


def _receipt_reference(verification: dict) -> str:
    """The bank / UPI reference the verifier read off a receipt."""
    return str(
        verification.get("utr_number")
        or verification.get("transaction_id")
        or verification.get("reference_number")
        or ""
    )


def _duplicate_response(exc: transaction_identity.DuplicateTransactionError) -> dict:
    """What the dashboard is told when a save would record a payment twice."""
    return {
        "status": "error",
        "message": str(exc),
        "duplicate_of": {
            "record_id": exc.existing.get("record_id"),
            "kind": exc.existing.get("kind"),
            "source_module": exc.existing.get("source_module"),
            "date": exc.existing.get("date"),
            "amount": exc.existing.get("amount"),
            "matched_on": exc.existing.get("matched_on"),
        },
    }


async def _receipt_upload(file: UploadFile):
    """The uploaded receipt's bytes and type, or the refusal to return."""
    from features import handler_expenses

    raw = await file.read()
    if not raw:
        return None, "", {"status": "error", "message": "Payment screenshot is required"}
    if len(raw) > handler_expenses.MAX_PROOF_BYTES:
        return None, "", {"status": "error", "message": f"File too large (max {handler_expenses.MAX_PROOF_BYTES // (1024*1024)} MB)"}
    mime = (file.content_type or "").lower().split(";")[0].strip()
    if not handler_expenses._ext_from_mime(mime, file.filename or ""):
        return None, "", {"status": "error", "message": "Only image files (jpg / png / webp / gif / heic) are allowed"}
    return raw, mime, None


async def _read_receipt(raw: bytes, mime: str, *, referrer: dict, category: str, create_ledger: bool, cache: str):
    """Verify the screenshot as a payout to this referrer, with the amount left for the screenshot to decide."""
    from features.payment_verification_engine import verify_payment_screenshot

    name = str(referrer.get("name") or "").strip()
    return await asyncio.to_thread(
        verify_payment_screenshot,
        raw,
        mime or "image/jpeg",
        source_module="handler_expense_create",
        expected_amount=0,
        entity_name=name,
        referrer_hint=name,
        referrer_id=str(referrer.get("id") or ""),
        purpose=(
            "handler_payout"
            if category.strip().lower() == "commission"
            else "expense_reimbursement"
        ),
        create_ledger=create_ledger,
        extraction_cache=cache,
    )


def _entered_amount(value: str):
    """The amount a client sent, or None when it sent none."""
    text = str(value or "").strip()
    if not text:
        return None
    return int(float(text))


def _incoming_expense(reference: str, amount: int, category: str, note: str, date: str, raw: bytes, verification: dict) -> dict:
    """The expense a receipt describes, as the duplicate check compares it."""
    return {
        "reference": reference,
        "amount": amount,
        "category": category,
        "note": note.strip(),
        "date": date,
        # What the verifier read off this receipt: the transaction reference, the
        # payer, the payment the engine matched it to, and the image's own hash.
        "external_transaction_id": _receipt_reference(verification),
        "payment_id": str(verification.get("payment_id") or ""),
        "screenshot_hash": transaction_identity.screenshot_hash(raw),
        "payer": verification.get("sender_name") or verification.get("sender_upi_id") or "",
    }


# Reading a screenshot takes about a minute on the AI node -- as long as the proxy in
# front of this server will wait -- so the request that starts a reading is not the
# one that answers it. It returns at once with an id, the server reads in the
# background, and the dashboard collects the answer. Held in memory, like the AI
# activity the dashboard follows, and forgotten after a while.
_READ_TTL_SECONDS = 30 * 60
_READ_LIMIT = 64
_reads: "OrderedDict[str, dict]" = OrderedDict()
_reads_lock = threading.Lock()


def forget_reads() -> None:
    with _reads_lock:
        _reads.clear()


def _begin_read(preferred: str = "") -> str:
    """Register a reading as pending and return its id (the dashboard's own, when it sent a usable one)."""
    now = time.monotonic()
    with _reads_lock:
        for stale in [rid for rid, entry in _reads.items() if now - entry["at"] > _READ_TTL_SECONDS]:
            del _reads[stale]
        read_id = preferred if ai_activity.valid_analysis_id(preferred) and preferred not in _reads else uuid.uuid4().hex
        _reads[read_id] = {"state": "pending", "response": None, "at": now}
        while len(_reads) > _READ_LIMIT:
            _reads.popitem(last=False)
    return read_id


def _finish_read(read_id: str, response: dict) -> None:
    with _reads_lock:
        entry = _reads.get(read_id)
        if entry is not None:
            entry.update(state="done", response=response, at=time.monotonic())


def _read_entry(read_id: str):
    now = time.monotonic()
    with _reads_lock:
        entry = _reads.get(read_id)
        if entry is None or now - entry["at"] > _READ_TTL_SECONDS:
            _reads.pop(read_id, None)
            return None
        return dict(entry)


@router.post("/handler-expenses/extract", dependencies=[Depends(_require_fleet_admin)])
async def handler_expenses_extract(
    background: BackgroundTasks,
    reference: str = Form(...),
    file: UploadFile = File(...),
    category: str = Form(default="commission"),
    date: str = Form(default=""),
    fresh: str = Form(default=""),
    analysis_id: str = Form(default=""),
):
    """Start reading the amount off a payment screenshot. Saves nothing.

    The dashboard calls this when a screenshot is attached. It answers at once --
    with `{"status": "pending", "read_id": ...}`, or with an error when the request
    itself is unusable (no such referrer, not an image) -- and the amount is
    collected from `GET /handler-expenses/extract/{read_id}`. `fresh` makes a newly
    attached screenshot be read again; without it a reading the server still
    remembers is reused.
    """
    from features.referrer_registry import resolve_referrer

    referrer = resolve_referrer(reference)
    if referrer is None:
        return {"status": "error", "message": "Select one registered referrer before attaching the screenshot."}
    raw, mime, refusal = await _receipt_upload(file)
    if refusal:
        return refusal
    read_id = _begin_read(analysis_id)
    background.add_task(
        _run_read, read_id, referrer, category, date, raw, mime,
        str(fresh).strip().lower() in {"1", "true", "yes"},
    )
    return {"status": "pending", "read_id": read_id}


@router.get("/handler-expenses/extract/{read_id}", dependencies=[Depends(_require_fleet_admin)])
async def handler_expenses_extract_result(read_id: str):
    """The answer to a reading: `pending` until it is finished, then the amount or the reason there is none."""
    entry = _read_entry(read_id)
    if entry is None:
        return {"status": "error", "message": "This reading is no longer available. Attach the screenshot again."}
    if entry["state"] == "pending":
        return {"status": "pending"}
    return entry["response"]


async def _run_read(read_id: str, referrer: dict, category: str, date: str, raw: bytes, mime: str, fresh: bool):
    """Carry out a reading and keep its answer for the dashboard to collect."""
    try:
        with ai_activity.analysis(read_id, kind=ai_activity.PAYMENT_ANALYSIS) as analysis:
            response = await _read_expense_amount(referrer, category, date, raw, mime, fresh=fresh)
        response = ai_activity.with_analysis(response, analysis)
    except Exception:
        logger.exception("Reading a handler expense screenshot failed")
        response = {"status": "error", "message": "The screenshot could not be read."}
    _finish_read(read_id, response)


async def _read_expense_amount(referrer: dict, category: str, date: str, raw: bytes, mime: str, *, fresh: bool):
    from features import handler_expenses

    canonical_reference = str(referrer.get("name") or "").strip()
    try:
        verification = await _read_receipt(
            raw, mime, referrer=referrer, category=category, create_ledger=False,
            cache="fresh" if fresh else "reuse",
        )
    except Exception as exc:
        logger.exception("Reading the amount off a handler expense screenshot failed")
        return {"status": "error", "message": f"Payment screenshot could not be read: {exc}"}
    read = receipt_amount.read_amount(verification)
    if not read.ok:
        return {"status": "error", "message": read.problem}
    # A receipt that is already recorded is refused now, not after the operator
    # has confirmed an amount for it.
    try:
        handler_expenses.check_new_expense(
            _incoming_expense(canonical_reference, read.amount, category, "", date, raw, verification)
        )
    except transaction_identity.DuplicateTransactionError as exc:
        return _duplicate_response(exc)
    return {
        "status": "ok",
        "amount": read.amount,
        "corroborated": bool(verification.get("amount_corroborated")),
        "amount_source": str(verification.get("amount_source") or ""),
    }


async def _create_expense(reference: str, amount: str, category: str, note: str, date: str, file: UploadFile):
    from features import handler_expenses
    from features.referrer_registry import resolve_referrer

    selected_referrer = resolve_referrer(reference)
    if selected_referrer is None:
        return {
            "status": "error",
            "message": "Select one registered referrer before logging a payout.",
        }
    canonical_reference = str(selected_referrer.get("name") or "").strip()
    try:
        entered = _entered_amount(amount)
    except ValueError:
        return {"status": "error", "message": "The amount must be a number."}

    raw, mime, refusal = await _receipt_upload(file)
    if refusal:
        return refusal

    try:
        verification = await _read_receipt(
            raw, mime, referrer=selected_referrer, category=category, create_ledger=True, cache="reuse",
        )
    except Exception as exc:
        logger.exception("Central payment verification failed for handler expense")
        return {"status": "error", "message": f"Payment screenshot could not be verified: {exc}"}

    # The amount saved is the one the screenshot establishes -- never one typed
    # in, and never one the screenshot leaves in doubt.
    read = receipt_amount.read_amount(verification)
    if not read.ok:
        return {"status": "error", "message": read.problem, "ai_extraction": verification}
    if entered is not None and entered != read.amount:
        return {
            "status": "error",
            "message": (
                f"The amount confirmed (₹{entered:,}) is not the amount on the screenshot "
                f"(₹{read.amount:,}). Nothing was saved. Attach the screenshot again to read it afresh."
            ),
        }

    body = _incoming_expense(canonical_reference, read.amount, category, note, date, raw, verification)
    try:
        row = handler_expenses.create_expense(body)
    except transaction_identity.DuplicateTransactionError as exc:
        return _duplicate_response(exc)

    # Attach the proof to the newly created expense
    try:
        handler_expenses.add_proof(
            row["id"],
            data=raw,
            original_name=file.filename or "",
            mime_type=file.content_type or "",
            note=note.strip(),
        )
    except ValueError:
        pass  # expense already created, proof validation already passed above

    # Reload the row to include proofs
    updated = next(
        (r for r in handler_expenses.list_expenses() if r.get("id") == row["id"]),
        row,
    )
    return {"status": "ok", "expense": updated}
@router.get(
    "/handler-expenses/reconciliation",
    dependencies=[Depends(_require_fleet_admin)],
)
async def handler_expenses_reconciliation():
    """Read-only: which transactions are recorded in more than one place.

    Reports only. Correcting a finding is a separate, deliberate action so the
    numbers can be reviewed before any balance moves.
    """
    from features import financial_reconciliation

    try:
        return {"status": "ok", "report": financial_reconciliation.reconciliation_report()}
    except Exception as exc:
        logger.exception("Financial reconciliation scan failed")
        return {"status": "error", "message": f"Reconciliation scan failed: {exc}"}


@router.post(
    "/handler-expenses/{eid}/void",
    dependencies=[Depends(_require_fleet_admin)],
)
async def handler_expenses_void(eid: str, body: dict = Body(default=None)):
    """Stop an expense counting as money paid, keeping the record for audit."""
    from features import handler_expenses

    payload = body or {}
    try:
        row = handler_expenses.void_expense(
            eid,
            status=str(payload.get("status") or "VOIDED_DUPLICATE"),
            reason=str(payload.get("reason") or ""),
            actor=str(payload.get("actor") or "admin"),
            ledger_ref=str(payload.get("ledger_ref") or ""),
        )
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    if row is None:
        return {"status": "error", "message": "Expense not found"}
    return {"status": "ok", "expense": row}


@router.patch("/handler-expenses/{eid}", dependencies=[Depends(_require_fleet_admin)])
async def handler_expenses_update(eid: str, body: dict):
    from features import handler_expenses

    try:
        row = handler_expenses.update_expense(eid, body or {})
    except transaction_identity.DuplicateTransactionError as exc:
        return _duplicate_response(exc)
    if row is None:
        return {"status": "error", "message": "Expense not found"}
    return {"status": "ok", "expense": row}
@router.delete("/handler-expenses/{eid}", dependencies=[Depends(_require_fleet_admin)])
async def handler_expenses_delete(eid: str):
    from features import handler_expenses

    ok = handler_expenses.delete_expense(eid)
    if not ok:
        return {"status": "error", "message": "Expense not found"}
    return {"status": "ok"}
@router.get("/handler-expenses/summary")
async def handler_expenses_summary(
    request: Request,
    month: str | None = Query(default=None),
    reference: str | None = Query(default=None),
):
    from core.dashboard_access import handler_payout_reference_scope
    from features import handler_expenses

    scoped_reference = handler_payout_reference_scope(request, reference)
    summary = handler_expenses.summary_by_handler(month=month)
    if scoped_reference:
        key = scoped_reference.strip().lower()
        summary = {
            name: bucket for name, bucket in summary.items()
            if name.strip().lower() == key
        }
    total = sum(b["total"] for b in summary.values())
    return {
        "status": "ok",
        "summary": summary,
        "total": total,
        "count": sum(b["count"] for b in summary.values()),
    }
@router.post("/handler-expenses/{eid}/proofs", dependencies=[Depends(_require_fleet_admin)])
async def handler_expense_upload_proof(
    eid: str,
    file: UploadFile = File(...),
    note: str = Form(default=""),
    analysis_id: str = Form(default=""),
):
    """Attach a payment screenshot to a handler expense entry.

    `analysis_id` lets the dashboard follow the AI node verifying it.
    """
    with ai_activity.analysis(analysis_id, kind=ai_activity.PAYMENT_ANALYSIS) as analysis:
        response = await _attach_expense_proof(eid, file, note)
    return ai_activity.with_analysis(response, analysis)


async def _attach_expense_proof(eid: str, file: UploadFile, note: str):
    from features import handler_expenses

    try:
        raw = await file.read()
        expense = next(
            (row for row in handler_expenses.list_expenses() if row.get("id") == eid),
            None,
        )
        if expense is None:
            return {"status": "error", "message": "Expense not found"}
        from features.payment_verification_engine import verify_payment_screenshot
        verification = await asyncio.to_thread(
            verify_payment_screenshot,
            raw,
            file.content_type or "image/jpeg",
            source_module="handler_expense_proof",
            expected_amount=int(expense.get("amount") or 0),
            entity_id=eid,
            entity_name=expense.get("reference") or "",
            referrer_hint=expense.get("reference") or "",
            purpose=(
                "handler_payout"
                if str(expense.get("category") or "").lower() == "commission"
                else "expense_reimbursement"
            ),
        )
        if not verification.get("deterministic_verified"):
            return {
                "status": "error",
                "message": " ".join(verification.get("deterministic_reasons") or [])
                or "Payment screenshot could not be verified.",
                "ai_extraction": verification,
            }
        # The same screenshot, or the same payment, must not end up as the
        # receipt of a second expense.
        try:
            handler_expenses.refuse_duplicate_proof(
                eid,
                screenshot_hash=transaction_identity.screenshot_hash(raw),
                payment_id=str(verification.get("payment_id") or ""),
                reference=_receipt_reference(verification),
            )
        except transaction_identity.DuplicateTransactionError as exc:
            return _duplicate_response(exc)
        entry = handler_expenses.add_proof(
            eid,
            data=raw,
            original_name=file.filename or "",
            mime_type=file.content_type or "",
            note=note or "",
        )
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    if entry is None:
        return {"status": "error", "message": "Expense not found"}
    return {"status": "ok", "proof": entry}
@router.get("/handler-expenses/{eid}/proofs/{pid}")
async def handler_expense_serve_proof(eid: str, pid: str):
    """Serve a stored handler expense proof image."""
    from features import handler_expenses

    hit = handler_expenses.get_proof(eid, pid)
    if hit is None:
        return {"status": "error", "message": "Proof not found"}
    path, entry = hit
    return FileResponse(
        path,
        media_type=entry.get("mime_type") or "application/octet-stream",
        filename=entry.get("original_name") or entry.get("filename"),
    )
@router.delete("/handler-expenses/{eid}/proofs/{pid}", dependencies=[Depends(_require_fleet_admin)])
async def handler_expense_delete_proof(eid: str, pid: str):
    """Remove a proof from a handler expense entry."""
    from features import handler_expenses

    ok = handler_expenses.delete_proof(eid, pid)
    if not ok:
        return {"status": "error", "message": "Proof not found"}
    return {"status": "ok"}
@router.get("/company-expenses", dependencies=[Depends(_require_fleet_admin)])
async def company_expenses_list(
    month: str | None = Query(default=None),
    category: str | None = Query(default=None),
):
    from features import company_expenses
    rows = company_expenses.list_expenses(month=month, category=category)
    # Merge months from company expenses + handler expenses + candidates
    months_set = {m["value"] for m in company_expenses.available_months()}
    try:
        from features import handler_expenses
        for m in handler_expenses.available_months():
            months_set.add(m["value"])
    except Exception:
        pass
    try:
        from features import candidate_store
        for m in candidate_store.available_months():
            if isinstance(m, dict):
                months_set.add(m["value"])
            else:
                months_set.add(m)
    except Exception:
        pass
    # Build sorted month options
    month_names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    all_months = []
    for m in sorted(months_set, reverse=True):
        try:
            y, mo = m.split("-")
            label = f"{month_names[int(mo) - 1]} {y}"
        except (ValueError, IndexError):
            label = m
        all_months.append({"value": m, "label": label})
    return {
        "status": "ok",
        "expenses": rows,
        "available_months": all_months,
        "categories": [
            {"value": k, "label": v}
            for k, v in company_expenses.CATEGORY_LABELS.items()
        ],
    }
@router.post("/company-expenses", dependencies=[Depends(_require_fleet_admin)])
async def company_expenses_create(body: dict):
    from features import company_expenses
    row = company_expenses.create_expense(body)
    return {"status": "ok", "expense": row}
@router.patch("/company-expenses/{eid}", dependencies=[Depends(_require_fleet_admin)])
async def company_expenses_update(eid: str, body: dict):
    from features import company_expenses
    row = company_expenses.update_expense(eid, body)
    if not row:
        return {"status": "error", "message": "Not found"}
    return {"status": "ok", "expense": row}
@router.delete("/company-expenses/{eid}", dependencies=[Depends(_require_fleet_admin)])
async def company_expenses_delete(eid: str):
    from features import company_expenses
    ok = company_expenses.delete_expense(eid)
    return {"status": "ok" if ok else "not_found"}
@router.get(
    "/company-expenses/total", dependencies=[Depends(_require_fleet_admin)]
)
async def company_expenses_total(month: str | None = Query(default=None)):
    """Combined view: handler payouts + company expenses = total expenditure."""
    from features import company_expenses
    result = company_expenses.total_expenditure(month=month)
    return {"status": "ok", **result}
@router.get("/handler-salaries")
async def handler_salaries_list(month: str | None = Query(default=None)):
    from features import handler_salaries
    rows = handler_salaries.list_salaries()
    by_handler = handler_salaries.salary_owed_by_handler(month=month)
    return {
        "status": "ok",
        "salaries": rows,
        "by_handler": by_handler,
        "total_for_view": handler_salaries.total_salary_owed(month=month),
        "month": month or "all",
    }
@router.post("/handler-salaries", dependencies=[Depends(_require_payroll_admin)])
async def handler_salaries_upsert(body: dict):
    """Create or update one handler's monthly salary.

    Body: { reference, monthly_salary, active_from?, active_until? }
    Passing monthly_salary <= 0 clears the entry (same as DELETE).
    """
    from features import handler_salaries
    try:
        row = handler_salaries.set_salary(
            reference     = body.get("reference") or "",
            monthly_salary= body.get("monthly_salary") or 0,
            active_from   = body.get("active_from"),
            active_until  = body.get("active_until"),
        )
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "salary": row}
@router.delete("/handler-salaries/{reference}", dependencies=[Depends(_require_payroll_admin)])
async def handler_salaries_delete(reference: str):
    from features import handler_salaries
    removed = handler_salaries.delete_salary(reference)
    return {"status": "ok" if removed else "not_found", "reference": reference}
