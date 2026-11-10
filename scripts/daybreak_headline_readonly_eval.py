#!/usr/bin/env python3
"""Read-only end-to-end evaluation of the Daybreak headline rewrite over real candidate data.

It drives the real route (`POST /api/integrations/daybreak/headline`) through the real AI gateway, with
the gateway's primary node pointed at a local Ollama, and feeds it the real approved facts and current
headlines of every candidate in a Daybreak D1 `.sql` backup that already exists on this machine. Each
returned headline is then put through the two Daybreak-side gates it would meet in production: the
server's 30-day duplicate fence, and the companion's own character-level change and fact-preservation
rules. The per-account verdict is the conjunction of all three.

Read-only by construction: it reads a backup file and never writes it, talks to no Daybreak, Naukri or
Cloudflare endpoint, saves no headline anywhere, and keeps the AI gateway's runtime state in a temporary
file. Reports hold real candidate headlines, so write them outside the repository.

    python scripts/daybreak_headline_readonly_eval.py --backup <path>/daybreak.sql --report <path>.json

Only a local Ollama URL is accepted. `--runs` repeats every account to show how often it succeeds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WINDOW_DAYS = 30


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backup", required=True, help="a Daybreak D1 .sql backup, read and never written")
    parser.add_argument("--report", help="where to write the JSON report")
    parser.add_argument("--runs", type=int, default=1, help="generations per account (default 1)")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many accounts")
    parser.add_argument("--only", default="", help="comma-separated profile ids, to re-examine single accounts")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be 1 or more")
    if not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):\d+", args.ollama_url):
        parser.error("only a local Ollama URL is allowed")
    return args


def open_backup(path: str) -> sqlite3.Connection:
    """Load the dump into memory. The file on disk is opened for reading only."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    statement = ""
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        statement += ("\n" if statement else "") + line
        if not statement.rstrip().endswith(";"):
            continue
        try:
            connection.execute(statement)
        except sqlite3.Error:
            pass  # a statement this sqlite build rejects cannot affect the reads below
        statement = ""
    connection.commit()
    return connection


def candidates(connection: sqlite3.Connection, limit: int, only: str = "") -> list[dict]:
    rows = connection.execute(
        "SELECT id, role, experience, skills, facts, naukri_headline FROM profiles "
        "WHERE enabled=1 AND naukri_headline IS NOT NULL AND TRIM(naukri_headline)<>'' ORDER BY id"
    ).fetchall()
    wanted = {part.strip() for part in only.split(",") if part.strip()}
    out = []
    for row in rows:
        try:
            skills = json.loads(row["skills"] or "[]")
        except json.JSONDecodeError:
            skills = [part for part in str(row["skills"] or "").split(",") if part.strip()]
        skills = [str(s).strip()[:60] for s in skills if str(s).strip()][:30]
        facts = {"target_role": str(row["role"] or "").strip()[:100], "experience": str(row["experience"] or "").strip()[:60],
                 "skills": skills, "profile_facts": str(row["facts"] or "").strip()[:1000]}
        if len(skills) < 2 and len(facts["profile_facts"]) < 40:
            continue  # the companion refuses to generate without enough approved facts
        if wanted and row["id"] not in wanted:
            continue
        out.append({"profile_id": row["id"], "facts": facts, "previous": str(row["naukri_headline"])})
        if limit and len(out) >= limit:
            break
    return out


# The Daybreak server's own normalized duplicate key (companion/editorial-policy.mjs), reproduced so the
# 30-day fence this reports is the one production applies, not an approximation of it.
_DUPLICATE_SUBSTITUTIONS = [
    (r"\bgen(?:erative)?\s*ai\b", "generativeai"), (r"\bjavascript\b", "js"),
    (r"\biam\b", "identityaccessmanagement"), (r"\biiq\b", "identityiq"),
    (r"\bgcp\b", "googlecloudplatforms"), (r"\bgcs\b", "googlecloudstorage"),
    (r"\bpom\b", "pageobjectmodel"), (r"\baws\b", "amazonwebservices"),
    (r"\brest(?:ful)?\s*apis?\b", "restapi"), (r"\bdevelopment\b", "developer"),
    (r"\bengineering\b", "engineer"), (r"\b(\d+)\s*(?:years?|yrs?|yoe)\b", r"\1years"),
    (r"\b(\d+)\s*(?:months?|mos?)\b", r"\1months"),
]


def duplicate_key(value: str) -> str:
    value = str(value or "").lower()
    for pattern, replacement in _DUPLICATE_SUBSTITUTIONS:
        value = re.sub(pattern, replacement, value)
    return re.sub(r"[^a-z0-9+#]+", "", value)


def saved_recently(connection: sqlite3.Connection, profile_id: str, as_of: str) -> set[str]:
    since = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(
        time.mktime(time.strptime(as_of[:19], "%Y-%m-%dT%H:%M:%S")) - WINDOW_DAYS * 86400))
    rows = connection.execute(
        "SELECT generated_headline FROM headline_history WHERE profile_id=? AND generated_at>=? "
        "AND save_result='saved' AND verification='verified'", (profile_id, since)).fetchall()
    return {duplicate_key(row["generated_headline"]) for row in rows}


def local_gateway(url: str):
    """Point the real AI gateway's Daybreak primary node at the local Ollama, nothing else changed."""
    os.environ["OLLAMA_NODE_RTX4060_URL"] = url
    os.environ["OLLAMA_NODE_STATE_FILE"] = str(Path(tempfile.gettempdir()) / "daybreak_eval_nodes_state.json")
    os.environ.setdefault("DAYBREAK_HEADLINE_TOKEN", "readonly-evaluation-token-" + "e" * 24)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    local_gateway(args.ollama_url)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import daybreak_integration as integration

    app = FastAPI()
    app.include_router(integration.router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + os.environ["DAYBREAK_HEADLINE_TOKEN"]}

    connection = open_backup(args.backup)
    as_of = connection.execute("SELECT MAX(generated_at) AS at FROM headline_history").fetchone()["at"] or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    people = candidates(connection, args.limit, args.only)
    if not people:
        print("no eligible candidates in this backup", file=sys.stderr)
        return 1

    print(f"P10 ({integration.REWRITE_MODEL}, temperature {integration.REWRITE_TEMPERATURE}, "
          f"num_predict {integration.REWRITE_NUM_PREDICT}) through the real gateway at {args.ollama_url}")
    print(f"{len(people)} accounts x {args.runs} runs; 30-day fence measured from {as_of}")
    print("read-only: no Daybreak, Naukri or Cloudflare request, and no headline is saved\n")

    totals = {"runs": 0, "valid": 0, "gateway_accepted": 0, "changed": 0, "dropped_facts": 0,
              "invented_terms": 0, "over_limit": 0, "duplicate_in_30_days": 0, "errors": 0}
    accounts, distinct_by_account = [], []
    for index, person in enumerate(people, 1):
        recent = saved_recently(connection, person["profile_id"], as_of)
        body = {"candidate_ref": person["profile_id"], "facts": person["facts"],
                "previous_headline": person["previous"], "max_length": 250, "rewrite": True}
        request = integration.DaybreakHeadlineRequest.model_validate(body)
        runs, drafts = [], []
        for _ in range(args.runs):
            totals["runs"] += 1
            response = client.post("/api/integrations/daybreak/headline", headers=headers, json=body)
            if response.status_code != 200:
                totals["errors"] += 1
                runs.append({"http": response.status_code, "valid": False, "reason": "http_" + str(response.status_code)})
                continue
            data = response.json()
            headline = data["headline"]
            drafts.append(headline)
            # The two Daybreak-side gates, in the order production applies them.
            duplicate = duplicate_key(headline) in recent
            dropped = integration._dropped_facts(request, headline)
            invented = integration._invented_terms(request, headline)
            changed = headline != person["previous"].strip()
            valid = bool(data["rewrite_accepted"]) and changed and not duplicate and not dropped and not invented and len(headline) <= 250
            totals["gateway_accepted"] += bool(data["rewrite_accepted"])
            totals["changed"] += changed
            totals["dropped_facts"] += bool(dropped)
            totals["invented_terms"] += bool(invented)
            totals["over_limit"] += len(headline) > 250
            totals["duplicate_in_30_days"] += duplicate
            totals["valid"] += valid
            runs.append({"headline": headline, "characters": len(headline), "valid": valid,
                         "changed": changed, "attempts": data["rewrite_attempts"],
                         "gateway_reason": data["rewrite_reason"], "duplicate_in_30_days": duplicate,
                         "dropped_facts": dropped, "invented_terms": invented})
        distinct = len(set(drafts))
        distinct_by_account.append(distinct)
        valid_runs = sum(1 for run in runs if run["valid"])
        accounts.append({"account": index, "profile_id": person["profile_id"],
                         "required_facts": integration._required_facts(request),
                         "previous_characters": len(person["previous"]), "saved_in_window": len(recent),
                         "valid_runs": valid_runs, "distinct": distinct, "runs": runs})
        print(f"account {index:>2}/{len(people)}  valid {valid_runs}/{len(runs)}  unique {distinct}  "
              f"attempts {runs[-1].get('attempts', '-')}  {runs[-1].get('reason') or runs[-1].get('gateway_reason') or 'accepted'}")

    fully_valid = sum(1 for account in accounts if account["valid_runs"] == args.runs)
    any_valid = sum(1 for account in accounts if account["valid_runs"])
    print(f"\nACCOUNTS: {any_valid}/{len(accounts)} produced a valid rewrite, "
          f"{fully_valid}/{len(accounts)} on every run")
    print(f"RUNS over {totals['runs']} generations")
    for label, key in (("valid end to end", "valid"), ("accepted by the gateway", "gateway_accepted"),
                       ("changed (one character counts)", "changed"), ("dropped a verified fact", "dropped_facts"),
                       ("used a word not in the approved facts", "invented_terms"), ("over 250 characters", "over_limit"),
                       ("already saved in the last 30 days", "duplicate_in_30_days"), ("errors", "errors")):
        value = totals[key]
        print(f"  {label:<46}{value:>5}  {100 * value / totals['runs']:.1f}%")

    if args.report:
        Path(args.report).write_text(json.dumps({
            "prompt": "P10 Free safe restructure", "model": integration.REWRITE_MODEL,
            "temperature": integration.REWRITE_TEMPERATURE, "num_predict": integration.REWRITE_NUM_PREDICT,
            "runs_per_account": args.runs, "window_days": WINDOW_DAYS, "as_of": as_of,
            "backup": args.backup, "totals": totals,
            "accounts_with_a_valid_rewrite": any_valid, "accounts": accounts,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
        print("\nreport: " + args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
