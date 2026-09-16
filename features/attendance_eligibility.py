"""Authoritative working-day attendance and earnings-eligibility service."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Iterable

from core.db.connection import get_connection, use_postgres
from core.ist_time import APP_TIMEZONE, IST
from core.office_network import NetworkVerification


NORMAL_ELIGIBILITY = 15_000
PERFECT_ATTENDANCE_ELIGIBILITY = 40_000
ATTENDANCE_START = time(9, 0)
POLICY_KEY = "default"


class AttendanceUnavailable(RuntimeError):
    pass


class RecommendationConflict(RuntimeError):
    pass


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    return current.astimezone(IST)


def _as_dict(cursor, row) -> dict[str, Any] | None:
    if row is None:
        return None
    return {str(column[0]): value for column, value in zip(cursor.description, row)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def account_identity(profile: dict[str, Any]) -> dict[str, str]:
    username = str(profile.get("username") or "").strip()
    role = str(profile.get("role") or "").strip().lower()
    if not username or role not in {"admin", "handler"}:
        raise ValueError("Authenticated Operations user is required")
    display_name = str(profile.get("display_name") or profile.get("reference") or username).strip()
    account_id = str(profile.get("account_id") or f"{role}:{username.casefold()}").strip()
    salary_reference = str(profile.get("reference") or display_name).strip()
    return {
        "account_id": account_id,
        "username": username,
        "display_name": display_name,
        "role": role,
        "salary_reference": salary_reference,
    }


def is_employee_identity(identity: dict[str, Any]) -> bool:
    """Only handler accounts participate in employee attendance/payroll policy."""
    return (
        str(identity.get("role") or "").casefold() == "handler"
        and str(identity.get("account_id") or "").casefold().startswith("handler:")
    )


def configured_effective_date(default: date) -> date:
    raw = str(os.environ.get("OPERATIONS_ATTENDANCE_EFFECTIVE_DATE") or "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return default


def required_working_dates(start: date, end: date, holidays: Iterable[date]) -> list[date]:
    if end < start:
        return []
    excluded = set(holidays)
    values: list[date] = []
    current = start
    while current <= end:
        if current.weekday() != 6 and current not in excluded:
            values.append(current)
        current += timedelta(days=1)
    return values


def evaluation_period(current: datetime, effective_date: date) -> tuple[date, date]:
    local = _now(current)
    start = max(local.date().replace(day=1), effective_date)
    # Today does not become a missed required day until attendance opens at 09:00.
    end = local.date() if local.time() >= ATTENDANCE_START else local.date() - timedelta(days=1)
    return start, end


def eligibility_for_counts(attended: int, required: int) -> tuple[float | None, int]:
    if required <= 0:
        return None, NORMAL_ELIGIBILITY
    ratio = round((attended / required) * 100, 4)
    amount = PERFECT_ATTENDANCE_ELIGIBILITY if attended == required else NORMAL_ELIGIBILITY
    return ratio, amount


def popup_decision(
    current: datetime,
    *,
    effective_date: date,
    holiday_dates: Iterable[date],
    already_marked: bool,
) -> dict[str, Any]:
    local = _now(current)
    today = local.date()
    holiday_set = set(holiday_dates)
    base = {
        "attendance_date": today.isoformat(),
        "server_time": local.isoformat(timespec="seconds"),
        "business_timezone": APP_TIMEZONE,
        "eligible_at": datetime.combine(today, ATTENDANCE_START, tzinfo=IST).isoformat(),
        "eligible": False,
        "working_day": False,
        "marked": already_marked,
    }
    if today < effective_date:
        return {**base, "reason": "POLICY_NOT_EFFECTIVE", "next_check_at": datetime.combine(effective_date, ATTENDANCE_START, tzinfo=IST).isoformat()}
    if today.weekday() == 6:
        return {**base, "reason": "SUNDAY", "next_check_at": datetime.combine(today + timedelta(days=1), ATTENDANCE_START, tzinfo=IST).isoformat()}
    if today in holiday_set:
        return {**base, "reason": "PUBLIC_HOLIDAY", "next_check_at": datetime.combine(today + timedelta(days=1), ATTENDANCE_START, tzinfo=IST).isoformat()}
    base["working_day"] = True
    if already_marked:
        return {**base, "reason": "ALREADY_MARKED", "next_check_at": datetime.combine(today + timedelta(days=1), ATTENDANCE_START, tzinfo=IST).isoformat()}
    if local.time() < ATTENDANCE_START:
        return {**base, "reason": "BEFORE_START_TIME", "next_check_at": base["eligible_at"]}
    return {**base, "eligible": True, "reason": "ATTENDANCE_REQUIRED", "next_check_at": (local + timedelta(minutes=5)).isoformat()}


def _require_database() -> None:
    if not use_postgres():
        raise AttendanceUnavailable("Attendance database is not configured")


def _policy(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT effective_date, business_timezone, attendance_start_time
               FROM operations_attendance_policy WHERE policy_key=%s""",
            (POLICY_KEY,),
        )
        row = cur.fetchone()
    if not row:
        raise AttendanceUnavailable("Attendance policy is not initialized")
    return {
        "effective_date": configured_effective_date(row[0]),
        "business_timezone": str(row[1] or APP_TIMEZONE),
        "attendance_start_time": row[2] or ATTENDANCE_START,
    }


def _holidays(conn, start: date, end: date) -> dict[date, str]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT holiday_date, name FROM operations_public_holidays
               WHERE active AND holiday_date BETWEEN %s AND %s""",
            (start, end),
        )
        return {row[0]: str(row[1]) for row in cur.fetchall()}


def _attendance_row(conn, account_id: str, attendance_date: date) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, account_id, username, display_name, attendance_date,
                      marked_at, status, office_network_verified
               FROM operations_attendance_records
               WHERE account_id=%s AND attendance_date=%s""",
            (account_id, attendance_date),
        )
        return _as_dict(cur, cur.fetchone())


def _attended_dates(conn, account_id: str, start: date, end: date) -> set[date]:
    if end < start:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT attendance_date FROM operations_attendance_records
               WHERE account_id=%s AND attendance_date BETWEEN %s AND %s
                 AND status='VERIFIED' AND office_network_verified""",
            (account_id, start, end),
        )
        return {row[0] for row in cur.fetchall()}


def _eligibility_reason(attended: int, required: int, amount: int) -> str:
    if required <= 0:
        return "No required working days have elapsed in the evaluation period"
    if amount == PERFECT_ATTENDANCE_ELIGIBILITY:
        return "100% verified attendance for required working days"
    return "Verified attendance is below 100% for required working days"


def _eligibility_calculation(
    conn,
    identity: dict[str, str],
    current: datetime,
    policy: dict[str, Any],
) -> dict[str, Any]:
    period_start, calculation_end = evaluation_period(current, policy["effective_date"])
    # The first effective day before 09:00 has no elapsed required days. Keep
    # that calculation empty without displaying an inverted period.
    period_end = max(period_start, calculation_end)
    holidays = _holidays(conn, period_start, calculation_end) if calculation_end >= period_start else {}
    required_dates = required_working_dates(period_start, calculation_end, holidays)
    attended_dates = _attended_dates(conn, identity["account_id"], period_start, calculation_end)
    attended = len(set(required_dates) & attended_dates)
    required = len(required_dates)
    ratio, amount = eligibility_for_counts(attended, required)
    return {
        "period_start": period_start,
        "period_end": period_end,
        "attended_working_days": attended,
        "required_working_days": required,
        "attendance_ratio": ratio,
        "eligibility_amount": amount,
        "reason": _eligibility_reason(attended, required, amount),
    }


def _eligibility_response(calculation: dict[str, Any]) -> dict[str, Any]:
    amount = int(calculation["eligibility_amount"])
    return {
        "period_start": calculation["period_start"].isoformat(),
        "period_end": calculation["period_end"].isoformat(),
        "attended_working_days": calculation["attended_working_days"],
        "required_working_days": calculation["required_working_days"],
        "attendance_ratio": calculation["attendance_ratio"],
        "eligibility_amount": amount,
        "eligibility_label": f"Eligible for ₹{amount:,}",
        "reason": calculation["reason"],
    }


def _reconcile_eligibility(
    conn,
    identity: dict[str, str],
    current: datetime,
    policy: dict[str, Any],
) -> dict[str, Any]:
    if not is_employee_identity(identity):
        raise ValueError("Earnings eligibility is only available to handler accounts")
    calculation = _eligibility_calculation(conn, identity, current, policy)
    period_start = calculation["period_start"]
    period_end = calculation["period_end"]
    attended = calculation["attended_working_days"]
    required = calculation["required_working_days"]
    ratio = calculation["attendance_ratio"]
    amount = calculation["eligibility_amount"]
    reason = calculation["reason"]
    lock_key = f"attendance-eligibility:{identity['account_id']}:{period_start.isoformat()}"

    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
        cur.execute(
            """SELECT eligibility_amount, attended_working_days, required_working_days,
                      attendance_ratio, period_end
               FROM operations_earnings_eligibility_state
               WHERE account_id=%s AND period_start=%s FOR UPDATE""",
            (identity["account_id"], period_start),
        )
        previous = cur.fetchone()
        previous_amount = int(previous[0]) if previous else None
        previous_ratio = float(previous[3]) if previous and previous[3] is not None else None
        changed = not previous or (
            previous_amount != amount
            or int(previous[1]) != attended
            or int(previous[2]) != required
            or previous_ratio != ratio
            or previous[4] != period_end
        )
        cur.execute(
            """INSERT INTO operations_earnings_eligibility_state(
                   account_id, period_start, period_end, username, display_name,
                   salary_reference, attended_working_days, required_working_days,
                   attendance_ratio, eligibility_amount, calculation_reason, calculated_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(account_id, period_start) DO UPDATE SET
                   period_end=EXCLUDED.period_end,
                   username=EXCLUDED.username,
                   display_name=EXCLUDED.display_name,
                   salary_reference=EXCLUDED.salary_reference,
                   attended_working_days=EXCLUDED.attended_working_days,
                   required_working_days=EXCLUDED.required_working_days,
                   attendance_ratio=EXCLUDED.attendance_ratio,
                   eligibility_amount=EXCLUDED.eligibility_amount,
                   calculation_reason=EXCLUDED.calculation_reason,
                   calculated_at=EXCLUDED.calculated_at""",
            (
                identity["account_id"], period_start, period_end,
                identity["username"], identity["display_name"], identity["salary_reference"],
                attended, required, ratio, amount, reason, current,
            ),
        )
        if changed:
            event_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO operations_earnings_eligibility_events(
                       id, account_id, username, display_name, salary_reference,
                       period_start, period_end, previous_eligibility_amount,
                       new_eligibility_amount, attended_working_days,
                       required_working_days, attendance_ratio, reason, calculated_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    event_id, identity["account_id"], identity["username"],
                    identity["display_name"], identity["salary_reference"],
                    period_start, period_end, previous_amount, amount,
                    attended, required, ratio, reason, current,
                ),
            )
            if previous_amount is not None and previous_amount != amount:
                cur.execute(
                    """UPDATE operations_salary_change_recommendations
                       SET review_status='SUPERSEDED', reviewed_at=%s,
                           review_note='Superseded by a newer attendance eligibility calculation'
                       WHERE account_id=%s AND period_start=%s AND review_status='PENDING'""",
                    (current, identity["account_id"], period_start),
                )
                cur.execute(
                    """INSERT INTO operations_salary_change_recommendations(
                           id, eligibility_event_id, account_id, username, display_name,
                           salary_reference, period_start, period_end,
                           previous_eligibility_amount, recommended_eligibility_amount,
                           attendance_ratio, attended_working_days, required_working_days,
                           reason, review_status, calculated_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)""",
                    (
                        str(uuid.uuid4()), event_id, identity["account_id"], identity["username"],
                        identity["display_name"], identity["salary_reference"], period_start,
                        period_end, previous_amount, amount, ratio, attended, required, reason, current,
                    ),
                )

    return _eligibility_response(calculation)


def status(profile: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    _require_database()
    current = _now(now)
    identity = account_identity(profile)
    with get_connection() as conn:
        policy = _policy(conn)
        today_holidays = _holidays(conn, current.date(), current.date())
        attendance = _attendance_row(conn, identity["account_id"], current.date())
        decision = popup_decision(
            current,
            effective_date=policy["effective_date"],
            holiday_dates=today_holidays,
            already_marked=attendance is not None,
        )
        employee_attendance = is_employee_identity(identity)
        if employee_attendance:
            eligibility = _reconcile_eligibility(conn, identity, current, policy)
        else:
            eligibility = None
            decision = {
                **decision,
                "eligible": False,
                "working_day": False,
                "reason": "NON_EMPLOYEE_ACCOUNT",
            }
        month_start = max(current.date().replace(day=1), policy["effective_date"])
        records = []
        with conn.cursor() as cur:
            cur.execute(
                """SELECT attendance_date, marked_at, status, office_network_verified
                   FROM operations_attendance_records
                   WHERE account_id=%s AND attendance_date BETWEEN %s AND %s
                   ORDER BY attendance_date DESC""",
                (identity["account_id"], month_start, current.date()),
            )
            records = [
                {
                    "attendance_date": row[0], "marked_at": row[1],
                    "status": row[2], "office_network_verified": row[3],
                }
                for row in cur.fetchall()
            ]
    return _json_safe({
        "status": "ok",
        "profile": identity,
        "policy": policy,
        "attendance": attendance,
        "popup": decision,
        "eligibility": eligibility,
        "records": records,
        "employee_attendance": employee_attendance,
    })


def _handler_profiles(profiles: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    identities: dict[str, dict[str, str]] = {}
    for profile in profiles:
        identity = account_identity(profile)
        if is_employee_identity(identity):
            identities[identity["account_id"]] = identity
    return sorted(identities.values(), key=lambda row: row["display_name"].casefold())


def admin_overview(
    profiles: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a read-only, account-isolated summary for every handler."""
    _require_database()
    current = _now(now)
    identities = _handler_profiles(profiles)
    with get_connection() as conn:
        policy = _policy(conn)
        period_start, calculation_end = evaluation_period(current, policy["effective_date"])
        period_end = max(period_start, calculation_end)
        holiday_start = min(period_start, current.date())
        holiday_end = max(calculation_end, current.date())
        holidays = _holidays(conn, holiday_start, holiday_end)
        required_dates = required_working_dates(period_start, calculation_end, holidays)
        required_set = set(required_dates)
        account_ids = [identity["account_id"] for identity in identities]
        rows_by_account: dict[str, list[dict[str, Any]]] = {account_id: [] for account_id in account_ids}
        if account_ids:
            query_start = min(period_start, current.date())
            query_end = max(period_end, current.date())
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT account_id, attendance_date, marked_at, status, office_network_verified
                       FROM operations_attendance_records
                       WHERE account_id=ANY(%s) AND attendance_date BETWEEN %s AND %s
                       ORDER BY attendance_date DESC""",
                    (account_ids, query_start, query_end),
                )
                for row in cur.fetchall():
                    rows_by_account.setdefault(str(row[0]), []).append({
                        "attendance_date": row[1],
                        "marked_at": row[2],
                        "status": row[3],
                        "office_network_verified": bool(row[4]),
                    })

        summaries = []
        for identity in identities:
            account_rows = rows_by_account.get(identity["account_id"], [])
            today_row = next(
                (row for row in account_rows if row["attendance_date"] == current.date()),
                None,
            )
            attended_dates = {
                row["attendance_date"]
                for row in account_rows
                if row["status"] == "VERIFIED" and row["office_network_verified"]
            }
            attended = len(required_set & attended_dates)
            required = len(required_dates)
            ratio, amount = eligibility_for_counts(attended, required)
            decision = popup_decision(
                current,
                effective_date=policy["effective_date"],
                holiday_dates=holidays,
                already_marked=today_row is not None,
            )
            summaries.append({
                "account_id": identity["account_id"],
                "username": identity["username"],
                "display_name": identity["display_name"],
                "today_status": (
                    "VERIFIED" if today_row
                    else "NOT_REQUIRED" if not decision["working_day"]
                    else "NOT_OPEN" if decision["reason"] == "BEFORE_START_TIME"
                    else "NOT_MARKED"
                ),
                "today_reason": decision["reason"],
                "marked_at": today_row["marked_at"] if today_row else None,
                "office_network_verified": (
                    today_row["office_network_verified"] if today_row else None
                ),
                "attended_working_days": attended,
                "required_working_days": required,
                "attendance_ratio": ratio,
                "eligibility_amount": amount,
            })

    return _json_safe({
        "status": "ok",
        "period_start": period_start,
        "period_end": period_end,
        "attendance_date": current.date(),
        "users": summaries,
    })


def admin_history(
    profile: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one handler's history without changing eligibility/audit state."""
    _require_database()
    current = _now(now)
    identity = account_identity(profile)
    if not is_employee_identity(identity):
        raise ValueError("Attendance history is only available for handler accounts")
    with get_connection() as conn:
        policy = _policy(conn)
        calculation = _eligibility_calculation(conn, identity, current, policy)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT attendance_date, marked_at, status, office_network_verified
                   FROM operations_attendance_records
                   WHERE account_id=%s AND attendance_date BETWEEN %s AND %s
                   ORDER BY attendance_date DESC""",
                (identity["account_id"], policy["effective_date"], current.date()),
            )
            records = [
                {
                    "attendance_date": row[0],
                    "marked_at": row[1],
                    "status": row[2],
                    "office_network_verified": bool(row[3]),
                }
                for row in cur.fetchall()
            ]
    return _json_safe({
        "status": "ok",
        "profile": identity,
        "eligibility": _eligibility_response(calculation),
        "records": records,
    })


def mark_attendance(
    profile: dict[str, Any],
    network: NetworkVerification,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    _require_database()
    current = _now(now)
    identity = account_identity(profile)
    if not is_employee_identity(identity):
        raise PermissionError("Attendance is only available to handler accounts.")
    with get_connection() as conn:
        policy = _policy(conn)
        holidays = _holidays(conn, current.date(), current.date())
        existing = _attendance_row(conn, identity["account_id"], current.date())
        decision = popup_decision(
            current,
            effective_date=policy["effective_date"],
            holiday_dates=holidays,
            already_marked=existing is not None,
        )
        if existing:
            return _json_safe({"status": "already_marked", "attendance": existing})
        if not decision["working_day"] or not decision["eligible"]:
            raise ValueError(decision["reason"])
        if not network.allowed:
            # Naming the address changes nothing about who is allowed in -- the
            # allowlist still decides -- but it turns "I am in the office and it
            # says I am not" into a value an admin can check against the policy.
            seen = str(getattr(network, "observed_ip", "") or "")
            raise PermissionError(
                "Connect to Office Wi-Fi to mark attendance."
                + (f" This device reaches us from {seen}, which is not an approved office network."
                   if seen else "")
            )
        record_id = str(uuid.uuid4())
        audit = {
            "source": "authenticated_operations_session",
            "business_timezone": APP_TIMEZONE,
            "session_id_hash": str(profile.get("session_id_hash") or ""),
        }
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO operations_attendance_records(
                       id, account_id, username, display_name, attendance_date,
                       marked_at, status, office_network_verified,
                       network_verification, audit_metadata)
                   VALUES(%s,%s,%s,%s,%s,%s,'VERIFIED',TRUE,%s::jsonb,%s::jsonb)
                   ON CONFLICT(account_id, attendance_date) DO NOTHING
                   RETURNING id, account_id, username, display_name, attendance_date,
                             marked_at, status, office_network_verified""",
                (
                    record_id, identity["account_id"], identity["username"],
                    identity["display_name"], current.date(), current,
                    json.dumps(network.audit_payload()), json.dumps(audit),
                ),
            )
            row = _as_dict(cur, cur.fetchone())
        if row is None:
            row = _attendance_row(conn, identity["account_id"], current.date())
            result_status = "already_marked"
        else:
            result_status = "marked"
        eligibility = _reconcile_eligibility(conn, identity, current, policy)
    return _json_safe({"status": result_status, "attendance": row, "eligibility": eligibility})


def record_auth_activity(profile: dict[str, Any], activity_type: str, *, now: datetime | None = None) -> None:
    if not use_postgres():
        return
    identity = account_identity(profile)
    activity = str(activity_type or "").upper()
    if activity not in {"LOGIN", "LOGOUT"}:
        raise ValueError("Unsupported auth activity")
    session_hash = str(profile.get("session_id_hash") or "") or None
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operations_auth_activity(
                   id, account_id, username, display_name, role, activity_type,
                   session_id_hash, happened_at, audit_metadata)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
            (
                str(uuid.uuid4()), identity["account_id"], identity["username"],
                identity["display_name"], identity["role"], activity, session_hash,
                _now(now), json.dumps({"source": "operations_auth"}),
            ),
        )


def list_holidays(*, year: int, month: int | None = None) -> list[dict[str, Any]]:
    _require_database()
    start = date(year, month or 1, 1)
    end = date(year, month or 12, monthrange(year, month or 12)[1])
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT holiday_date, name, active, created_by_account_id, created_at,
                      updated_by_account_id, updated_at
               FROM operations_public_holidays
               WHERE holiday_date BETWEEN %s AND %s ORDER BY holiday_date""",
            (start, end),
        )
        return _json_safe([_as_dict(cur, row) for row in cur.fetchall()])


def upsert_holiday(holiday_date: date, name: str, actor_profile: dict[str, Any]) -> dict[str, Any]:
    _require_database()
    actor = account_identity(actor_profile)
    label = str(name or "").strip()
    if not label:
        raise ValueError("Holiday name is required")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operations_public_holidays(
                   holiday_date, name, active, created_by_account_id,
                   updated_by_account_id, updated_at)
               VALUES(%s,%s,TRUE,%s,%s,NOW())
               ON CONFLICT(holiday_date) DO UPDATE SET
                   name=EXCLUDED.name, active=TRUE,
                   updated_by_account_id=EXCLUDED.updated_by_account_id,
                   updated_at=NOW()
               RETURNING holiday_date, name, active, created_by_account_id,
                         created_at, updated_by_account_id, updated_at""",
            (holiday_date, label, actor["account_id"], actor["account_id"]),
        )
        return _json_safe(_as_dict(cur, cur.fetchone()))


def remove_holiday(holiday_date: date, actor_profile: dict[str, Any]) -> bool:
    _require_database()
    actor = account_identity(actor_profile)
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE operations_public_holidays
               SET active=FALSE, updated_by_account_id=%s, updated_at=NOW()
               WHERE holiday_date=%s AND active""",
            (actor["account_id"], holiday_date),
        )
        return cur.rowcount == 1


def list_recommendations(status_filter: str = "PENDING") -> list[dict[str, Any]]:
    _require_database()
    status_value = str(status_filter or "PENDING").upper()
    with get_connection() as conn, conn.cursor() as cur:
        if status_value == "ALL":
            cur.execute(
                """SELECT * FROM operations_salary_change_recommendations
                   WHERE account_id LIKE %s
                   ORDER BY calculated_at DESC LIMIT 500""",
                ("handler:%",),
            )
        else:
            cur.execute(
                """SELECT * FROM operations_salary_change_recommendations
                   WHERE account_id LIKE %s AND review_status=%s
                   ORDER BY calculated_at DESC LIMIT 500""",
                ("handler:%", status_value),
            )
        return _json_safe([_as_dict(cur, row) for row in cur.fetchall()])


def _current_salary(reference: str) -> tuple[int, str]:
    from features import handler_salaries

    match = next(
        (
            row for row in handler_salaries.list_salaries()
            if str(row.get("reference") or "").casefold() == reference.casefold()
        ),
        None,
    )
    return int((match or {}).get("monthly_salary") or 0), str((match or {}).get("active_until") or "")


def review_recommendation(
    recommendation_id: str,
    decision: str,
    actor_profile: dict[str, Any],
    *,
    note: str = "",
) -> dict[str, Any]:
    _require_database()
    actor = account_identity(actor_profile)
    action = str(decision or "").upper()
    if action not in {"APPROVE", "REJECT"}:
        raise ValueError("Decision must be APPROVE or REJECT")
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operations_salary_change_recommendations WHERE id=%s FOR UPDATE",
            (recommendation_id,),
        )
        recommendation = _as_dict(cur, cur.fetchone())
        if not recommendation:
            raise KeyError("Recommendation not found")
        if not str(recommendation.get("account_id") or "").casefold().startswith("handler:"):
            raise RecommendationConflict("Generic admin accounts are not eligible for salary changes")
        if recommendation["review_status"] != "PENDING":
            raise RecommendationConflict("Recommendation is no longer pending")
        if action == "REJECT":
            cur.execute(
                """UPDATE operations_salary_change_recommendations
                   SET review_status='REJECTED', reviewed_by_account_id=%s,
                       reviewed_at=NOW(), review_note=%s WHERE id=%s RETURNING *""",
                (actor["account_id"], str(note or "")[:500], recommendation_id),
            )
            return _json_safe(_as_dict(cur, cur.fetchone()))
        cur.execute(
            """SELECT eligibility_amount FROM operations_earnings_eligibility_state
               WHERE account_id=%s AND period_start=%s""",
            (recommendation["account_id"], recommendation["period_start"]),
        )
        current = cur.fetchone()
        if not current or int(current[0]) != int(recommendation["recommended_eligibility_amount"]):
            raise RecommendationConflict("Recommendation no longer matches current eligibility")
        cur.execute(
            """UPDATE operations_salary_change_recommendations
               SET review_status='APPROVED_PENDING_APPLY', reviewed_by_account_id=%s,
                   reviewed_at=NOW(), review_note=%s WHERE id=%s""",
            (actor["account_id"], str(note or "")[:500], recommendation_id),
        )

    before, active_until = _current_salary(str(recommendation["salary_reference"]))
    target = int(recommendation["recommended_eligibility_amount"])
    try:
        from features import handler_salaries

        handler_salaries.set_salary(
            str(recommendation["salary_reference"]),
            target,
            str(recommendation["period_start"])[:7],
            active_until,
        )
    except Exception as exc:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE operations_salary_change_recommendations
                   SET review_status='APPLY_FAILED', salary_amount_before=%s,
                       review_note=LEFT(COALESCE(review_note,'') || %s, 500)
                   WHERE id=%s RETURNING *""",
                (before, " | Salary store update failed", recommendation_id),
            )
            failed = _as_dict(cur, cur.fetchone())
        raise AttendanceUnavailable("Approved salary change could not be applied") from exc

    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE operations_salary_change_recommendations
               SET review_status='APPLIED', salary_amount_before=%s,
                   salary_amount_after=%s, applied_at=NOW()
               WHERE id=%s AND review_status='APPROVED_PENDING_APPLY'
               RETURNING *""",
            (before, target, recommendation_id),
        )
        applied = _as_dict(cur, cur.fetchone())
    if not applied:
        raise RecommendationConflict("Salary was applied but audit finalization needs review")
    return _json_safe(applied)


def last_login_by_account_ids(account_ids: Iterable[str]) -> dict[str, str | None]:
    ids = sorted({str(value) for value in account_ids if str(value)})
    if not ids or not use_postgres():
        return {value: None for value in ids}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT account_id, MAX(happened_at) FROM operations_auth_activity
               WHERE activity_type='LOGIN' AND account_id=ANY(%s) GROUP BY account_id""",
            (ids,),
        )
        found = {str(row[0]): row[1].isoformat() for row in cur.fetchall()}
    return {value: found.get(value) for value in ids}


def session_id_hash(session_id: str) -> str:
    value = str(session_id or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20] if value else ""
