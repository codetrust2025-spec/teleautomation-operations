"""Operations attendance, holiday, eligibility, and payroll-review API."""

from __future__ import annotations

import asyncio
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import dashboard_auth_vps as auth
from core.ist_time import IST
from core.office_network import verify_office_network
from features import attendance_eligibility as attendance
from features import daily_checkout


router = APIRouter(prefix="/attendance", tags=["attendance"])


class HolidayBody(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=160)


class RecommendationReviewBody(BaseModel):
    decision: str
    note: str = Field(default="", max_length=500)


def _profile(request: Request) -> dict:
    profile = auth.operator_profile_from_cookies(dict(request.cookies))
    if not profile.get("username"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return profile


def _payroll_admin(request: Request) -> dict:
    profile = _profile(request)
    if not auth.is_payroll_admin_profile(profile):
        raise HTTPException(status_code=403, detail="Payroll administrator access required")
    return profile


def _attendance_handler_profiles() -> list[dict]:
    inventory = auth.audit_operator_accounts()
    profiles = []
    for row in inventory["users"]:
        if str(row.get("role") or "").casefold() != "handler":
            continue
        if not row.get("active") or row.get("orphaned"):
            continue
        profiles.append({
            "username": row["username"],
            "role": "handler",
            "reference": row["name"],
            "display_name": row["name"],
            "account_id": row["account_id"],
        })
    return profiles


async def _call(function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except attendance.AttendanceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status")
async def attendance_status(request: Request):
    return await _call(attendance.status, _profile(request))


@router.post("/mark")
async def attendance_mark(request: Request):
    profile = _profile(request)
    try:
        return await _call(attendance.mark_attendance, profile, _network(request))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _network(request: Request):
    """The same verification attendance marking uses, read the same way."""
    return verify_office_network(
        request.client.host if request.client else "",
        request.headers.get("x-forwarded-for", ""),
    )


@router.get("/checkout")
async def attendance_checkout_status(request: Request):
    """What check-out would do, without doing it: the panel polls this."""
    profile = _profile(request)
    try:
        return await _call(daily_checkout.status, profile, _network(request))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/checkout")
async def attendance_checkout(request: Request):
    profile = _profile(request)
    try:
        return await _call(daily_checkout.check_out, profile, _network(request))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except daily_checkout.CheckoutBlocked as exc:
        # 409, not 403: nothing is wrong with who is asking, the day is simply
        # not finished. The body carries every blocker so the checklist on the
        # page can render them all at once.
        raise HTTPException(
            status_code=409,
            detail={"message": "The working day is not finished yet.",
                    "blockers": exc.blockers},
        ) from exc


@router.get("/holidays")
async def attendance_holidays(
    request: Request,
    year: int | None = Query(default=None, ge=2000, le=2200),
    month: int | None = Query(default=None, ge=1, le=12),
):
    _profile(request)
    business_year = year or datetime.now(IST).year
    return {"status": "ok", "holidays": await _call(attendance.list_holidays, year=business_year, month=month)}


@router.post("/holidays")
async def attendance_holiday_upsert(request: Request, body: HolidayBody):
    actor = _payroll_admin(request)
    row = await _call(attendance.upsert_holiday, body.holiday_date, body.name, actor)
    return {"status": "ok", "holiday": row}


@router.delete("/holidays/{holiday_date}")
async def attendance_holiday_remove(request: Request, holiday_date: date):
    actor = _payroll_admin(request)
    removed = await _call(attendance.remove_holiday, holiday_date, actor)
    if not removed:
        raise HTTPException(status_code=404, detail="Active holiday not found")
    return {"status": "ok", "holiday_date": holiday_date.isoformat()}


@router.get("/salary-recommendations")
async def salary_recommendations(
    request: Request,
    review_status: str = Query(default="PENDING", max_length=40),
):
    _payroll_admin(request)
    rows = await _call(attendance.list_recommendations, review_status)
    return {"status": "ok", "recommendations": rows}


@router.post("/salary-recommendations/{recommendation_id}/review")
async def salary_recommendation_review(
    request: Request,
    recommendation_id: str,
    body: RecommendationReviewBody,
):
    actor = _payroll_admin(request)
    try:
        row = await _call(
            attendance.review_recommendation,
            recommendation_id,
            body.decision,
            actor,
            note=body.note,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except attendance.RecommendationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "recommendation": row}


@router.get("/admin/users")
async def operations_user_audit(request: Request):
    _payroll_admin(request)
    inventory = await asyncio.to_thread(auth.audit_operator_accounts)
    account_ids = [row["account_id"] for row in inventory["users"]]
    last_logins = await _call(attendance.last_login_by_account_ids, account_ids)
    users = []
    for row in inventory["users"]:
        clean = dict(row)
        clean["last_login"] = last_logins.get(row["account_id"])
        clean["account_source"] = ", ".join(clean.pop("account_sources"))
        users.append(clean)
    return {
        "status": "ok",
        "users": users,
        "findings": {
            "duplicate_identity_groups": inventory["duplicate_identity_groups"],
            "orphaned_usernames": inventory["orphaned_usernames"],
            "inactive_usernames": [row["username"] for row in users if not row["active"]],
            "without_password_usernames": [row["username"] for row in users if not row["password_configured"]],
            "multiple_auth_source_usernames": [
                row["username"] for row in users if "," in row["account_source"]
            ],
        },
    }


@router.get("/admin/overview")
async def attendance_admin_overview(request: Request):
    _payroll_admin(request)
    profiles = await asyncio.to_thread(_attendance_handler_profiles)
    return await _call(attendance.admin_overview, profiles)


@router.get("/admin/history")
async def attendance_admin_history(
    request: Request,
    account_id: str = Query(min_length=1, max_length=200),
):
    _payroll_admin(request)
    profiles = await asyncio.to_thread(_attendance_handler_profiles)
    profile = next((row for row in profiles if row["account_id"] == account_id), None)
    if not profile:
        raise HTTPException(status_code=404, detail="Handler account not found")
    return await _call(attendance.admin_history, profile)
