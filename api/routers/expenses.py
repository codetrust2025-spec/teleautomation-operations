"""Operations expense, payout and salary routes."""
import asyncio
from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from core import ai_activity
from features import transaction_identity
from core.operations_api_helpers import require_admin as _require_fleet_admin
from core.operations_api_helpers import require_payroll_admin as _require_payroll_admin

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
    amount: str = Form(...),
    category: str = Form(default="commission"),
    note: str = Form(default=""),
    date: str = Form(default=""),
    file: UploadFile = File(...),
    analysis_id: str = Form(default=""),
):
    # The screenshot is verified by an AI node before the expense exists;
    # `analysis_id` lets the dashboard follow which one.
    with ai_activity.analysis(analysis_id, kind=ai_activity.PAYMENT_ANALYSIS) as analysis:
        response = await _create_expense(reference, amount, category, note, date, file)
    return ai_activity.with_analysis(response, analysis)


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
    if int(float(amount or 0)) <= 0:
        return {"status": "error", "message": "Amount must be greater than zero"}

    # Validate the screenshot
    raw = await file.read()
    if not raw:
        return {"status": "error", "message": "Payment screenshot is required"}
    if len(raw) > handler_expenses.MAX_PROOF_BYTES:
        return {"status": "error", "message": f"File too large (max {handler_expenses.MAX_PROOF_BYTES // (1024*1024)} MB)"}
    mime = (file.content_type or "").lower().split(";")[0].strip()
    if not handler_expenses._ext_from_mime(mime, file.filename or ""):
        return {"status": "error", "message": "Only image files (jpg / png / webp / gif / heic) are allowed"}

    body = {
        "reference": canonical_reference,
        "amount": int(float(amount)),
        "category": category,
        "note": note.strip(),
        "date": date,
    }
    try:
        from features.payment_verification_engine import verify_payment_screenshot
        verification = await asyncio.to_thread(
            verify_payment_screenshot,
            raw,
            mime or "image/jpeg",
            source_module="handler_expense_create",
            expected_amount=int(float(amount)),
            entity_name=canonical_reference,
            referrer_hint=canonical_reference,
            referrer_id=str(selected_referrer.get("id") or ""),
            purpose=(
                "handler_payout"
                if category.strip().lower() == "commission"
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
    except Exception as exc:
        logger.exception("Central payment verification failed for handler expense")
        return {"status": "error", "message": f"Payment screenshot could not be verified: {exc}"}

    # The verifier has just read the transaction reference and the payer off
    # this screenshot. Storing them is what lets the same money be recognised
    # if it was already recorded as a recovery or a payout somewhere else.
    body["external_transaction_id"] = (
        verification.get("utr_number")
        or verification.get("transaction_id")
        or verification.get("reference_number")
        or ""
    )
    body["payer"] = (
        verification.get("sender_name") or verification.get("sender_upi_id") or ""
    )
    try:
        row = handler_expenses.create_expense(body)
    except transaction_identity.DuplicateTransactionError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "duplicate_of": {
                "record_id": exc.existing.get("record_id"),
                "kind": exc.existing.get("kind"),
                "source_module": exc.existing.get("source_module"),
                "date": exc.existing.get("date"),
                "amount": exc.existing.get("amount"),
            },
        }

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

    row = handler_expenses.update_expense(eid, body or {})
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
