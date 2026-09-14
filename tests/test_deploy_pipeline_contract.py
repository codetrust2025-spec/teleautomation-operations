"""The release pipeline's safety properties, pinned.

`.github/workflows/deploy.yml` turns a merge into a production release, and
`.github/workflows/ci.yml` is the one check a merge needs. A small edit to
either can quietly remove a guarantee -- a secret in a build argument, an
unpinned host key, a skipped job that still reads as green -- without breaking
anything visible. These tests fail when one of those guarantees goes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
DOCKERFILE = ROOT / "Dockerfile"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key `on` as the boolean True.
    return workflow.get("on", workflow.get(True)) or {}


def steps(job: dict) -> list[dict]:
    return job.get("steps") or []


def step_named(job: dict, fragment: str) -> dict:
    matches = [s for s in steps(job) if fragment in (s.get("name") or "")]
    assert len(matches) == 1, f"expected one step named like {fragment!r}"
    return matches[0]


@pytest.fixture(scope="module")
def deploy() -> dict:
    return load(DEPLOY)


@pytest.fixture(scope="module")
def ci() -> dict:
    return load(CI)


class TestWhatCanStartARelease:
    def test_only_main_and_a_manual_run(self, deploy):
        on = triggers(deploy)
        assert set(on) == {"push", "workflow_dispatch"}
        assert on["push"] == {"branches": ["main"]}

    def test_a_pull_request_can_never_trigger_it(self, deploy):
        text = DEPLOY.read_text(encoding="utf-8")
        assert "pull_request" not in text

    def test_a_manual_run_defaults_to_verify_not_deploy(self, deploy):
        action = triggers(deploy)["workflow_dispatch"]["inputs"]["action"]
        assert action["default"] == "verify"
        assert action["options"] == ["verify", "deploy", "build"]

    def test_a_manual_build_never_reaches_the_host(self, deploy):
        condition = " ".join(deploy["jobs"]["deploy"]["if"].split())
        assert "(github.event_name == 'workflow_dispatch' && inputs.action != 'build')" in condition
        assert "(github.event_name == 'push' && needs.preflight.outputs.auto_deploy == 'true')" in condition

    def test_old_images_are_pruned_but_a_rollback_window_is_kept(self, deploy):
        prune = deploy["jobs"]["prune"]
        assert prune["if"] == "github.event_name == 'push'"
        keep = steps(prune)[0]["with"]
        assert keep["package-name"] == "teleautomation-operations"
        assert int(keep["min-versions-to-keep"]) >= 20

    def test_only_a_commit_on_main_is_built(self, deploy):
        run = step_named(deploy["jobs"]["image"], "Only a commit on main")["run"]
        assert "merge-base --is-ancestor" in run and "origin/main" in run

    def test_releases_are_serialised_and_never_cancelled(self, deploy):
        assert deploy["concurrency"] == {"group": "production-release", "cancel-in-progress": False}

    def test_the_stage_switch_is_committed_not_a_setting(self, deploy):
        assert deploy["env"]["AUTO_DEPLOY"] in {"true", "false"}
        assert "auto_deploy == 'true'" in deploy["jobs"]["deploy"]["if"]


class TestTheImage:
    def test_it_is_tagged_and_stamped_with_the_commit(self, deploy):
        build = step_named(deploy["jobs"]["image"], "Build and push")["with"]
        assert build["tags"].endswith(":${{ steps.target.outputs.sha }}")
        assert "RELEASE_SHA=${{ steps.target.outputs.sha }}" in build["build-args"]
        assert build["push"] is True

    def test_no_secret_is_baked_into_it(self, deploy):
        build = step_named(deploy["jobs"]["image"], "Build and push")["with"]
        assert "secrets." not in build["build-args"]

    def test_it_is_one_manifest_so_the_pulled_digest_is_the_inspected_image(self, deploy):
        build = step_named(deploy["jobs"]["image"], "Build and push")["with"]
        assert build["provenance"] is False and build["sbom"] is False

    def test_the_layer_cache_is_shared_with_ci(self, deploy, ci):
        build = step_named(deploy["jobs"]["image"], "Build and push")["with"]
        ci_build = step_named(ci["jobs"]["container"], "Build the image")["with"]
        assert build["cache-from"] == ci_build["cache-from"] == "type=gha,scope=operations-image"

    def test_a_rollback_reuses_the_released_image(self, deploy):
        assert "imagetools inspect" in step_named(deploy["jobs"]["image"], "Reuse the image")["run"]

    def test_the_image_job_may_write_packages_and_nothing_else(self, deploy):
        assert deploy["permissions"] == {"contents": "read"}
        assert deploy["jobs"]["image"]["permissions"] == {"contents": "read", "packages": "write"}


class TestTheDeployJob:
    def test_its_credentials_come_from_the_production_environment(self, deploy):
        job = deploy["jobs"]["deploy"]
        assert job["environment"]["name"] == "production"
        assert "secrets.PROD_DEPLOY_SSH_KEY" in step_named(job, "Configure SSH")["env"]["KEY"]

    def test_its_token_can_read_packages_and_write_nothing(self, deploy):
        assert deploy["jobs"]["deploy"]["permissions"] == {"contents": "read", "packages": "read"}

    def test_the_host_key_is_pinned(self, deploy):
        text = DEPLOY.read_text(encoding="utf-8")
        assert "StrictHostKeyChecking=yes" in text
        assert not re.search(r"StrictHostKeyChecking\s*=?\s*no", text)
        assert "UserKnownHostsFile=" in text

    def test_the_registry_token_is_sent_on_stdin_and_never_in_the_remote_command(self, deploy):
        run = step_named(deploy["jobs"]["deploy"], "Release on the host")["run"]
        assert re.search(r"printf '%s\\n%s\\n' \"\$GITHUB_ACTOR\" \"\$REGISTRY_TOKEN\"\s*\\\s*\n\s*\| ssh", run)
        remote = run.rsplit('"deploy@$HOST"', 1)[1]
        assert remote.strip() == '"$ACTION $SHA $DIGEST"'
        assert "REGISTRY_TOKEN" not in remote

    def test_the_host_is_only_ever_asked_to_verify_or_deploy(self, deploy):
        run = step_named(deploy["jobs"]["deploy"], "Release on the host")["run"]
        assert "case \"$ACTION\" in verify|deploy)" in run

    def test_the_key_is_removed_even_on_failure(self, deploy):
        cleanup = step_named(deploy["jobs"]["deploy"], "Remove the key")
        assert cleanup["if"] == "always()"


class TestTheMergeGate:
    FULL = {"python", "container", "dual-service"}

    def test_the_single_gate_waits_for_every_job(self, ci):
        gate = ci["jobs"]["ci"]
        assert set(gate["needs"]) == {"classify", "dashboard"} | self.FULL
        assert gate["if"] == "always()"

    def test_dashboard_checks_run_on_both_lanes(self, ci):
        assert "if" not in ci["jobs"]["dashboard"]

    @pytest.mark.parametrize("job", sorted(FULL))
    def test_backend_jobs_run_on_the_full_lane(self, ci, job):
        assert ci["jobs"][job]["if"] == "needs.classify.outputs.lane == 'full'"

    def test_a_skipped_backend_job_fails_the_full_lane(self, ci):
        run = steps(ci["jobs"]["ci"])[0]["run"]
        full = run[run.index("full)"):run.index(";;", run.index("full)"))]
        assert '[ "${job#*=}" = success ]' in full

    def test_the_container_check_proves_version_and_label(self, ci):
        run = step_named(ci["jobs"]["container"], "/version reports")["run"]
        assert "/version" in run and "GITHUB_SHA" in run
        assert "org.opencontainers.image.revision" in run

    def test_the_dual_service_key_becomes_mandatory_at_cutover(self, ci):
        assert ci["env"]["REQUIRE_DUAL_SERVICE"] in {"true", "false"}
        run = step_named(ci["jobs"]["dual-service"], "Check for the read-only Marketing key")["run"]
        assert '"$REQUIRE_DUAL_SERVICE" = "true"' in run and "exit 1" in run


class TestTheDockerfile:
    TEXT = DOCKERFILE.read_text(encoding="utf-8")

    def test_the_image_carries_its_commit_as_a_label(self):
        assert "LABEL org.opencontainers.image.revision=$RELEASE_SHA" in self.TEXT
        assert self.TEXT.index("ARG RELEASE_SHA") < self.TEXT.index("LABEL org.opencontainers.image.revision")

    def test_public_urls_do_not_invalidate_the_npm_install_layer(self):
        assert self.TEXT.index("RUN npm ci") < self.TEXT.index("ARG VITE_OPERATIONS_PUBLIC_URL")
        assert self.TEXT.index("ARG VITE_MARKETING_PUBLIC_URL") < self.TEXT.index("RUN npm run build")
