from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from core import dashboard_auth_vps as auth
from core.office_network import verify_office_network
from features import attendance_eligibility as attendance


IST = ZoneInfo("Asia/Kolkata")


def at(hour: int, minute: int = 0, day: int = 1) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=IST)


def test_before_nine_is_not_eligible_but_nine_ten_and_one_pm_are():
    before = attendance.popup_decision(
        at(8, 30), effective_date=date(2026, 9, 1), holiday_dates=[], already_marked=False
    )
    assert before["eligible"] is False
    assert before["reason"] == "BEFORE_START_TIME"
    assert before["next_check_at"].startswith("2026-09-01T09:00:00")

    for current in (at(9, 10), at(13, 0)):
        decision = attendance.popup_decision(
            current, effective_date=date(2026, 9, 1), holiday_dates=[], already_marked=False
        )
        assert decision["eligible"] is True
        assert decision["reason"] == "ATTENDANCE_REQUIRED"


def test_already_marked_never_becomes_eligible_again_that_day():
    decision = attendance.popup_decision(
        at(13), effective_date=date(2026, 9, 1), holiday_dates=[], already_marked=True
    )
    assert decision["eligible"] is False
    assert decision["marked"] is True
    assert decision["reason"] == "ALREADY_MARKED"
    assert decision["next_check_at"].startswith("2026-09-02T09:00:00")


def test_sunday_and_public_holiday_are_not_working_days():
    sunday = attendance.popup_decision(
        at(10, day=6), effective_date=date(2026, 9, 1), holiday_dates=[], already_marked=False
    )
    holiday = attendance.popup_decision(
        at(10, day=2), effective_date=date(2026, 9, 1),
        holiday_dates=[date(2026, 9, 2)], already_marked=False,
    )
    assert (sunday["working_day"], sunday["eligible"], sunday["reason"]) == (False, False, "SUNDAY")
    assert (holiday["working_day"], holiday["eligible"], holiday["reason"]) == (False, False, "PUBLIC_HOLIDAY")


def test_sundays_and_holidays_are_excluded_from_ratio_denominator():
    required = attendance.required_working_dates(
        date(2026, 9, 1), date(2026, 9, 30),
        {date(2026, 9, 10), date(2026, 9, 21)},
    )
    assert all(day.weekday() != 6 for day in required)
    assert date(2026, 9, 10) not in required
    assert date(2026, 9, 21) not in required
    assert len(required) == 24


def test_eligibility_tiers_require_exactly_one_hundred_percent_verified_attendance():
    assert attendance.eligibility_for_counts(24, 24) == (100.0, 40_000)
    assert attendance.eligibility_for_counts(23, 24) == (95.8333, 15_000)
    assert attendance.eligibility_for_counts(24, 24)[1] == 40_000  # later recovery
    assert attendance.eligibility_for_counts(0, 0) == (None, 15_000)


def test_evaluation_period_does_not_penalize_today_before_nine():
    assert attendance.evaluation_period(at(8, 30), date(2026, 9, 1)) == (
        date(2026, 9, 1), date(2026, 8, 31)
    )


def test_first_effective_day_empty_calculation_never_displays_an_inverted_period(monkeypatch):
    captured = {}

    class Cursor:
        description = []

        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=None):
            if "INSERT INTO operations_earnings_eligibility_state" in sql:
                captured["period_start"] = params[1]
                captured["period_end"] = params[2]
        def fetchone(self): return None

    monkeypatch.setattr(attendance, "_holidays", lambda *_args: {})
    monkeypatch.setattr(attendance, "_attended_dates", lambda *_args: set())
    result = attendance._reconcile_eligibility(
        _Connection(Cursor()),
        attendance.account_identity({"username": "employee-a", "role": "handler", "reference": "Employee A"}),
        at(8, 30),
        {"effective_date": date(2026, 9, 1)},
    )
    assert result["required_working_days"] == 0
    assert result["period_start"] == result["period_end"] == "2026-09-01"
    assert captured["period_start"] == captured["period_end"] == date(2026, 9, 1)


def test_business_timezone_controls_the_attendance_date_boundary():
    utc_value = datetime(2026, 8, 31, 18, 45, tzinfo=timezone.utc)
    decision = attendance.popup_decision(
        utc_value, effective_date=date(2026, 9, 1), holiday_dates=[], already_marked=False
    )
    assert decision["attendance_date"] == "2026-09-01"
    assert decision["reason"] == "BEFORE_START_TIME"
    assert attendance.evaluation_period(at(9, 0), date(2026, 9, 1)) == (
        date(2026, 9, 1), date(2026, 9, 1)
    )


def test_session_identity_uses_reference_for_handler_and_never_hardcodes_a_name(monkeypatch):
    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "admin-pass")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "test-secret")
    handler = auth._complete_profile({"username": "venu-login", "role": "handler", "reference": "Venu"})
    other = auth._complete_profile({"username": "pavan-login", "role": "handler", "reference": "Pavan"})
    assert handler["display_name"] == "Venu"
    assert other["display_name"] == "Pavan"
    token = auth.create_session_token(**{
        "username": handler["username"], "role": handler["role"],
        "reference": handler["reference"], "display_name": handler["display_name"],
        "account_id": handler["account_id"],
    })
    parsed = auth.parse_session_token(token)
    assert parsed["display_name"] == "Venu"
    assert parsed["account_id"] == "handler:venu-login"


def test_office_network_allows_approved_direct_address_and_rejects_others():
    assert verify_office_network("203.0.113.14", office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="").allowed
    assert not verify_office_network("198.51.100.20", office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="").allowed
    assert not verify_office_network("192.0.2.44", office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="").allowed


def test_client_cannot_spoof_office_ip_in_forwarding_header():
    # Direct peers are never allowed to make X-Forwarded-For authoritative.
    direct_spoof = verify_office_network(
        "198.51.100.20", "203.0.113.14",
        office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="127.0.0.1/32",
    )
    assert not direct_spoof.allowed
    # Nginx appends the real client at the right. A client-prepended office IP
    # therefore cannot beat the right-to-left trusted-proxy walk.
    proxied_spoof = verify_office_network(
        "127.0.0.1", "203.0.113.14, 198.51.100.20",
        office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="127.0.0.1/32",
    )
    assert not proxied_spoof.allowed
    genuine = verify_office_network(
        "127.0.0.1", "203.0.113.14",
        office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="127.0.0.1/32",
    )
    assert genuine.allowed
    assert genuine.audit_payload().keys() == {
        "verified", "source", "policy_id", "reason", "observed_ip"}


def test_missing_or_malformed_network_configuration_fails_closed():
    assert not verify_office_network("203.0.113.14", office_cidrs="", trusted_proxy_cidrs="").allowed
    assert not verify_office_network("203.0.113.14", office_cidrs="not-a-cidr", trusted_proxy_cidrs="").allowed


def test_migration_enforces_one_record_and_keeps_all_audit_layers():
    sql = Path("core/migrations/028_attendance_earnings_eligibility.sql").read_text("utf-8").lower()
    assert "unique (account_id, attendance_date)" in sql
    for table in (
        "operations_attendance_records", "operations_public_holidays",
        "operations_auth_activity", "operations_earnings_eligibility_state",
        "operations_earnings_eligibility_events", "operations_salary_change_recommendations",
    ):
        assert f"create table if not exists {table}" in sql
    assert "approved_pending_apply" in sql
    assert "office_network_verified boolean not null check (office_network_verified)" in sql


def test_double_click_path_is_an_atomic_database_upsert_that_fails_closed():
    source = Path("features/attendance_eligibility.py").read_text("utf-8")
    assert "ON CONFLICT(account_id, attendance_date) DO NOTHING" in source
    assert "AND status='VERIFIED' AND office_network_verified" in source
    assert "attendance_ratio" not in Path("api/routers/attendance.py").read_text("utf-8").split("async def attendance_mark", 1)[1].split("@router", 1)[0]


class _InsertCursor:
    description = [
        ("id",), ("account_id",), ("username",), ("display_name",),
        ("attendance_date",), ("marked_at",), ("status",), ("office_network_verified",),
    ]

    def __init__(self):
        self.params = None

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def execute(self, _sql, params=None): self.params = params
    def fetchone(self):
        return (
            self.params[0], self.params[1], self.params[2], self.params[3],
            self.params[4], self.params[5], "VERIFIED", True,
        )


class _Connection:
    def __init__(self, cursor): self._cursor = cursor
    def cursor(self): return self._cursor


def test_mark_stores_real_request_time_and_server_derived_identity(monkeypatch):
    cursor = _InsertCursor()

    @contextmanager
    def connection():
        yield _Connection(cursor)

    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    monkeypatch.setattr(attendance, "get_connection", connection)
    monkeypatch.setattr(attendance, "_policy", lambda _conn: {
        "effective_date": date(2026, 9, 1), "business_timezone": "Asia/Kolkata",
        "attendance_start_time": attendance.ATTENDANCE_START,
    })
    monkeypatch.setattr(attendance, "_holidays", lambda *_args: {})
    monkeypatch.setattr(attendance, "_attendance_row", lambda *_args: None)
    monkeypatch.setattr(attendance, "_reconcile_eligibility", lambda *_args: {"eligibility_amount": 40_000})
    actual = at(13, 7)
    result = attendance.mark_attendance(
        {"username": "employee-a", "role": "handler", "reference": "Employee A"},
        verify_office_network("203.0.113.14", office_cidrs="203.0.113.0/24", trusted_proxy_cidrs=""),
        now=actual,
    )
    assert result["status"] == "marked"
    assert result["attendance"]["marked_at"] == actual.isoformat()
    assert result["attendance"]["account_id"] == "handler:employee-a"


def test_generic_admin_status_never_reconciles_employee_eligibility(monkeypatch):
    class EmptyCursor:
        description = []
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, _sql, _params=None): return None
        def fetchall(self): return []

    @contextmanager
    def connection():
        yield _Connection(EmptyCursor())

    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    monkeypatch.setattr(attendance, "get_connection", connection)
    monkeypatch.setattr(attendance, "_policy", lambda _conn: {
        "effective_date": date(2026, 9, 1), "business_timezone": "Asia/Kolkata",
        "attendance_start_time": attendance.ATTENDANCE_START,
    })
    monkeypatch.setattr(attendance, "_holidays", lambda *_args: {})
    monkeypatch.setattr(attendance, "_attendance_row", lambda *_args: None)
    monkeypatch.setattr(
        attendance, "_reconcile_eligibility",
        lambda *_args: pytest.fail("generic admin reached earnings eligibility"),
    )

    result = attendance.status(
        {"username": "operations_admin", "role": "admin", "display_name": "Operations Admin"},
        now=at(10),
    )
    assert result["employee_attendance"] is False
    assert result["eligibility"] is None
    assert result["popup"]["eligible"] is False
    assert result["popup"]["reason"] == "NON_EMPLOYEE_ACCOUNT"


def test_generic_admin_cannot_mark_attendance(monkeypatch):
    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    network = verify_office_network(
        "203.0.113.14", office_cidrs="203.0.113.0/24", trusted_proxy_cidrs="",
    )
    with pytest.raises(PermissionError, match="handler accounts"):
        attendance.mark_attendance(
            {"username": "operations_admin", "role": "admin"}, network, now=at(10),
        )


def test_recommendation_filter_uses_bound_handler_prefix(monkeypatch):
    class RecommendationCursor:
        description = []
        def __init__(self): self.calls = []
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, sql, params=None): self.calls.append((sql, params))
        def fetchall(self): return []

    cursor = RecommendationCursor()

    @contextmanager
    def connection():
        yield _Connection(cursor)

    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    monkeypatch.setattr(attendance, "get_connection", connection)
    assert attendance.list_recommendations("PENDING") == []
    assert cursor.calls[-1][1] == ("handler:%", "PENDING")
    assert attendance.list_recommendations("ALL") == []
    assert cursor.calls[-1][1] == ("handler:%",)


def test_admin_overview_uses_stable_account_ids_and_excludes_admin(monkeypatch):
    class OverviewCursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, _sql, _params=None): return None
        def fetchall(self):
            return [
                (
                    "handler:referrer-thrilok", date(2026, 9, 1), at(11, 51),
                    "VERIFIED", True,
                ),
            ]

    @contextmanager
    def connection():
        yield _Connection(OverviewCursor())

    monkeypatch.setattr(attendance, "use_postgres", lambda: True)
    monkeypatch.setattr(attendance, "get_connection", connection)
    monkeypatch.setattr(attendance, "_policy", lambda _conn: {
        "effective_date": date(2026, 9, 1), "business_timezone": "Asia/Kolkata",
        "attendance_start_time": attendance.ATTENDANCE_START,
    })
    monkeypatch.setattr(attendance, "_holidays", lambda *_args: {})
    result = attendance.admin_overview([
        {
            "username": "thrilok", "role": "handler", "reference": "Thrilok",
            "account_id": "handler:referrer-thrilok",
        },
        {
            "username": "operations_admin", "role": "admin",
            "display_name": "Operations Admin", "account_id": "admin:operations_admin",
        },
    ], now=at(13))
    assert [row["display_name"] for row in result["users"]] == ["Thrilok"]
    assert result["users"][0]["account_id"] == "handler:referrer-thrilok"
    assert result["users"][0]["today_status"] == "VERIFIED"
    assert result["users"][0]["attendance_ratio"] == 100.0
    assert result["users"][0]["eligibility_amount"] == 40_000


@pytest.fixture
def api_client(monkeypatch):
    import main
    from api.routers import attendance as attendance_api

    monkeypatch.setattr(auth, "_refresh_dashboard_env_from_file", lambda: None)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("DASHBOARD_AUTH_SECRET", "test-secret")
    monkeypatch.setenv("DASHBOARD_COOKIE_SECURE", "false")
    client = TestClient(main.app)
    return client, attendance_api


def signed_in(client, username="employee-a", role="handler", reference="Employee A"):
    client.cookies.clear()
    client.cookies.set(
        auth.SESSION_COOKIE,
        auth.create_session_token(username, role=role, reference=reference),
    )
    return client


def test_mark_api_ignores_client_identity_time_ratio_and_eligibility(api_client, monkeypatch):
    client, attendance_api = api_client
    captured = {}
    monkeypatch.setattr(attendance_api, "verify_office_network", lambda *_args: type("N", (), {"allowed": True})())

    def fake_mark(profile, network):
        captured.update(profile)
        return {"status": "marked", "attendance": {"id": "one"}}

    monkeypatch.setattr(attendance_api.attendance, "mark_attendance", fake_mark)
    response = signed_in(client).post("/attendance/mark", json={
        "user_id": "handler:employee-b", "marked_at": "2000-01-01T00:00:00Z",
        "attendance_ratio": 100, "eligibility_amount": 40_000, "client_ip": "203.0.113.14",
    })
    assert response.status_code == 200
    assert captured["username"] == "employee-a"
    assert captured["account_id"] == "handler:employee-a"


def test_handler_cannot_manage_holidays_or_approve_salary(api_client, monkeypatch):
    client, attendance_api = api_client
    monkeypatch.setattr(attendance_api.attendance, "upsert_holiday", lambda *_args: pytest.fail("writer reached"))
    monkeypatch.setattr(attendance_api.attendance, "review_recommendation", lambda *_args: pytest.fail("writer reached"))
    signed_in(client, role="handler")
    holiday = client.post("/attendance/holidays", json={"holiday_date": "2026-09-10", "name": "Test"})
    salary = client.post("/attendance/salary-recommendations/rec/review", json={"decision": "APPROVE"})
    direct_salary = client.post("/handler-salaries", json={
        "reference": "Employee A", "monthly_salary": 40_000, "active_from": "2026-09",
    })
    assert holiday.status_code == 403
    assert salary.status_code == 403
    assert direct_salary.status_code == 403


def test_admin_can_reach_salary_review_but_no_salary_changes_without_explicit_approval(api_client, monkeypatch):
    client, attendance_api = api_client
    called = []
    monkeypatch.setattr(
        attendance_api.attendance, "review_recommendation",
        lambda rec_id, decision, profile, note="": called.append((rec_id, decision, profile["role"])) or {"id": rec_id, "review_status": "APPLIED"},
    )
    signed_in(client, username="operations_admin", role="admin", reference=None)
    assert called == []
    response = client.post("/attendance/salary-recommendations/rec/review", json={"decision": "APPROVE"})
    assert response.status_code == 200
    assert called == [("rec", "APPROVE", "admin")]


def test_attendance_admin_endpoints_reject_handlers_and_validate_selected_account(api_client, monkeypatch):
    client, attendance_api = api_client
    profiles = [{
        "username": "thrilok", "role": "handler", "reference": "Thrilok",
        "display_name": "Thrilok", "account_id": "handler:referrer-thrilok",
    }]
    monkeypatch.setattr(
        attendance_api, "_attendance_handler_profiles",
        lambda: profiles,
    )
    monkeypatch.setattr(
        attendance_api.attendance, "admin_overview",
        lambda rows: {"status": "ok", "users": rows},
    )
    monkeypatch.setattr(
        attendance_api.attendance, "admin_history",
        lambda profile: {"status": "ok", "profile": profile, "records": []},
    )

    signed_in(client, role="handler")
    assert client.get("/attendance/admin/overview").status_code == 403

    signed_in(client, username="operations_admin", role="admin", reference=None)
    overview = client.get("/attendance/admin/overview")
    assert overview.status_code == 200
    assert overview.json()["users"][0]["account_id"] == "handler:referrer-thrilok"
    history = client.get(
        "/attendance/admin/history", params={"account_id": "handler:referrer-thrilok"},
    )
    assert history.status_code == 200
    assert history.json()["profile"]["username"] == "thrilok"
    assert client.get(
        "/attendance/admin/history", params={"account_id": "admin:operations_admin"},
    ).status_code == 404


def test_admin_overview_registry_includes_active_future_handlers_only(api_client, monkeypatch):
    _client, attendance_api = api_client
    monkeypatch.setattr(attendance_api.auth, "audit_operator_accounts", lambda: {"users": [
        {
            "username": "operations_admin", "role": "admin", "name": "Operations Admin",
            "account_id": "admin:operations_admin", "active": True, "orphaned": False,
        },
        {
            "username": "future-handler", "role": "handler", "name": "Future Handler",
            "account_id": "handler:future-handler", "active": True, "orphaned": False,
        },
        {
            "username": "disabled-handler", "role": "handler", "name": "Disabled Handler",
            "account_id": "handler:disabled-handler", "active": False, "orphaned": False,
        },
    ]})
    assert attendance_api._attendance_handler_profiles() == [{
        "username": "future-handler", "role": "handler", "reference": "Future Handler",
        "display_name": "Future Handler", "account_id": "handler:future-handler",
    }]


def test_login_and_logout_preserve_session_behavior_and_emit_audit_events(api_client, monkeypatch):
    client, _attendance_api = api_client
    from core import dashboard_auth_api

    events = []

    async def capture(profile, activity):
        events.append((profile["username"], profile["display_name"], activity))

    monkeypatch.setattr(dashboard_auth_api, "_record_auth_activity", capture)
    monkeypatch.setattr(auth, "get_credentials", lambda: ("operations_admin", "test-password"))
    login = client.post("/auth/login", json={"username": "operations_admin", "password": "test-password"})
    assert login.status_code == 200
    assert login.json()["display_name"] == "Operations Admin"
    assert login.cookies.get(auth.SESSION_COOKIE)
    logout = client.post("/auth/logout")
    assert logout.status_code == 200
    assert events == [
        ("operations_admin", "Operations Admin", "LOGIN"),
        ("operations_admin", "Operations Admin", "LOGOUT"),
    ]


# ── the office public IP changes when the router is reset ────────────────────
#
# September 2026: the office reached production from one address until the 12th
# and from another from the 14th, while OPERATIONS_OFFICE_NETWORK_CIDRS still
# named the first. Everyone sitting in the office was told to connect to office
# Wi-Fi. The allowlist was right to refuse an address nobody had approved; what
# was missing was any way to see which address was being judged. The addresses
# themselves are configuration, so they are not written down here.


def test_a_refused_device_is_told_which_address_was_judged():
    from core.office_network import verify_office_network

    refused = verify_office_network(
        "203.0.113.77", "", office_cidrs="198.51.100.10/32", trusted_proxy_cidrs="")

    assert refused.allowed is False
    assert refused.observed_ip == "203.0.113.77"
    assert refused.reason == "OUTSIDE_APPROVED_OFFICE_NETWORK"


def test_an_approved_device_records_the_same_address():
    from core.office_network import verify_office_network

    allowed = verify_office_network(
        "198.51.100.10", "", office_cidrs="198.51.100.10/32", trusted_proxy_cidrs="")

    assert allowed.allowed is True
    assert allowed.observed_ip == "198.51.100.10"


def test_a_new_office_address_needs_registering_not_guessing():
    """A changed public IP is refused until it is approved. The allowlist is
    the only thing that grants access; naming the address does not."""
    from core.office_network import verify_office_network

    old_policy = "198.51.100.10/32"
    after_router_reset = verify_office_network("198.51.100.42", "", office_cidrs=old_policy,
                                               trusted_proxy_cidrs="")
    once_registered = verify_office_network("198.51.100.42", "",
                                            office_cidrs=f"{old_policy},198.51.100.42/32",
                                            trusted_proxy_cidrs="")

    assert after_router_reset.allowed is False
    assert once_registered.allowed is True


def test_several_approved_networks_can_be_configured_at_once():
    """Two offices, or an office mid-migration between addresses."""
    from core.office_network import verify_office_network

    policy = "198.51.100.10/32,203.0.113.0/24"
    assert verify_office_network("198.51.100.10", "", office_cidrs=policy, trusted_proxy_cidrs="").allowed
    assert verify_office_network("203.0.113.55", "", office_cidrs=policy, trusted_proxy_cidrs="").allowed
    assert not verify_office_network("192.0.2.9", "", office_cidrs=policy, trusted_proxy_cidrs="").allowed


def test_a_mobile_network_is_still_refused_after_the_office_moves():
    from core.office_network import verify_office_network

    policy = "198.51.100.42/32"
    for outsider in ("192.0.2.110", "198.51.100.113", "203.0.113.45"):
        verdict = verify_office_network(outsider, "", office_cidrs=policy, trusted_proxy_cidrs="")
        assert verdict.allowed is False
        assert verdict.observed_ip == outsider
