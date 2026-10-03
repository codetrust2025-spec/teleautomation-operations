"""The arm64 (OCI) validation can never become, or reach, a production release.

`.github/workflows/oci-arm64-validate.yml` proves the Operations image builds and
runs on linux/arm64 ahead of any move to an Oracle Cloud Ampere host. These tests
pin the isolation it promises, so a later edit to either workflow cannot quietly:

  * point production at the arm64 image (different repository name; the host's
    teleautomation-deploy pulls only from its one fixed repository),
  * give the validation workflow the production environment, its secrets, SSH or
    the deploy host,
  * turn the arm64 image into a multi-arch index (the deploy script validates
    labels on the exact digest it pulls, so it must stay one plain manifest),
  * change the existing amd64 production build in deploy.yml.
"""
import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows")
PROD_REPO = "ghcr.io/codetrust2025-spec/teleautomation-operations"


def load(name):
    text = open(os.path.join(WF, name), encoding="utf-8").read()
    data = yaml.safe_load(text)
    # PyYAML reads the bare key `on` as the boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return text, data


ARM_TEXT, ARM = load("oci-arm64-validate.yml")
DEPLOY_TEXT, DEPLOY = load("deploy.yml")


def steps(job):
    return ARM["jobs"][job]["steps"]


def build_steps(workflow):
    return [s for job in workflow["jobs"].values() for s in job.get("steps", [])
            if str(s.get("uses", "")).startswith("docker/build-push-action")]


# --- the validation workflow is isolated ------------------------------------------------

def test_it_uses_a_different_image_repository_from_production():
    repo = ARM["env"]["TEST_IMAGE_REPO"]
    assert repo != PROD_REPO
    assert not repo.rstrip("/").endswith("/teleautomation-operations")
    assert repo.startswith("ghcr.io/codetrust2025-spec/teleautomation-operations-oci-arm64-test")


def test_every_tag_it_builds_is_a_test_tag_in_the_test_repository():
    for step in build_steps(ARM):
        tags = str(step["with"]["tags"])
        assert PROD_REPO + ":" not in tags
        assert "steps.meta.outputs.tag" in tags or "needs.validate.outputs.tag" in tags
    assert ARM["env"]["TEST_TAG_PREFIX"] == "oci-arm64-test-"
    assert '${TEST_IMAGE_REPO}:${TEST_TAG_PREFIX}${GITHUB_SHA}' in ARM_TEXT


def test_it_builds_linux_arm64_only_as_a_single_plain_manifest():
    for step in build_steps(ARM):
        w = step["with"]
        assert w["platforms"] == "linux/arm64", "one platform: no amd64+arm64 index"
        assert w["provenance"] is False and w["sbom"] is False, "attestations would make it an index"


def test_it_runs_on_native_arm64_runners():
    assert all(job["runs-on"] == "ubuntu-24.04-arm" for job in ARM["jobs"].values())


def test_it_never_runs_for_main():
    triggers = ARM["on"]
    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["migration/oci-**"]


def test_it_has_no_route_to_production():
    lowered = ARM_TEXT.lower()
    for forbidden in ("environment:", "prod_deploy", "prod_known_hosts", "teleautomation-deploy ", "kvm1_ssh",
                      "ssh -", "ssh -i", "operations.teleautomation.online", "deploy@"):
        assert forbidden not in lowered, forbidden
    for job in ARM["jobs"].values():
        assert "environment" not in job


def test_its_secrets_are_only_the_job_token():
    assert set(re.findall(r"secrets\.([A-Z_]+)", ARM_TEXT)) <= {"GITHUB_TOKEN"}


def test_pushing_is_opt_in_and_only_from_a_manual_run():
    push = ARM["jobs"]["push"]
    assert push["if"] == "github.event_name == 'workflow_dispatch' && inputs.push"
    assert ARM["on"]["workflow_dispatch"]["inputs"]["push"]["default"] is False
    validate = next(s for s in build_steps(ARM) if s["with"].get("load"))
    assert validate["with"]["push"] is False


def test_only_the_push_job_may_write_packages():
    assert ARM["permissions"] == {"contents": "read"}
    assert ARM["jobs"]["push"]["permissions"]["packages"] == "write"
    assert "permissions" not in ARM["jobs"]["validate"]


def test_its_build_cache_never_shares_or_evicts_the_amd64_one():
    for step in build_steps(ARM):
        assert "scope=operations-image-arm64-test" in step["with"]["cache-from"]
        assert "scope=operations-image," not in step["with"]["cache-from"] + ","
        if "cache-to" in step["with"]:
            assert "scope=operations-image-arm64-test" in step["with"]["cache-to"]


def test_it_validates_everything_the_audit_asked_for():
    names = " | ".join(s.get("name", "") for s in steps("validate")).lower()
    for must in ("arm64 with the right commit", "native python libraries", "tesseract",
                 "/health", "/version", "assets", "architecture-related errors", "migrations"):
        assert must in names, must
    assert "uname -m" in ARM_TEXT and "aarch64" in ARM_TEXT


def test_the_container_only_talks_to_a_throwaway_database_on_the_runner():
    start = next(s for s in steps("validate") if s.get("name", "").startswith("Start it"))
    assert "docker-compose.yml:compose.arm64-test.yml" in start["run"]
    assert "cp .env.example .env" in start["run"]
    compose = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()
    assert "postgresql://operations:operations@db:5432/operations" in compose


# --- the production build is unchanged ------------------------------------------------------

def test_production_still_builds_its_own_repository():
    assert DEPLOY["env"]["IMAGE_REPO"] == PROD_REPO


def test_production_build_is_still_the_plain_amd64_one():
    (build,) = build_steps(DEPLOY)
    w = build["with"]
    assert "platforms" not in w, "deploy.yml must keep its default (amd64) build until the OCI cutover"
    assert w["provenance"] is False and w["sbom"] is False
    assert w["tags"] == "${{ env.IMAGE_REPO }}:${{ steps.target.outputs.sha }}"
    assert "scope=operations-image" in w["cache-from"] and "arm64" not in w["cache-from"]
    assert DEPLOY["jobs"]["image"]["runs-on"] == "ubuntu-latest"


def test_production_never_mentions_the_arm64_test_image():
    assert "oci-arm64" not in DEPLOY_TEXT and "arm64" not in DEPLOY_TEXT


def test_production_still_releases_only_main():
    assert DEPLOY["on"]["push"]["branches"] == ["main"]


@pytest.mark.parametrize("name", ["ci.yml", "deploy.yml"])
def test_existing_workflows_do_not_reference_the_test_repository(name):
    text = open(os.path.join(WF, name), encoding="utf-8").read()
    assert "oci-arm64-test" not in text
