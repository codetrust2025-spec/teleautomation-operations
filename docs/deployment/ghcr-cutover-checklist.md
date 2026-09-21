# GHCR cutover: what to configure, and how to get each value safely

## Status, 21 Sep 2026

Steps 1-5 were done on 17 Sep and step 6 passed the same day: the host log
records `init`, `verify`, `rollback` and a live `deploy` of 21e0569 from the
registry. Every deploy after that went through `fix_and_deploy.sh`'s raw
`docker compose up`, which never told the release record, so by 21 Sep the host
served 84c8f29 while recording 21e0569 -- the state rollback, the root compose
wrapper and a failed release's automatic restore would all have acted on.

On 21 Sep the record was put right with `deploy-local` for the running image,
84c8f29 was released from the registry by digest through the workflow, and
rollback was proven both ways between the two builds of that commit.
`fix_and_deploy.sh` now releases through the workflow and the host tool by
default (Marketing #231), refuses to release onto a stale record, and fails
`verify` until `teleautomation-deploy status` agrees.

Still open, and not engineering decisions: `AUTO_DEPLOY` stays `"false"` --
flipping it releases every merge to main without Marketing's pin and
dual-service check -- and the release anchor stays, because Marketing CI,
`ci_classify.sh`, the compose file and `fix_and_deploy.sh`'s sync check all
still read it.

Everything in the pipeline already exists. What is missing is configuration that
only the person holding the host and the GitHub account can create. This is that
list, with the exact way to produce each value and nothing invented.

Verified on the host 2026-09-17: the Marketing checkout is in place at
`/opt/teleautomation/marketing`, sshd has no `AllowUsers` restriction to amend,
and neither `/usr/local/sbin/teleautomation-deploy` nor the `deploy` user exists
yet — so provisioning has genuinely never run.

Repository variables `OPERATIONS_PUBLIC_URL` and `MARKETING_PUBLIC_URL` have
existed since 2026-09-14 and need nothing.

## 1. A `production` environment

GitHub → the Operations repository → **Settings → Environments → New
environment** → name it exactly `production`.

While you are there, set **Deployment branches** to *Selected branches* and add
`main`. That is what stops a branch from reaching the host by dispatching the
workflow from itself.

The `deploy` job names this environment, so until it exists the job cannot run
at all.

## 2. `PROD_DEPLOY_SSH_KEY` — an environment **secret**

Generate a **new** key that exists only for CI. Do not reuse the operator key
you deploy with today: the point of a separate one is that it can be revoked
without locking yourself out, and it is restricted on the host to a single
command.

```bash
ssh-keygen -t ed25519 -C "teleautomation-ci-deploy" -f ~/.ssh/teleautomation_ci_deploy -N ""
```

- **Private half** (`~/.ssh/teleautomation_ci_deploy`) → paste the whole file,
  including the `-----BEGIN`/`-----END` lines and the trailing newline, as the
  value of environment secret `PROD_DEPLOY_SSH_KEY`. It is write-only once
  saved; nothing and nobody reads it back, including me.
- **Public half** (`~/.ssh/teleautomation_ci_deploy.pub`) → used in step 5.

Passphrase must be empty (`-N ""`): a workflow cannot type one.

## 3. `PROD_KNOWN_HOSTS` — an environment **variable**

This pins the host key so the workflow refuses to connect to anything else
rather than trusting whatever answers.

```bash
ssh-keyscan -t ed25519 <production-host> 2>/dev/null
```

**Verify before you trust it.** Compare the fingerprint of what keyscan returned
against the host's own, which was read directly on the host:

```
SHA256:7Pmsg7NxGd4WdqJ8Wd5I+BQVM+M9IywtGdFNQdJZetY   (ssh-ed25519, root@srv1926295)
```

Check yours with:

```bash
ssh-keyscan -t ed25519 <production-host> 2>/dev/null | ssh-keygen -lf -
```

If those two do not match, stop — something is answering for the host. If they
match, the full `ssh-keyscan` line (host, key type, key) is the variable value.

## 4. `PROD_DEPLOY_HOST` — an environment **variable**

The address the workflow connects to: the same host `KVM1_SSH` points at today,
without the `root@`. It goes in the environment, never in either repository —
that is why it is a variable and not a committed value.

## 5. Provision the host

Run with the **public** half on stdin. The script refuses anything that is not
exactly one `ssh-ed25519` public key line, which is the guard against pasting a
private key by accident.

```bash
ssh root@<production-host> \
  'bash /opt/teleautomation/marketing/deploy/production/provision_deploy_user.sh' \
  < ~/.ssh/teleautomation_ci_deploy.pub
```

What it does, and nothing else: creates the `deploy` system user whose key can
only run `/usr/local/sbin/teleautomation-deploy` (forced command plus
`restrict`: no shell, no pty, no forwarding); allows that user to run exactly
that script as root through one sudo rule; installs `teleautomation-deploy` and
`teleautomation-compose` from the checkout, owned by root and not writable by
`deploy`; and records the currently running Operations container as the first
release, so the first CI deploy has something verified to roll back to.

It changes no running container and reads no secret. Re-running it replaces the
key and reinstalls the scripts, keeping the recorded release.

This is a privileged host change — a new user and a sudo rule — so it is yours
to run, not something to automate from a session.

## 6. Then, and only then: prove it

With all of the above in place, the proof runs against the commit **already
live**, so a failure changes nothing:

1. Actions → **deploy** → Run workflow → branch `main`, action `verify`.
2. `verify` pulls the image by digest and inspects it without restarting
   anything: the image's `org.opencontainers.image.revision` label and its baked
   `RELEASE_SHA` must both equal the requested commit.
3. Then check, as the pipeline already does: `/version` reports that commit; the
   public site and `/health` return 200; every container in the compose project
   is healthy.
4. Rollback: `teleautomation-deploy rollback` on the host restores the release
   recorded in step 5 from an image **already present** — it never re-pulls, and
   refuses if that image is gone. Prove it by rolling back and forward once
   while the two releases are the same commit, so nothing changes but the
   recorded state.

Only after that passes should `AUTO_DEPLOY` be flipped to `"true"`, and only
after the registry path has actually deployed something should the release
anchor be retired — until then the anchor is the only check tying a running
container to a reviewed commit.
