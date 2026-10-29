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

AI Controls owns the model selector, with selected size in GB, Test and Save.
Current model/node is shown below; availability and validation are collapsed.
AI Mail Review retains candidate Gmail operations and a Manage AI Controls
navigation link, without duplicate global AI controls or node polling.
Admin Test checks both mail relevance and
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

## AI Controls

The Operations sidebar's AI Controls dashboard consolidates the existing mail
model selector, AI Nodes component, global OCR and Pure Ollama switches. It uses
the existing cookie-authenticated model, nodes/status/activity, metrics, feature
config, mailbox health and recruitment review APIs, plus the existing activity
websocket. Existing Set Primary, Unload, diagnostics tests and policy audit
routes are reused without backend changes or new permissions.

Installed size is decimal GB. Reported GPU residency is for the first loaded
model from the node health API, not physical GPU capacity or utilization. Queue
counters distinguish current rows from cumulative totals; recruitment events
are not a complete AI job log. Unsupported 24-hour totals, general server logs,
workload overrides and routing pins are explicitly unavailable rather than
invented. Only API-returned configuration is displayed; no host files or
secrets are exposed. The validator is read-only. No inference runs on page load;
tests and mutations require existing explicit actions and confirmations.

Overview is a compact model-selection and node-health panel. Models, Nodes,
Policies, Workloads, Queues and Logs each show only their own content; advanced
diagnostics are collapsed. Installed models and policy history paginate six
rows at a time, and recruitment history paginates five of the API's existing
twenty records. Pagination is local and adds no API. One catalog response feeds
both the selector and inventory; node rendering lives in AiNodeManager.jsx,
independent of the email workspace. Mail results, alerts, Gmail monitoring and
candidate-specific actions remain in AI Mail Review.
