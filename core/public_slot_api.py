"""Public interview slot booking API (no dashboard login required)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from core import ai_activity
from core.ocr_policy import processing_mode

logger = logging.getLogger(__name__)


def _booking_lock():
    """The one lock every booking-creating path holds; see candidate_store."""
    from features import candidate_store

    return candidate_store.BOOKING_LOCK

# The live analysis stream: how often it looks for a change, how long it may
# stay open, and how often it proves it is alive. The keepalive stays well
# inside nginx's default 60s proxy read timeout on the public site, and the
# lifetime outlasts the longest analysis the invite endpoint will wait for.
ANALYSIS_POLL_SECONDS = 0.3
ANALYSIS_STREAM_SECONDS = 300
ANALYSIS_KEEPALIVE_SECONDS = 15

# Invite extraction must always answer with JSON. Nginx gives the app 300s
# (proxy_read_timeout) before serving its own HTML 504, which the browser
# cannot parse, so the application deadline is deliberately well below that.
# 90s proved too tight: the vision model was still working at 90,091ms and
# every invite fell through to manual entry. 240s leaves a 60s margin under
# Nginx's 300s proxy_read_timeout, so the application still answers first.
INVITE_EXTRACTION_TIMEOUT_DEFAULT = 240
INVITE_EXTRACTION_TIMEOUT_CEILING = 240


def invite_extraction_timeout_seconds() -> int:
    """Seconds to wait for invite extraction before falling back to manual entry.

    Configurable via INVITE_EXTRACTION_TIMEOUT, but always clamped below the
    proxy read timeout so the proxy can never answer before the application.
    """
    raw = os.environ.get("INVITE_EXTRACTION_TIMEOUT", "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = INVITE_EXTRACTION_TIMEOUT_DEFAULT
    if value <= 0:
        value = INVITE_EXTRACTION_TIMEOUT_DEFAULT
    return min(value, INVITE_EXTRACTION_TIMEOUT_CEILING)


def _invite_extraction_fallback(warning: str, *, trace_id: str = "") -> dict:
    """Sanitized manual-entry payload. Creates no candidate and no booking."""
    return {
        "status": "ok",
        "success": False,
        "extraction_source": "error",
        "processing_mode": processing_mode(),
        "data": {
            "candidate_name": "",
            "interview_date": "",
            "start_time": "",
            "end_time": "",
            "interview_round": "",
            "technology": "",
            "meeting_platform": "",
            "confidence_score": 0,
            "missing_fields": ["interview_date", "start_time", "interview_round"],
            "warnings": [warning],
            "is_payment_screenshot": False,
            "looks_like_interview_invite": True,
            "manual_fields_required": True,
            "invite_trace_id": trace_id,
        },
    }


def _invite_trace_id(value: str = "") -> str:
    """Return a safe correlation id without logging candidate-controlled text."""
    supplied = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", supplied):
        return supplied
    return uuid.uuid4().hex


def _invite_trace_value(value: Any, *, limit: int = 80) -> str:
    """Bound and flatten diagnostic values before writing public input to logs."""
    return " ".join(str(value or "").split())[:limit]


def _log_invite_extraction_trace(
    *,
    trace_id: str,
    image_sha256: str,
    result: dict[str, Any],
    outcome: str = "complete",
) -> None:
    """Log only the time provenance needed to diagnose AM/PM drift."""
    raw_date = result.pop("_model_raw_interview_date", "")
    raw_start = result.pop("_model_raw_start_time", "")
    raw_end = result.pop("_model_raw_end_time", "")
    result["invite_trace_id"] = trace_id
    # Production intentionally filters ordinary INFO traffic. This provenance
    # must survive so an AM/PM incident can be proven end to end.
    logger.warning(
        "Invite booking trace phase=extract outcome=%s trace_id=%s image_sha256=%s "
        "raw_date=%r raw_start=%r raw_end=%r normalized_date=%r "
        "normalized_start=%r normalized_end=%r normalized_time_24h=%r "
        "model=%s node=%s safe=%s method=%s",
        outcome,
        trace_id,
        image_sha256,
        _invite_trace_value(raw_date),
        _invite_trace_value(raw_start),
        _invite_trace_value(raw_end),
        _invite_trace_value(result.get("interview_date") or result.get("date")),
        _invite_trace_value(result.get("start_time")),
        _invite_trace_value(result.get("end_time")),
        _invite_trace_value(result.get("time")),
        _invite_trace_value(result.get("primary_model")),
        _invite_trace_value(
            result.get("inference_node_id") or result.get("inference_node_label")
        ),
        bool(result.get("auto_booking_safe")),
        _invite_trace_value(
            result.get("extraction_method") or result.get("extraction_source")
        ),
    )


def _json_error(message: str, status: int = 400, **extra: Any) -> JSONResponse:
    payload: dict[str, Any] = {"status": "error", "message": message}
    payload.update(extra)
    return JSONResponse(payload, status_code=status)


class BookingNotPersisted(RuntimeError):
    """The booking did not reach the candidate row.

    Raised before any payment is linked, so the confirmation unwinds with the
    receipt still reusable rather than reporting a 500 over a spent payment.
    """

    def __init__(self, action: str = "") -> None:
        super().__init__(f"confirm_unpersisted action={action}")
        self.action = action


def _pending_proof_ids(*values: str) -> list[str]:
    """Pending payment proof ids from a single field or a split-payment list.

    A split payment carries several proofs, so the id fields accept a
    comma-separated list. Order is preserved and repeats are dropped: the same
    id twice must never let one instalment count twice toward the fee.
    """
    ordered: list[str] = []
    for value in values:
        for token in str(value or "").replace("\n", ",").split(","):
            proof_id = token.strip()
            if proof_id and proof_id not in ordered:
                ordered.append(proof_id)
    return ordered


def install_public_slot_routes(app) -> None:
    from features import candidate_store as cs

    @app.get("/public/slots/candidates")
    async def public_slot_candidates(channel: str | None = None):
        rows = cs.interview_slot_picker_rows(channel=channel or "profile")
        return JSONResponse(
            {"status": "ok", "candidates": rows, "count": len(rows)},
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/public/slots/booked")
    async def public_slot_booked(days: int = 60):
        snap = cs.public_booked_interview_slots(days=days)
        return JSONResponse(
            {"status": "ok", **snap},
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/public/slots/payment-requirement")
    async def public_slot_payment_requirement(
        service_type: str = "",
        name: str = "",
        phone: str = "",
        candidate_id: str = "",
        interview_round: str = "",
    ):
        """What this booking owes, straight from the rule the booking enforces.

        Round-wise candidates are typed in by hand and are not required to exist
        on the profile roster, so the form cannot read a balance off a roster row
        the way profile service does. Asking here keeps one answer at the upload
        boundary, the booking boundary and the screen.
        """
        requirement = cs.public_booking_payment_requirement(
            service_type=service_type,
            name=name,
            phone=phone,
            candidate_id=candidate_id,
            interview_round=interview_round,
        )
        return JSONResponse(
            {"status": "ok", **requirement},
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/public/slots/payment-info")
    async def public_slot_payment_info(
        service_type: str = "round_wise",
        name: str = "",
        phone: str = "",
        candidate_id: str = "",
        interview_round: str = "",
    ):
        """Backward-compatible view of the authoritative payment requirement."""
        requirement = cs.public_booking_payment_requirement(
            service_type=service_type,
            name=name,
            phone=phone,
            candidate_id=candidate_id,
            interview_round=interview_round,
        )
        return JSONResponse(
            {
                "status": "ok",
                **requirement,
                "needs_payment": requirement["payment_required"],
                "waived": requirement["re_service"],
            },
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/public/slots/analysis/{analysis_id}")
    async def public_slot_analysis_status(analysis_id: str):
        """Which AI node is working on one upload, for the page that sent it.

        The booking page follows its uploads here, and so do the dashboard's
        payment, resume and referrer-expense uploads: one status for every
        upload an AI node reads. Only node display names and the analysis
        state are ever returned: nothing about the candidate, the image or the
        result. An id that is not known (yet) reads as waiting, since the
        upload it belongs to may still be arriving, and saying "unknown" would
        only tell a caller which ids exist.
        """
        if not ai_activity.valid_analysis_id(analysis_id):
            return _json_error("Unknown analysis.", status=404)
        return JSONResponse(
            {"status": "ok", "analysis": ai_activity.analysis_view(analysis_id)},
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/public/slots/analysis/{analysis_id}/events")
    async def public_slot_analysis_events(analysis_id: str, request: Request):
        """The same status as a server-sent event stream, pushed as it changes.

        Closes once the analysis is done, and after a bounded lifetime either
        way; the page also stops listening as soon as its upload returns.
        `X-Accel-Buffering: no` keeps nginx from holding events back until the
        stream ends, and the keepalive comment stays inside its read timeout.
        """
        if not ai_activity.valid_analysis_id(analysis_id):
            return _json_error("Unknown analysis.", status=404)
        if not ai_activity.open_stream():
            return _json_error("Too many live updates. The upload continues.", status=429)

        async def events():
            try:
                yield "retry: 2000\n\n"
                last = None
                deadline = time.monotonic() + ANALYSIS_STREAM_SECONDS
                keepalive = time.monotonic() + ANALYSIS_KEEPALIVE_SECONDS
                while time.monotonic() < deadline:
                    status = ai_activity.analysis_view(analysis_id)
                    if status != last:
                        last = status
                        keepalive = time.monotonic() + ANALYSIS_KEEPALIVE_SECONDS
                        yield f"data: {json.dumps(status)}\n\n"
                        if status["state"] == "done":
                            return
                    elif time.monotonic() >= keepalive:
                        keepalive = time.monotonic() + ANALYSIS_KEEPALIVE_SECONDS
                        yield ": keepalive\n\n"
                    if await request.is_disconnected():
                        return
                    await asyncio.sleep(ANALYSIS_POLL_SECONDS)
            finally:
                ai_activity.close_stream()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/public/slots/payment-proof")
    async def public_slot_payment_proof(
        name: str = Form(...),
        file: UploadFile | None = File(default=None),
        files: list[UploadFile] = File(default=[]),
        note: str = Form(default=""),
        service_type: str = Form(default=""),
        phone: str = Form(default=""),
        candidate_id: str = Form(default=""),
        technology: str = Form(default=""),
        interview_round: str = Form(default=""),
        existing_proof_ids: str = Form(default=""),
        analysis_id: str = Form(default=""),
    ):
        # Every model call made while verifying these screenshots is attributed
        # to this upload, so the page can name the node reading them and the AI
        # nodes panel can show that node busy with a booking analysis.
        with ai_activity.booking_analysis(analysis_id) as analysis:
            response = await _payment_proof(
                name=name, file=file, files=files, note=note,
                service_type=service_type, phone=phone, candidate_id=candidate_id,
                technology=technology, interview_round=interview_round,
                existing_proof_ids=existing_proof_ids,
            )
        return ai_activity.with_analysis(response, analysis)

    async def _payment_proof(
        *,
        name: str,
        file: UploadFile | None,
        files: list[UploadFile],
        note: str,
        service_type: str,
        phone: str,
        candidate_id: str,
        technology: str,
        interview_round: str,
        existing_proof_ids: str,
    ):
        # A split payment arrives as several screenshots (2,000 + 1,000 + 2,000
        # against a 5,000 fee). Every receipt is still verified on its own and
        # still fails closed - registered receiver, visible identifier,
        # transaction reference, successful status, extraction confidence - but
        # "does this cover the fee" is a property of the whole set, not of one
        # instalment, so it is decided on the running total here and enforced
        # again at /bookings/confirm, which is where money actually moves.
        uploads = [
            upload
            for upload in ([file] if file is not None else []) + list(files or [])
            if upload is not None and (upload.filename or "")
        ]
        if not uploads:
            return _json_error("Attach at least one payment screenshot.")
        try:
            # The amount is never decided here — candidate_store owns it, and
            # /bookings/confirm re-derives it from the same call.
            requirement = cs.public_booking_payment_requirement(
                service_type=service_type,
                name=name,
                phone=phone,
                candidate_id=candidate_id,
                interview_round=interview_round,
            )
            due_amount = requirement["amount_due"]
            payment_owner = (
                None if service_type.strip() == "round_wise" else cs._best_row_for_slot_name(name)
            )

            from features.payment_verification_engine import verify_payment_screenshot
            from features.ollama_payment_extract import generate_payment_narrative
            from features.pending_slot_payment import (
                duplicates_existing,
                get_verified_proof,
                save_verified_proof,
                verified_total,
            )

            normalized_service = service_type.strip() or "profile_service"
            # Screenshots saved by an earlier click already count toward the
            # total, so uploading the instalments one at a time reaches the same
            # place as uploading them together.
            accepted: list[dict] = []
            for proof_id in _pending_proof_ids(existing_proof_ids):
                resolved = get_verified_proof(
                    proof_id,
                    name=name,
                    service_type=normalized_service,
                    phone=phone,
                    candidate_id=candidate_id,
                    technology=technology,
                    interview_round=interview_round,
                )
                if resolved:
                    accepted.append(resolved[1])

            saved: list[dict] = []
            extractions: list[dict] = []
            rejected: list[dict] = []
            for upload in uploads:
                raw = await upload.read()
                label = upload.filename or "payment.jpg"
                # Payee validation is security-critical and deliberately fails
                # closed. Never save or credit the receipt before this succeeds.
                try:
                    ai_extraction = await asyncio.to_thread(
                        verify_payment_screenshot,
                        raw,
                        upload.content_type or "image/jpeg",
                        source_module="public_slot_payment_proof",
                        # Deliberately 0: one instalment of a split payment is a
                        # complete, genuine payment, and rejecting it for not
                        # covering the whole fee on its own is what made split
                        # payments impossible. The fee is checked on the total.
                        expected_amount=0,
                        entity_id=str((payment_owner or {}).get("id") or ""),
                        entity_name=name.strip(),
                        candidate_id=str((payment_owner or {}).get("id") or ""),
                        referrer_hint=str((payment_owner or {}).get("reference") or ""),
                        purpose="candidate_payment",
                        payment_scope=(
                            "ROUND" if service_type.strip() == "round_wise" else "PROFILE"
                        ),
                        create_ledger=False,
                    )
                except Exception:
                    logger.exception("Company payment verification failed")
                    rejected.append({
                        "filename": label,
                        "message": (
                            "Could not verify this payment against the company/referrer registry. "
                            "Upload a clear receipt showing the receiver UPI ID or payment phone number, amount, UTR, and successful status."
                        ),
                    })
                    continue
                ai_extraction["company_payment_reasons"] = list(
                    ai_extraction.get("deterministic_reasons") or []
                )
                if not ai_extraction.get("booking_eligible"):
                    verification_state = str(
                        ai_extraction.get("verification_state") or ""
                    )
                    if verification_state == "INCOMPLETE_PAYMENT_EVIDENCE":
                        message = (
                            "More Payment Details Required. Upload the complete "
                            "transaction-details screenshot showing the receiver "
                            "identifier and Transaction ID or UTR."
                        )
                    else:
                        message = (
                            " ".join(ai_extraction["company_payment_reasons"])
                            or "This receipt is not a verified payment to a registered company or referrer account."
                        )
                    # What the model actually read, on the one path that
                    # refuses a receipt. A rejected upload is never stored -- the
                    # payee check fails closed and nothing is written before it
                    # passes -- so without this the extraction that caused the
                    # refusal is gone the moment the response is sent, and the
                    # only way to reason about a live rejection is to guess at
                    # it. Identifiers here are masked by the payment app before
                    # they ever reach us.
                    logger.warning(
                        "payment proof refused: state=%s receiver_name=%r "
                        "receiver_upi=%r receiver_account=%r sender_name=%r "
                        "sender_upi=%r sender_account=%r amount=%r status=%r "
                        "utr=%r txn=%r match=%r conflict=%r reasons=%s",
                        ai_extraction.get("verification_state"),
                        ai_extraction.get("receiver_name"),
                        ai_extraction.get("receiver_upi_id"),
                        ai_extraction.get("receiver_account"),
                        ai_extraction.get("sender_name"),
                        ai_extraction.get("sender_upi_id"),
                        ai_extraction.get("sender_account_identifier"),
                        ai_extraction.get("amount"),
                        ai_extraction.get("payment_status") or ai_extraction.get("status"),
                        ai_extraction.get("utr_number"),
                        ai_extraction.get("transaction_id"),
                        ai_extraction.get("receiver_match"),
                        ai_extraction.get("receiver_identifier_conflict"),
                        ai_extraction.get("company_payment_reasons"),
                    )
                    rejected.append({
                        "filename": label,
                        "message": message,
                        "ai_extraction": ai_extraction,
                    })
                    continue
                # The same receipt uploaded twice is one payment. Nothing else
                # catches it while the proofs are still pending - the fraud
                # check only sees evidence already attached to a candidate - so
                # without this one 2,000 screenshot would read as 4,000.
                if duplicates_existing(
                    {
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "verification": ai_extraction,
                    },
                    accepted,
                ):
                    rejected.append({
                        "filename": label,
                        "message": (
                            "This screenshot is the same transaction as one already "
                            "uploaded, so it adds nothing to the total."
                        ),
                        "ai_extraction": ai_extraction,
                    })
                    continue
                try:
                    pending_proof = save_verified_proof(
                        name=name,
                        service_type=normalized_service,
                        phone=phone,
                        candidate_id=candidate_id,
                        technology=technology,
                        interview_round=interview_round,
                        data=raw,
                        original_name=label,
                        mime_type=upload.content_type or "image/jpeg",
                        amount_due=due_amount,
                        note=note or "",
                        verification=ai_extraction,
                    )
                except ValueError as proof_exc:
                    rejected.append({
                        "filename": label,
                        "message": str(proof_exc),
                        "ai_extraction": ai_extraction,
                    })
                    continue
                accepted.append(pending_proof)
                saved.append(pending_proof)
                extractions.append(ai_extraction)

            if saved:
                narratives = await asyncio.gather(
                    *(
                        asyncio.to_thread(
                            generate_payment_narrative,
                            extraction,
                            candidate_name=name,
                            expected_amount=due_amount,
                            received_amount=0,
                        )
                        for extraction in extractions
                    ),
                    return_exceptions=True,
                )
                for extraction, narrative in zip(extractions, narratives):
                    if isinstance(narrative, BaseException):
                        logger.debug(
                            "Payment narrative generation skipped: %s", narrative
                        )
                    else:
                        extraction["narrative"] = narrative

            required = max(0, int(due_amount or 0))
            verified_sum = verified_total(accepted)
            remaining = max(0, required - verified_sum)
            payment_complete = required <= 0 or remaining <= 0
            totals = {
                "proof_ids": [str(entry.get("id") or "") for entry in accepted],
                "verified_total": verified_sum,
                "amount_due": required,
                "remaining_due": remaining,
                "balance_due": remaining,
                "payment_complete": payment_complete,
                "proof_count": len(accepted),
                "rejected": [
                    {"filename": item["filename"], "message": item["message"]}
                    for item in rejected
                ],
            }
            if not saved:
                first = rejected[0] if rejected else {}
                return _json_error(
                    " ".join(dict.fromkeys(item["message"] for item in rejected))
                    or "No payment screenshot could be verified.",
                    ai_extraction=first.get("ai_extraction") or {},
                    **totals,
                )
        except ValueError as e:
            return _json_error(str(e))
        return {
            "status": "ok",
            "candidate_id": "",
            "name": name.strip(),
            # The proof's own notice (a reuse allowance, say) stays here, where
            # it has always been. How far the instalments get toward the fee is
            # reported by the totals below, not by prose.
            "message": str(saved[0].get("message") or ""),
            # Single-proof shape kept intact for any caller that predates split
            # payments; the lists alongside it are the complete answer.
            "proof_id": str(saved[0].get("id") or ""),
            "proof": saved[0],
            "ai_extraction": extractions[0],
            "proofs": saved,
            "ai_extractions": extractions,
            "notices": [
                str(entry.get("message") or "")
                for entry in saved
                if str(entry.get("message") or "").strip()
            ],
            **totals,
        }

    @app.post("/public/slots/parse-screenshot")
    async def public_slot_parse_screenshot(file: UploadFile = File(...)):
        raw = await file.read()
        mime = file.content_type or "image/jpeg"
        logger.info(
            "Invite upload received filename=%s mime=%s bytes=%d sha256=%s transport=original",
            file.filename or "",
            mime,
            len(raw),
            hashlib.sha256(raw).hexdigest()[:16],
        )
        try:
            from features.slot_screenshot_parse import parse_invite_screenshot

            parsed = await asyncio.to_thread(parse_invite_screenshot, raw, mime)
        except ValueError as e:
            return _json_error(str(e))
        except Exception as exc:
            logger.exception("parse-screenshot failed")
            return _json_error(f"Could not read screenshot: {exc}", status=500)
        return {"status": "ok", "slot": parsed}

    @app.post("/public/slots/extract-invite-ai")
    async def public_slot_extract_invite_ai(
        file: UploadFile = File(...),
        analysis_id: str = Form(default=""),
    ):
        """AI-powered interview invite extraction using Ollama vision models."""
        with ai_activity.booking_analysis(analysis_id) as analysis:
            response = await _extract_invite_ai(file)
        return ai_activity.with_analysis(response, analysis)

    async def _extract_invite_ai(file: UploadFile):
        raw = await file.read()
        mime = file.content_type or "image/jpeg"
        trace_id = _invite_trace_id()
        image_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            from features.ollama_invite_extract import extract_interview_invite_with_ollama

            # Bound the wait so this endpoint always answers with JSON. The
            # extractor's own OLLAMA_TIMEOUT defaults to 900s, far beyond the
            # 300s proxy read timeout, so a slow model used to let Nginx reply
            # first with an HTML 504 that the browser could not parse as JSON.
            result = await asyncio.wait_for(
                asyncio.to_thread(extract_interview_invite_with_ollama, raw, mime),
                timeout=invite_extraction_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "AI invite extraction exceeded %ss; returning manual-entry fallback",
                invite_extraction_timeout_seconds(),
            )
            fallback = _invite_extraction_fallback(
                "Invite reading took too long. Retry or enter the date and time manually.",
                trace_id=trace_id,
            )
            _log_invite_extraction_trace(
                trace_id=trace_id,
                image_sha256=image_sha256,
                result=fallback["data"],
                outcome="timeout",
            )
            return fallback
        except Exception as exc:
            logger.exception("AI invite extraction failed")
            fallback = _invite_extraction_fallback(
                f"AI extraction failed: {exc}. Use manual entry.",
                trace_id=trace_id,
            )
            _log_invite_extraction_trace(
                trace_id=trace_id,
                image_sha256=image_sha256,
                result=fallback["data"],
                outcome="error",
            )
            return fallback

        _log_invite_extraction_trace(
            trace_id=trace_id,
            image_sha256=image_sha256,
            result=result,
        )

        is_success = bool(result and result.get("confidence_score", 0) > 0)
        if not result.get("auto_booking_safe"):
            logger.warning(
                "Invite extraction not safe stage=%s reason=%s method=%s warnings=%s",
                result.get("failure_stage") or "unknown",
                result.get("failure_reason") or "No exact failure reason supplied",
                result.get("extraction_method") or result.get("extraction_source"),
                result.get("warnings") or [],
            )
        return {
            "status": "ok",
            "success": is_success,
            # Which engine actually read the file. Prefer the mode the
            # extractor snapshotted at the start of this request over a fresh
            # read, so an admin toggling the switch mid-extraction cannot make
            # the response describe a mode this result was not produced under.
            "processing_mode": result.get("processing_mode") or processing_mode(),
            "extraction_source": result.get("extraction_source", "unknown"),
            "primary_model": result.get("primary_model", ""),
            "backup_model": result.get("backup_model", ""),
            "data": result,
        }

    @app.post("/public/slots/extract-payment-ai")
    async def public_slot_extract_payment_ai(
        file: UploadFile = File(...),
        candidate_name: str = Form(default=""),
    ):
        """AI-powered payment proof extraction using Ollama vision models.

        Reads UPI/bank screenshots and extracts: amount, sender, UTR, date, status.
        If candidate_name is provided, auto-verifies against their balance due.
        """
        raw = await file.read()
        mime = file.content_type or "image/jpeg"
        # Persist the bytes before anything else looks at them. This path used
        # to record ledger evidence — checksum, extraction, UTR — while the
        # image lived only in memory for the request, so payments ended up with
        # complete metadata and no screenshot left to re-read.
        stored_evidence: dict = {}
        try:
            from features import payment_evidence_store
            stored_evidence = await asyncio.to_thread(
                payment_evidence_store.store,
                raw,
                mime_type=mime,
                original_filename=file.filename or "",
                candidate_id=str(
                    (
                        cs._best_row_for_slot_name(candidate_name.strip())
                        if candidate_name.strip()
                        else {}
                    ).get("id")
                    or ""
                ),
                upload_source="public_slot_payment_proof",
            )
        except Exception:
            logger.exception("Could not durably store public payment evidence")
        try:
            from features.payment_verification_engine import verify_payment_screenshot

            amount_due = (
                cs.merged_balance_due_for_name(candidate_name.strip())
                if candidate_name.strip()
                else 0
            )
            result = await asyncio.to_thread(
                verify_payment_screenshot,
                raw,
                mime,
                source_module="public_slot_payment_extract",
                expected_amount=amount_due,
                entity_id=str(
                    (
                        cs._best_row_for_slot_name(candidate_name.strip())
                        if candidate_name.strip()
                        else {}
                    ).get("id")
                    or ""
                ),
                entity_name=candidate_name.strip(),
                candidate_id=str(
                    (
                        cs._best_row_for_slot_name(candidate_name.strip())
                        if candidate_name.strip()
                        else {}
                    ).get("id")
                    or ""
                ),
                referrer_hint=str(
                    (
                        cs._best_row_for_slot_name(candidate_name.strip())
                        if candidate_name.strip()
                        else {}
                    ).get("reference")
                    or ""
                ),
                purpose="candidate_payment",
                payment_scope="PROFILE",
                create_ledger=False,
            )

            if candidate_name.strip() and result.get("is_payment_screenshot"):
                try:
                    # Generate narrative
                    from features.ollama_payment_extract import generate_payment_narrative
                    result["narrative"] = await asyncio.to_thread(
                        generate_payment_narrative,
                        result,
                        candidate_name=candidate_name.strip(),
                        expected_amount=amount_due,
                        received_amount=0,
                    )
                except Exception as vex:
                    logger.warning("Payment verification failed: %s", vex)
                    result["warnings"] = list(result.get("warnings") or [])
                    result["warnings"].append(f"Auto-verify failed: {vex}")

        except Exception as exc:
            logger.exception("AI payment extraction failed")
            return {
                "status": "ok",
                "success": False,
                "extraction_source": "error",
                "data": {
                    "amount": 0,
                    "is_payment_screenshot": False,
                    "confidence_score": 0,
                    "warnings": [f"AI extraction failed: {exc}"],
                    "verified": False,
                    "verification_result": "Extraction failed",
                },
            }

        is_success = bool(
            result
            and result.get("is_payment_screenshot")
            and result.get("amount", 0) > 0
        )
        return {
            "status": "ok",
            "success": is_success,
            "extraction_source": result.get("extraction_source", "unknown"),
            "primary_model": result.get("primary_model", ""),
            "data": result,
        }

    @app.post("/public/slots/extract-resume-ai")
    async def public_slot_extract_resume_ai(
        file: UploadFile = File(...),
        analysis_id: str = Form(default=""),
    ):
        """AI-powered resume PDF extraction using Ollama.

        Reads PDF resumes and extracts: name, phone, email, technology,
        years of experience, skills, education, current company. The
        dashboard's resume auto-fill follows the node reading it by
        `analysis_id`, exactly as the booking page follows its uploads.
        """
        with ai_activity.analysis(analysis_id, kind=ai_activity.RESUME_ANALYSIS) as analysis:
            response = await _extract_resume_ai(file)
        return ai_activity.with_analysis(response, analysis)

    async def _extract_resume_ai(file: UploadFile):
        raw = await file.read()
        mime = file.content_type or "application/pdf"
        try:
            from features.ollama_resume_extract import extract_resume_with_ollama

            result = await asyncio.to_thread(extract_resume_with_ollama, raw, mime)
        except Exception as exc:
            logger.exception("AI resume extraction failed")
            return {
                "status": "ok",
                "success": False,
                "extraction_source": "error",
                "data": {
                    "candidate_name": "",
                    "technology": "",
                    "phone": "",
                    "confidence_score": 0,
                    "is_resume": False,
                    "error": str(exc),
                },
            }

        # Success if we have at least a name OR enough contact/skill signals.
        # Regex fallback (no Ollama) is still useful if it found phone/email/tech.
        has_name = bool(result.get("candidate_name"))
        has_contact = bool(result.get("phone") or result.get("email"))
        has_tech = bool(result.get("technology"))
        is_success = bool(
            result
            and result.get("is_resume")
            and (has_name or (has_contact and has_tech))
        )
        return {
            "status": "ok",
            "success": is_success,
            "extraction_source": result.get("extraction_source", "unknown"),
            "primary_model": result.get("primary_model", ""),
            "data": result,
        }

    @app.post("/bookings/confirm")
    async def confirm_public_slot_booking(
        name: str = Form(...),
        date: str = Form(default=""),
        time: str = Form(default=""),
        time_end: str = Form(default=""),
        interview_round: str = Form(default=""),
        technology: str = Form(default=""),
        phone: str = Form(default=""),
        candidate_id: str = Form(default=""),
        service_type: str = Form(default="round_wise"),
        notes: str = Form(default=""),
        payment_proof_id: str = Form(default=""),
        payment_proof_ids: str = Form(default=""),
        idempotency_key: str = Form(default=""),
        invite_trace_id: str = Form(default=""),
        invite_display_date: str = Form(default=""),
        invite_display_time: str = Form(default=""),
        invite_extracted_start_time: str = Form(default=""),
        file: UploadFile | None = File(default=None),
    ):
        trace_id = _invite_trace_id(invite_trace_id)
        normalized_service_type = service_type.strip() or "round_wise"
        normalized_technology = technology.strip()
        normalized_phone = phone.strip() if normalized_service_type == "round_wise" else ""
        normalized_round = cs.normalise_interview_round(interview_round)
        if not name.strip():
            return _json_error("Client name is required.")
        if not normalized_round:
            return _json_error(
                "Interview round is required. Select L1, L2, or another valid round."
            )
        if normalized_service_type == "round_wise" and not normalized_technology:
            return _json_error(
                "Technology is required for round-wise booking. "
                "Select the technology and try again."
            )
        if normalized_service_type == "round_wise" and not cs.candidate_phone_identity(normalized_phone):
            return _json_error(
                "A valid phone number is required for round-wise booking. "
                "Enter the candidate phone number and try again."
            )

        requested_proof_ids = _pending_proof_ids(payment_proof_id, payment_proof_ids)
        normalized_proof_id = requested_proof_ids[0] if requested_proof_ids else ""
        pending_payment_proofs: list[tuple[str, dict]] = []
        if requested_proof_ids:
            from features.pending_slot_payment import get_verified_proof

            for proof_id in requested_proof_ids:
                resolved = get_verified_proof(
                    proof_id,
                    name=name,
                    service_type=normalized_service_type,
                    phone=normalized_phone,
                    candidate_id=candidate_id.strip(),
                    technology=normalized_technology,
                    interview_round=normalized_round,
                )
                if resolved:
                    pending_payment_proofs.append(resolved)
        pending_payment_proof = pending_payment_proofs[0] if pending_payment_proofs else None
        re_service_booking = cs.candidate_is_re_service_eligible(
            name=name.strip(),
            phone=normalized_phone,
            interview_round=normalized_round,
            candidate_id=candidate_id.strip(),
        )
        if normalized_service_type == "round_wise" and not pending_payment_proofs and not re_service_booking:
            return _json_error(
                "Upload and verify the payment screenshot to continue.",
                payment_due=True,
                balance_due=cs.baseline_for_service("round_wise"),
                name=name.strip(),
            )
        # The upload boundary verifies each receipt on its own; the fee itself
        # is only satisfiable by the set. Booking is where money moves, so the
        # total is re-derived here from the stored proofs rather than trusted
        # from the client, and instalments that do not add up are refused.
        if pending_payment_proofs and not re_service_booking:
            from features.pending_slot_payment import verified_total

            required_amount = cs.public_booking_payment_requirement(
                service_type=normalized_service_type,
                name=name,
                phone=normalized_phone,
                candidate_id=candidate_id.strip(),
                interview_round=normalized_round,
            )["amount_due"]
            paid_total = verified_total(
                [entry for _path, entry in pending_payment_proofs]
            )
            if required_amount > 0 and paid_total < required_amount:
                # Round-wise is priced by the referrer and client; the tariff
                # enforced here is only the floor, so it is named as one.
                shortfall = (
                    f"The verified payments add up to Rs {paid_total:,}, below the "
                    f"Rs {required_amount:,} minimum charge. Upload the remaining payment "
                    "screenshot(s) before confirming."
                    if normalized_service_type == "round_wise"
                    else f"The verified payments add up to Rs {paid_total:,} of the "
                    f"Rs {required_amount:,} due. Upload the remaining payment "
                    "screenshot(s) before confirming."
                )
                return _json_error(
                    shortfall,
                    payment_due=True,
                    balance_due=required_amount - paid_total,
                    verified_total=paid_total,
                    amount_due=required_amount,
                    name=name.strip(),
                )

        slot_image: bytes | None = None
        slot_image_name = ""
        slot_image_mime = ""
        if not file or not file.filename:
            return _json_error("Interview invite screenshot is required.")
        if file and file.filename:
            slot_image = await file.read()
            slot_image_name = file.filename or "slot.jpg"
            slot_image_mime = file.content_type or "image/jpeg"
            # Validate: must look like an interview invite
            try:
                from features.payment_proof_validator import validate_interview_invite
                is_valid, reason = validate_interview_invite(slot_image, slot_image_mime)
                if not is_valid:
                    return _json_error(reason)
            except ValueError as exc:
                return _json_error(str(exc))
            except Exception as exc:
                logger.exception("Interview invite validation failed")
                return _json_error(
                    "Interview invite verification failed. Upload the original screenshot again.",
                    failure_reason=str(exc),
                )

        day = date.strip()
        slot_time = time.strip()
        slot_end = time_end.strip()
        if not day or not slot_time:
            return _json_error(
                "Interview date and start time are required. "
                "Automatic booking is allowed only after dual-source AI verification; "
                "otherwise enter them manually."
            )
        image_sha256 = hashlib.sha256(slot_image or b"").hexdigest()
        logger.warning(
            "Invite booking trace phase=confirm_received trace_id=%s image_sha256=%s "
            "extracted_start=%r displayed_date=%r displayed_time=%r "
            "submitted_date=%r submitted_time=%r submitted_end=%r",
            trace_id,
            image_sha256,
            _invite_trace_value(invite_extracted_start_time),
            _invite_trace_value(invite_display_date),
            _invite_trace_value(invite_display_time),
            _invite_trace_value(day),
            _invite_trace_value(slot_time),
            _invite_trace_value(slot_end),
        )
        booking_key = hashlib.sha256(
            "|".join(
                [
                    idempotency_key.strip(),
                    name.strip().lower(),
                    normalized_service_type,
                    normalized_phone,
                    day,
                    slot_time,
                    slot_end,
                    normalized_round.lower(),
                    # Every instalment is part of what identifies this booking.
                    # A single proof still joins to itself, so keys minted
                    # before split payments existed are unchanged.
                    ",".join(requested_proof_ids),
                ]
            ).encode("utf-8")
        ).hexdigest()
        candidate_ids_before: set[str] = set()
        # The row this attempt touched, recorded as soon as it exists. Deleting
        # rows created during the attempt is not enough on its own: a slot
        # assigned to a candidate who was already on file leaves that row in
        # place, and with it any payment evidence attached to it.
        booked_row_id = ""

        def rollback_attempt() -> None:
            """Leave the payment exactly as reusable as it was before."""
            if booked_row_id:
                requested = set(requested_proof_ids)
                attached = [
                    str(proof.get("id") or "")
                    for proof in (cs.list_attachments(booked_row_id, "payment_proof") or [])
                    if str(proof.get("pending_proof_id") or "") in requested
                ]
                if attached:
                    cs.detach_booking_payment_proofs(booked_row_id, attached)
            current_ids = {
                str(candidate.get("id") or "")
                for candidate in cs.list_candidates(stage="all", month="all")
                if str(candidate.get("id") or "")
            }
            for created_id in current_ids - candidate_ids_before:
                cs.delete_candidate(created_id)

        try:
            with _booking_lock():
                candidate_ids_before = {
                    str(candidate.get("id") or "")
                    for candidate in cs.list_candidates(stage="all", month="all")
                    if str(candidate.get("id") or "")
                }
                # A previous attempt only satisfies this one if it actually
                # booked the slot. The key is stored on the candidate row before
                # the slot is applied, so an attempt blocked after that point
                # leaves the key behind on a row with no date and
                # slot_confirmed false; matching on the key alone replayed that
                # row as a success forever and made the slot unbookable. A
                # booking cancelled since is no answer either, and the rows are
                # every stored row: the list view keeps one per profile candidate.
                existing_booking = next(
                    (
                        candidate
                        for candidate in cs.all_booking_rows()
                        if str(candidate.get("booking_idempotency_key") or "").strip() == booking_key
                        and cs.slot_still_stands(candidate)
                    ),
                    None,
                )
                if existing_booking:
                    row, action = existing_booking, "skip_exists"
                else:
                    payment_reuse = {}
                    if pending_payment_proofs:
                        from features.pending_slot_payment import validate_for_confirmation

                        # Every instalment is fraud-checked; any rejection
                        # raises and aborts the whole confirmation.
                        reuse_results = [
                            validate_for_confirmation(
                                proof,
                                phone=normalized_phone,
                                candidate_id=candidate_id.strip(),
                                booking_key=booking_key,
                            )
                            for proof in pending_payment_proofs
                        ]
                        reused = [
                            result for result in reuse_results if result.get("reuse_allowed")
                        ]
                        if len(reused) > 1:
                            # Only one prior booking can be recorded as the
                            # source of a reused payment, so a split payment
                            # carrying two of them has no single answer.
                            from features.payment_fraud_detection import (
                                PAYMENT_REUSE_BLOCKED_MESSAGE,
                            )

                            raise ValueError(PAYMENT_REUSE_BLOCKED_MESSAGE)
                        payment_reuse = reused[0] if reused else reuse_results[0]
                    row, action = cs.import_confirmed_interview_slot(
                        name=name, date=day, time=slot_time, time_end=slot_end,
                        interview_round=normalized_round, technology=normalized_technology,
                        phone=normalized_phone, service_type=normalized_service_type,
                        notes=notes, source="submit-slot form",
                        payment_proof_id=normalized_proof_id or None,
                        pending_payment_proof=pending_payment_proof,
                        pending_payment_proofs=pending_payment_proofs,
                        payment_reuse=payment_reuse,
                        candidate_id=candidate_id.strip(),
                        idempotency_key=booking_key, slot_image=slot_image,
                        slot_image_name=slot_image_name, slot_image_mime=slot_image_mime,
                    )
                    booked_row_id = str(row.get("id") or "")
                    # Money is linked only to a booking that exists. This
                    # check used to sit after the linkage and outside the try,
                    # so a slot that failed to persist returned a 500 with the
                    # payment already spent and no rollback -- the receipt was
                    # then refused as "already linked to an active or completed
                    # booking" for a booking that had never happened.
                    if not cs.candidate_has_confirmed_slot(row):
                        raise BookingNotPersisted(action)
                    row = cs.finalize_public_booking_payment(
                        row, pending_payment_proof=pending_payment_proof,
                        pending_payment_proofs=pending_payment_proofs,
                        payment_reuse=payment_reuse,
                        idempotency_key=booking_key,
                    )
        except cs.PaymentDueError as e:
            rollback_attempt()
            return _json_error(
                str(e),
                payment_due=True,
                balance_due=e.balance_due,
                name=e.name,
            )
        except cs.SlotBookedError as e:
            rollback_attempt()
            return _json_error(str(e), slot_conflict=True, conflicts=e.conflicts)
        except ValueError as e:
            rollback_attempt()
            return _json_error(str(e))
        except BookingNotPersisted as e:
            rollback_attempt()
            logger.error(
                "Invite booking trace phase=confirm_unpersisted trace_id=%s "
                "image_sha256=%s action=%s",
                trace_id, image_sha256, _invite_trace_value(e.action),
            )
            return _json_error(
                "Booking did not complete. The interview slot was not saved — "
                "try again, and if it repeats report this invite.",
                status=500,
                failure_reason=str(e),
            )
        except Exception as e:
            rollback_attempt()
            logger.exception("Booking confirmation failed")
            return _json_error(
                "Booking confirmation failed. No candidate or booking was created.",
                status=500,
                failure_reason=str(e),
            )

        # Success is reported only for a row that really carries the slot. A
        # response of 200 with an unbooked row is what put "Slot confirmed" on
        # screen while Confirmed slots stayed empty, so it is refused here
        # rather than left to the caller to notice. The same condition is
        # checked before the payment is linked; this is the second reading,
        # after the linkage, and it unwinds the money too.
        if not cs.candidate_has_confirmed_slot(row):
            rollback_attempt()
            logger.error(
                "Invite booking trace phase=confirm_unpersisted trace_id=%s "
                "image_sha256=%s action=%s row_id=%r stored_date=%r stored_time=%r",
                trace_id,
                image_sha256,
                _invite_trace_value(action),
                str(row.get("id") or ""),
                _invite_trace_value(row.get("date")),
                _invite_trace_value(row.get("time")),
            )
            return _json_error(
                "Booking did not complete. The interview slot was not saved — "
                "try again, and if it repeats report this invite.",
                status=500,
                failure_reason=f"confirm_unpersisted action={action}",
            )

        # The booking is committed and verified on the row. Only now is the
        # payment spent -- every earlier exit above leaves it reusable.
        if requested_proof_ids:
            try:
                from features.pending_slot_payment import mark_utilized

                mark_utilized(
                    requested_proof_ids,
                    candidate_id=str(row.get("id") or ""),
                    booking_key=booking_key,
                )
            except Exception:
                # The row attachment is the authoritative consumption record;
                # this marker is the pending-side mirror of it. Failing to
                # write it must not fail a booking that already succeeded.
                logger.exception("Could not mark pending payment proofs utilized")

        logger.warning(
            "Invite booking trace phase=confirm_stored trace_id=%s image_sha256=%s "
            "stored_date=%r stored_time=%r stored_end=%r action=%s",
            trace_id,
            image_sha256,
            _invite_trace_value(row.get("date")),
            _invite_trace_value(row.get("time")),
            _invite_trace_value(row.get("time_end")),
            _invite_trace_value(action),
        )

        async def _notify() -> None:
            try:
                from services.slot_booking_notify import notify_slot_booked

                await notify_slot_booked(row, action=action)
            except Exception as exc:
                logger.debug("slot booking notify failed: %s", exc)

        asyncio.create_task(_notify())
        return {"status": "ok", "action": action, "candidate": row}

    @app.post("/public/slots/book")
    async def retired_public_slot_book():
        return _json_error(
            "This booking endpoint is retired. Use POST /bookings/confirm.",
            status=410,
        )

    @app.post("/public/slots/session-complete")
    async def public_slot_session_complete(
        name: str = Form(...),
        date: str = Form(default=""),
        time: str = Form(default=""),
        file: UploadFile = File(...),
    ):
        try:
            raw = await file.read()
            row, action = cs.mark_session_complete_by_name(
                name,
                date=date,
                time=time,
                source="submit-slot",
                slot_image=raw,
                slot_image_name=file.filename or "session-complete.jpg",
                slot_image_mime=file.content_type or "image/jpeg",
            )
        except ValueError as e:
            return _json_error(str(e))
        return {"status": "ok", "action": action, "candidate": row}
