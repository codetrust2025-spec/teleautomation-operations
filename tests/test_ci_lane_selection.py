"""Which CI lane a change takes, driven through the real script.

The fast lane skips the Python suite, the image build and the container health,
migration and persistence checks. That is safe only while the paths it accepts
genuinely cannot affect any of them, so the interesting half of this file is the
second class: every kind of change that must NOT be allowed to skip them.

These call `scripts/ci_lane.sh` itself rather than reimplementing the rule, so a
regex edited in the script without revisiting the policy fails here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_lane.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required to run the lane script"
)


def lane(*paths: str) -> str:
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        input="\n".join(paths),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


class TestTheFastLaneIsNarrow:
    @pytest.mark.parametrize(
        "path",
        [
            "dashboard/src/App.jsx",
            "dashboard/src/index.css",
            "dashboard/src/components/ConfirmDialog.jsx",
            "dashboard/src/utils/istTime.js",
            "dashboard/src/OperationsSidebarOrder.test.js",
        ],
    )
    def test_dashboard_source_takes_the_fast_lane(self, path: str) -> None:
        assert lane(path) == "frontend"

    def test_several_dashboard_files_together_still_qualify(self) -> None:
        assert lane("dashboard/src/App.jsx", "dashboard/src/index.css") == "frontend"

    def test_the_sidebar_fix_that_motivated_this_qualifies(self) -> None:
        # The one-character icon change measured at 9m01s end to end.
        assert lane(
            "dashboard/src/App.jsx",
            "dashboard/src/OperationsSidebarOrder.test.js",
        ) == "frontend"


class TestRiskDomainsKeepTheFullPipeline:
    @pytest.mark.parametrize(
        "path,why",
        [
            ("main.py", "backend entrypoint"),
            ("api/routers/attendance.py", "attendance logic"),
            ("api/routers/data_room.py", "handler credentials"),
            ("core/dashboard_auth_vps.py", "auth"),
            ("core/password_hashing.py", "auth"),
            ("features/attendance_eligibility.py", "attendance and earnings"),
            ("features/payment_verification_engine.py", "payments"),
            ("services/recruitment_mail_agent.py", "mail classification"),
            ("core/recruitment_realtime.py", "realtime delivery"),
            ("core/migrations/029_something.sql", "database migration"),
            ("requirements.txt", "dependencies"),
            ("Dockerfile", "image"),
            ("docker-compose.yml", "infrastructure"),
            (".github/workflows/ci.yml", "the pipeline itself"),
            ("tests/test_handler_login_persistence.py", "test suite"),
            ("scripts/ci_lane.sh", "the lane rule itself"),
        ],
    )
    def test_it_takes_the_full_lane(self, path: str, why: str) -> None:
        assert lane(path) == "full", why

    @pytest.mark.parametrize(
        "path,why",
        [
            ("dashboard/package.json", "changes what npm ci resolves in the image"),
            ("dashboard/package-lock.json", "changes what npm ci resolves in the image"),
            ("dashboard/vite.config.js", "changes how the bundle is produced"),
            ("dashboard/index.html", "outside src/"),
        ],
    )
    def test_dashboard_build_inputs_are_not_fast_lane(self, path: str, why: str) -> None:
        """`npm ci` inside the image must resolve an unchanged lockfile for the
        fast lane's reasoning to hold, so its inputs cannot be fast-laned."""
        assert lane(path) == "full", why

    @pytest.mark.parametrize(
        "path",
        [
            "dashboard/src/notifications/mailEventStream.js",
            "dashboard/src/notifications/notificationEvents.js",
            "dashboard/src/attendance/AttendancePanel.jsx",
            "dashboard/src/attendance/AttendanceContext.jsx",
        ],
    )
    def test_realtime_and_attendance_ui_still_takes_the_full_lane(self, path: str) -> None:
        """These are dashboard source and would otherwise qualify. Realtime mail
        delivery and attendance are named risk domains: a regression there is
        expensive and invisible in a screenshot, so they keep the full run."""
        assert lane(path) == "full"


class TestItFailsSafe:
    def test_no_paths_means_full(self) -> None:
        assert lane() == "full"

    def test_blank_input_means_full(self) -> None:
        assert lane("", "   ") == "full"

    def test_one_backend_file_among_many_frontend_files_forces_full(self) -> None:
        assert lane(
            "dashboard/src/App.jsx",
            "dashboard/src/index.css",
            "core/recruitment_mail_store.py",
        ) == "full"

    @pytest.mark.parametrize(
        "path",
        [
            "dashboard/src/thing.ts",
            "dashboard/src/thing.tsx",
            "dashboard/src/data.json",
            "dashboard/src/logo.svg",
        ],
    )
    def test_unknown_extensions_under_src_are_not_assumed_safe(self, path: str) -> None:
        """The rule is an allowlist. A file type the suite and the build have
        never seen is not evidence of a harmless change."""
        assert lane(path) == "full"

    @pytest.mark.parametrize(
        "path",
        [
            "notdashboard/src/App.jsx",
            "vendor/dashboard/src/App.jsx",
            "dashboard/srcx/App.jsx",
        ],
    )
    def test_matching_is_anchored_not_substring(self, path: str) -> None:
        assert lane(path) == "full"


class TestRiskDomainsAlwaysTakeTheFullLane:
    """Booking, payments, Gmail automation and sign-in never skip the checks.

    The owner's rule for the fast lane: never for these domains, whatever layer
    the change is in. Logic files are matched by folder and by name, so a new
    file in one of these domains needs no edit to the rule to be caught.
    """

    @pytest.mark.parametrize(
        "path",
        [
            # booking
            "dashboard/src/pages/SubmitSlotPage.jsx",
            "dashboard/src/dailyOps/InterviewRoster.jsx",
            "dashboard/src/dailyOps/dateRangePresets.js",
            "dashboard/src/utils/bookingSource.js",
            "dashboard/src/components/CandidatesPanel.jsx",
            # payments
            "dashboard/src/candidates/PayoutModal.jsx",
            "dashboard/src/candidates/paymentProofs.js",
            "dashboard/src/candidates/EarningsBreakdown.jsx",
            "dashboard/src/candidates/ReferrerPaymentAccounts.jsx",
            "dashboard/src/ownership.js",
            # Gmail automation
            "dashboard/src/components/RecruitmentMailPanelRedesign.jsx",
            "dashboard/src/components/MailMonitoringNotifications.jsx",
            "dashboard/src/components/ReconnectWorklist.jsx",
            "dashboard/src/components/OcrToggle.jsx",
            "dashboard/src/components/PureOllamaToggle.jsx",
            "dashboard/src/components/AiNodeProgress.jsx",
            "dashboard/src/components/ExpiryCountdown.jsx",
            "dashboard/src/utils/mailboxStatus.js",
            "dashboard/src/utils/mailAlertSound.js",
            # sign-in and credentials
            "dashboard/src/components/AuthGate.jsx",
            "dashboard/src/components/LoginScreen.jsx",
            "dashboard/src/components/ChangePasswordModal.jsx",
            "dashboard/src/context/AuthContext.jsx",
            "dashboard/src/components/DataRoomVaultSection.jsx",
            "dashboard/src/utils/dataRoomCredentialsApi.js",
            "dashboard/src/utils/offerLetterUrls.js",
            "dashboard/src/config.js",
            # a file that does not exist yet is caught by its name
            "dashboard/src/components/NewPaymentWidget.jsx",
            "dashboard/src/utils/gmailQuota.js",
        ],
    )
    def test_their_logic_takes_the_full_lane(self, path: str) -> None:
        assert lane(path) == "full"

    def test_and_so_does_its_test(self) -> None:
        assert lane("dashboard/src/components/MailMonitoringNotifications.test.jsx") == "full"

    def test_one_risk_file_takes_the_whole_change_with_it(self) -> None:
        assert lane("dashboard/src/index.css", "dashboard/src/candidates/PayoutModal.jsx") == "full"

    @pytest.mark.parametrize(
        "path",
        [
            "dashboard/src/dailyOps.css",
            "dashboard/src/recruitmentMail.css",
            "dashboard/src/candidates/CompanyExpenditure.css",
        ],
    )
    def test_their_stylesheets_stay_on_the_fast_lane(self, path: str) -> None:
        # Presentation cannot change what these screens do; the dashboard job
        # still runs the whole suite and the production build.
        assert lane(path) == "frontend"

    def test_the_rule_itself_is_never_fast_laned(self) -> None:
        assert lane("scripts/ci_lane.sh") == "full"
