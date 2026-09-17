# Deployment architecture: what is load-bearing, what is waiting

Reviewed 2026-09-17 against production (`f97e1d8`) and both repositories.

## How a release actually reaches production today

Marketing's `scripts/fix_and_deploy.sh` opens the Operations PR, merges it on
green, writes the commit into the release anchor in Marketing's
`docker-compose.production.yml`, merges that, then over SSH: checks both
repositories out on the host, refuses if the anchor and the checkout disagree,
**builds the image on the host**, recreates the container and verifies
`/version`, health, every container and the public site.

The host holds no registry credential, pulls no image, and runs the image it
just built. That is the whole path.

## Classification

| Item | Verdict | Why |
| --- | --- | --- |
| **A. GHCR cutover** | READY BUT BLOCKED | Every piece exists: `deploy.yml` builds and pushes per commit, `deploy/production/teleautomation-deploy` pulls by digest with a job-scoped token on stdin, verifies the revision label and `RELEASE_SHA` before restarting, and rolls back on failure. It cannot run because the `deploy` job needs the `production` environment plus `PROD_DEPLOY_SSH_KEY`, `PROD_KNOWN_HOSTS` and `PROD_DEPLOY_HOST`, and the host script is not installed on the host. |
| **B. Release-anchor retirement** | FUTURE IMPROVEMENT, strictly after A | The anchor is the only thing tying a running container to a reviewed commit today; the host refuses to build when it disagrees with the checkout. Retiring it before the registry path is proven removes that check and replaces it with nothing. |
| **C. `deploy.yml`** | REQUIRED, working as intended | It builds and pushes an image for every main commit (verified: "Build and push: success"), and its `deploy` job is correctly inert while `AUTO_DEPLOY` is `"false"`. |
| **D. Automated production deploy** | READY BUT BLOCKED | Same blockers as A, plus the deliberate `AUTO_DEPLOY` switch. |
| **E. Local host-build path** | REQUIRED NOW | It is the only live deploy path. It is also slower than a pull and needs build tooling on the production host — the reason A exists. |
| **F. Rollback** | REQUIRED NOW, and narrower than it looks | The host script restores the previous release from an image **already on the host** (`--pull never`); if that image is gone it refuses and tells you to redeploy the commit. Deleting registry versions cannot break it. The live path today has no scripted rollback at all: recovery is redeploying the previous commit through `fix_and_deploy.sh`. |
| **G. Image retention** | FUTURE IMPROVEMENT | See below. |
| **H. Manual/laptop dependency** | REQUIRED NOW, and the real fragility | Every deploy starts from a workstation running `fix_and_deploy.sh` with an SSH key. A is what removes it. |
| **I. Marketing/Operations coupling** | REQUIRED NOW | Marketing owns the compose file and the anchor, so an Operations change cannot reach production without a Marketing merge. Two PRs per change is the cost of one reviewed source of truth. |

Nothing here is OBSOLETE.

## G. Image retention — recommendation

`prune` keeps `min-versions-to-keep: 30`. At the current cadence (28 versions
accumulated in three days of heavy work; a normal week is far slower) thirty
versions is between three days and several weeks of history, and each image is
about 164 MB with most layers shared.

Rollback needs one previous version, occasionally two or three. Thirty is
therefore about ten times what recovery requires, and the surplus is what keeps
old builds — including any built before a data cleanup — reachable for longer.

**Recommended: 10.** It keeps a comfortable rollback depth for any realistic
recovery, ages retired builds out roughly three times faster, and stays well
above the one or two versions the documented rollback path can actually use.

Not implemented here: changing it edits `deploy.yml`, which this round of work
was explicitly told not to touch. It is a one-line change
(`min-versions-to-keep: 30` → `10`) when that instruction is lifted.

Whatever the number, `prune` never removes the newest versions, so the deployed
release and its immediate predecessors are always kept.

## What would unblock the cutover

In the Operations repository:

- repository **variables** `OPERATIONS_PUBLIC_URL` and `MARKETING_PUBLIC_URL`
  (the image job skips its build-args step without them),
- an environment named `production`, holding secret `PROD_DEPLOY_SSH_KEY` and
  variables `PROD_KNOWN_HOSTS`, `PROD_DEPLOY_HOST`,
- `deploy/production/provision_deploy_user.sh` run on the host to install the
  `deploy` user and the forced-command script,
- then `AUTO_DEPLOY` flipped to `"true"` only after a manual `workflow_dispatch`
  run of `verify` has passed against the commit already live.

Their values are environment specifics that belong to the person who owns the
host, not in either repository.
