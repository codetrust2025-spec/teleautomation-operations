"""Independent TeleAutomation Operations API."""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.routers.candidates import router as candidates_router
from api.routers.attendance import router as attendance_router
from api.routers.data_room import router as data_room_router
from api.routers.expenses import router as expenses_router
from api.routers.operations_ai import router as operations_ai_router
from api.routers.referrers import router as referrers_router
from core.dashboard_auth_api import install_dashboard_auth
from core.log_redaction import install_access_log_redaction
from core.public_slot_api import install_public_slot_routes
from core.recruitment_mail_api import install_recruitment_mail_routes

# Before anything can serve a request: the Gmail Pub/Sub push endpoint carries
# its verification token in the query string, and uvicorn's access log would
# otherwise write that live secret to disk on every successful push.
install_access_log_redaction()

app = FastAPI(title="TeleAutomation Operations", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["*"], allow_headers=["*"])

install_dashboard_auth(app)
install_public_slot_routes(app)
install_recruitment_mail_routes(app)
app.include_router(candidates_router)
app.include_router(attendance_router)
app.include_router(expenses_router)
app.include_router(data_room_router)
app.include_router(referrers_router)
app.include_router(operations_ai_router)


class OpportunityCommandV1(BaseModel):
    slot: str = Field(min_length=1, max_length=80)
    user_id: int
    opportunity_type: str = Field(default="partnership", max_length=80)
    name: str = Field(default="", max_length=200)
    username: str = Field(default="", max_length=200)
    tech_stack: str = Field(default="", max_length=500)
    volume_hint: str = Field(default="", max_length=500)
    summary: str = Field(default="", max_length=500)
    source_snippet: str = Field(default="", max_length=1500)
    phone: str = Field(default="", max_length=80)
    whatsapp: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=320)
    notes_append: str = Field(default="", max_length=500)


@app.get("/health")
async def health():
    """Liveness does not depend on Marketing or external providers."""
    return {"status": "ok", "service": "teleautomation-operations"}


@app.get("/version")
async def version():
    """Identify exactly which commit is serving this deployment.

    A deployment that cannot say what it is running cannot be verified, and
    cannot be rolled back with confidence. RELEASE_SHA is baked in at image
    build time; "unknown" means the image was built outside the release path.
    """
    return {
        "service": os.getenv("SERVICE_NAME", "teleautomation-operations"),
        "sha": os.getenv("RELEASE_SHA", "unknown"),
        "built_at": os.getenv("RELEASE_BUILT_AT", "unknown"),
    }


def _require_internal(request: Request) -> None:
    from core.dashboard_access import is_internal_service_authorized

    if not is_internal_service_authorized(request):
        raise HTTPException(status_code=403, detail="Invalid service credential")


def _claim_idempotency_key(request: Request) -> str | None:
    from features.service_inbox import claim

    key = (request.headers.get("X-Idempotency-Key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="X-Idempotency-Key is required")
    return key if claim(key) else None


@app.post("/internal/v1/opportunities", include_in_schema=False)
async def ingest_marketing_opportunity(request: Request, body: OpportunityCommandV1):
    _require_internal(request)
    idempotency_key = _claim_idempotency_key(request)
    if not idempotency_key:
        return {"status": "duplicate"}
    from features import data_room_store
    try:
        payload = body.model_dump()
        row = data_room_store.upsert_from_telegram(
            payload["slot"],
            payload["user_id"],
            opportunity_type=payload["opportunity_type"],
            name=payload["name"],
            username=payload["username"],
            tech_stack=payload["tech_stack"],
            volume_hint=payload["volume_hint"],
            summary=payload["summary"],
            source_snippet=payload["source_snippet"],
            phone=payload["phone"],
            whatsapp=payload["whatsapp"],
            email=payload["email"],
            notes_append=payload["notes_append"],
        )
        return {"status": "ok", "opportunity": row}
    except Exception:
        from features.service_inbox import release

        release(idempotency_key)
        raise


@app.post("/internal/v1/candidates/{candidate_id}/payment-proofs", include_in_schema=False)
async def ingest_marketing_payment_proof(
    candidate_id: str,
    request: Request,
    file: UploadFile,
    note: str = Form(default=""),
    source_module: str = Form(default="marketing"),
    upload_context: str = Form(default=""),
):
    _require_internal(request)
    idempotency_key = _claim_idempotency_key(request)
    if not idempotency_key:
        return {"status": "duplicate"}
    try:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Payment proof file is required")
        from features import candidate_store

        proof = await asyncio.to_thread(
            candidate_store.add_payment_proof,
            candidate_id,
            data=raw,
            original_name=file.filename or "marketing-payment-proof",
            mime_type=file.content_type or "application/octet-stream",
            note=note[:200],
            metadata={
                "source_module": source_module[:80],
                "source_endpoint": "operations_internal_payment_proof",
                "upload_context": upload_context[:160],
            },
        )
        if proof is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return {"status": "ok", "payment_proof": proof}
    except Exception:
        from features.service_inbox import release

        release(idempotency_key)
        raise


@app.on_event("startup")
async def startup() -> None:
    if os.getenv("TELEAUTOMATION_SAFE_UI_MODE", "false").lower() in {"1", "true", "yes"}:
        return
    from core.recruitment_mail_store import ensure_schema
    from core.migrations.runner import apply_migrations
    from services.gmail_watch_renewal_loop import start_gmail_watch_renewal_loop
    from services.interview_reminder_loop import start_interview_reminder_loop
    from services.messaging_client import start_outbox_dispatcher
    from workers.recruitment_mail_worker import recruitment_mail_worker

    from core import recruitment_realtime

    apply_migrations()
    ensure_schema()
    # Started here rather than on first WebSocket connect: the tailer delivers
    # events written by any process, and it needs the loop registered before a
    # publisher runs, not after a browser happens to arrive.
    recruitment_realtime.start_tailer()
    recruitment_realtime.start_activity_pusher()
    recruitment_mail_worker.start()
    start_interview_reminder_loop()
    start_gmail_watch_renewal_loop()
    start_outbox_dispatcher()


@app.on_event("shutdown")
async def shutdown() -> None:
    from services.gmail_watch_renewal_loop import stop_gmail_watch_renewal_loop
    from services.interview_reminder_loop import stop_interview_reminder_loop
    from services.messaging_client import stop_outbox_dispatcher
    from workers.recruitment_mail_worker import recruitment_mail_worker

    from core import recruitment_realtime

    await stop_gmail_watch_renewal_loop()
    await stop_interview_reminder_loop()
    await stop_outbox_dispatcher()
    await recruitment_realtime.stop_tailer()
    await recruitment_realtime.stop_activity_pusher()
    await recruitment_mail_worker.stop()


# Register every concrete API root after all routers are mounted. This closes
# the SPA fall-through authorization gap for newly-added route families.
from core import dashboard_auth_vps as dashboard_auth

dashboard_auth.register_api_roots(app)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")
    _NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=_NO_CACHE)

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        first = full_path.split("/")[0] if full_path else ""
        if first in dashboard_auth.api_roots():
            raise HTTPException(status_code=404, detail="Not Found")
        file_path = os.path.join(STATIC_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=_NO_CACHE)
