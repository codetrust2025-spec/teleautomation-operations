# teleautomation-operations

Independent TeleAutomation Operations service. Its hostname is supplied per environment; no production domain is assumed by this repository.

## Local run

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn main:app --reload
cd dashboard; npm ci; npm run dev
```

Runtime data and secrets are intentionally excluded. See `docs/migration/` for frozen contracts and cutover plans.

## Deployment

This service does not deploy itself. `docker-compose.production.yml` and the
release anchor naming the Operations commit both live in the companion
`teleautomation-messaging` repository, so a deployment is driven from there
with the two repositories checked out side by side:

```bash
cd ../teleautomation-messaging
OPERATIONS_BRANCH=your-branch KVM1_SSH=user@host bash scripts/fix_and_deploy.sh
```

That opens the Operations pull request, waits for its checks, merges it, moves
the release anchor, waits again, syncs the host, builds, deploys, and verifies
`/version` and container health. It is resumable: re-running after an
interruption continues from the first incomplete stage rather than repeating a
merge or a deployment.

Hostnames, key paths and env-file locations are supplied through the
environment, never committed here. `CLAUDE.md` carries the same instructions
for agents, including when *not* to deploy.

## AI Mail model selection

AI Mail Review lists installed Ollama model names, byte sizes and node
availability beside Pure Ollama. Admin Test checks both mail relevance and
classification JSON schemas on at least two nodes; Save independently repeats
validation before atomically persisting a revision-checked selection in
`OPERATIONS_DATA_DIR/ai_mail_model.json` (`AI_MAIL_MODEL_STATE_FILE` may override
it). The independent validator must also be installed and pass on those nodes.
The existing minimum decision-model size is enforced. There is no model pull.

Foreground and background mail analysis read a snapshot for each decision. The
first successful call chooses a validated node in the existing preference order;
the entire relevance/classification/validation decision stays on that node.
Initial node failure may fail over; failure after a decision starts parks the
mail for retry, preserving the existing one-machine safety rule. Global primary
selection and non-mail AI routes do not read this setting. Actual model/node
provenance is recorded in mail results and the existing activity stream.

The state file contains configuration and last-used model/node only, not mail
or candidate content. It lives on the existing protected Operations data volume;
no database migration, credential change or new binding is required. Failed
validation or a stale revision retains the previous selection. The previous
release/image remains the deployment rollback target.
