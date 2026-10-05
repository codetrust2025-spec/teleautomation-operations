"""The release pipeline's safety properties, pinned.

`.github/workflows/deploy.yml` turns a merge into a production release, and
`.github/workflows/ci.yml` is the one check a merge needs. A small edit to
either can quietly remove a guarantee -- a secret in a build argument, an
unpinned host key, a skipped job that still reads as green -- without breaking
anything visible. These tests fail when one of those guarantees goes.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
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
        # Ten, as docs/deployment/architecture-review.md recommends: the host
        # rolls back to an image it already holds and never pulls one, so the
        # registry only has to cover recent releases.
        assert int(keep["min-versions-to-keep"]) >= 10

    def test_only_a_commit_on_main_is_built(self, deploy):
        run = step_named(deploy["jobs"]["image"], "Only a commit on main")["run"]
        assert "merge-base --is-ancestor" in run and "origin/main" in run

    def test_releases_are_serialised_and_never_cancelled(self, deploy):
        # On the job that touches the host, not the workflow: see the class
        # below for why the whole run must not hold this lock.
        assert deploy["jobs"]["deploy"]["concurrency"] == {"group": "production-release", "cancel-in-progress": False}

    def test_the_stage_switch_is_committed_not_a_setting(self, deploy):
        assert deploy["env"]["AUTO_DEPLOY"] in {"true", "false"}
        assert "auto_deploy == 'true'" in deploy["jobs"]["deploy"]["if"]


class TestOnlyTheHostReleaseIsSerialised:
    """A push must never make a manual deploy wait.

    The workflow used to sit in one `production-release` group as a whole. A
    push -- which only builds while AUTO_DEPLOY is off -- held the lock through
    its build and its registry prune, so a manual deploy waited behind
    housekeeping (on 5 Oct, behind a prune queued for a runner). Only the deploy
    job takes the release lock now.
    """

    def test_the_workflow_itself_takes_no_lock(self, deploy):
        assert "concurrency" not in deploy

    def test_no_job_but_deploy_joins_the_release_group(self, deploy):
        for name, job in deploy["jobs"].items():
            group = (job.get("concurrency") or {}).get("group", "")
            if name == "deploy":
                assert group == "production-release"
            else:
                assert "production-release" not in group, f"{name} would hold up releases"

    def test_housekeeping_never_waits_on_anything(self, deploy):
        for name in ("preflight", "prune"):
            assert "concurrency" not in deploy["jobs"][name]

    def test_a_commit_is_built_once_and_other_commits_never_wait(self, deploy):
        # Keyed by the commit, so the push for a merge and a deploy dispatched
        # right after it share one build; any other commit builds in parallel.
        image = deploy["jobs"]["image"]["concurrency"]
        assert image == {"group": "operations-image-${{ inputs.sha || github.sha }}", "cancel-in-progress": False}


class TestOneDeployPerCommit:
    """Two deploys of one commit restart the service once.

    The skip runs inside the release lock, so the second request sees what the
    first did. Exercised for real: the step's own script runs under bash with
    stand-ins for curl, gh and python3.
    """

    def test_a_restart_of_the_live_commit_must_be_asked_for(self, deploy):
        force = triggers(deploy)["workflow_dispatch"]["inputs"]["force"]
        assert force["type"] == "boolean" and force["default"] is False

    def test_the_step_reads_the_live_commit_inside_the_lock(self, deploy):
        job = deploy["jobs"]["deploy"]
        names = [s.get("name") or "" for s in steps(job)]
        check = names.index("Skip a stale push or a commit that is already live")
        assert check < names.index("Configure SSH") < names.index("Release on the host")
        assert step_named(job, "Release on the host")["if"] == "steps.current.outputs.release == 'true'"
        assert step_named(job, "Configure SSH")["if"] == "steps.current.outputs.release == 'true'"

    SHA = "a" * 40
    OTHER = "b" * 40

    @pytest.fixture()
    def run_check(self, deploy, tmp_path):
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash is not available")
        script = step_named(deploy["jobs"]["deploy"], "Skip a stale push")["run"]
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        python = sys.executable.replace("\\", "/")

        def stub(name: str, body: str) -> None:
            path = bin_dir / name
            path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8", newline="\n")
            path.chmod(0o755)

        def run(*, event: str, action: str, force: str = "false", live: str | None, head: str | None = None) -> str:
            # curl answers like /version, or fails as an unreachable site does.
            stub("curl", f'echo \'{{"service":"x","sha":"{live}"}}\'' if live else "exit 7")
            stub("gh", f"echo {head or self.SHA}")
            stub("python3", f'exec "{python}" "$@"')
            out = tmp_path / "out"
            out.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "PATH": f"{bin_dir.as_posix()}{os.pathsep}{os.environ.get('PATH', '')}",
                "SHA": self.SHA, "EVENT": event, "ACTION": action, "FORCE": force,
                "PUBLIC_URL": "https://ops.example.invalid/", "GH_TOKEN": "x",
                "GITHUB_REPOSITORY": "owner/repo", "GITHUB_OUTPUT": out.as_posix(),
            }
            subprocess.run([bash, "-c", script], env=env, check=True, capture_output=True, timeout=60)
            return out.read_text(encoding="utf-8").strip()

        return run

    def test_a_second_deploy_of_the_live_commit_is_skipped(self, run_check):
        assert run_check(event="workflow_dispatch", action="deploy", live=self.SHA) == "release=false"

    def test_a_push_of_the_live_commit_is_skipped_too(self, run_check):
        assert run_check(event="push", action="deploy", live=self.SHA) == "release=false"

    def test_a_new_commit_is_deployed(self, run_check):
        assert run_check(event="workflow_dispatch", action="deploy", live=self.OTHER) == "release=true"

    def test_force_restarts_the_live_commit(self, run_check):
        assert run_check(event="workflow_dispatch", action="deploy", force="true", live=self.SHA) == "release=true"

    def test_verify_is_read_only_and_always_runs(self, run_check):
        assert run_check(event="workflow_dispatch", action="verify", live=self.SHA) == "release=true"

    def test_an_unreachable_site_does_not_block_a_deploy(self, run_check):
        # A site that is down is a reason to deploy, not to skip.
        assert run_check(event="workflow_dispatch", action="deploy", live=None) == "release=true"

    def test_a_push_main_has_moved_past_is_still_skipped(self, run_check):
        assert run_check(event="push", action="deploy", live=self.OTHER, head=self.OTHER) == "release=false"


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
