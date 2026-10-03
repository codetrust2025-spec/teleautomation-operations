#!/usr/bin/env python3
"""Manifest and restore verification for TeleAutomation backups. Stdlib only.

The nightly backup (deploy/ops/teleautomation-backup) stages a consistent copy of
production in one directory and snapshots that directory with restic:

    db/operations.sql            pg_dump --no-owner --no-acl (plain SQL)
    db/operations.counts.tsv     exact row count of every table, taken right after the dump
    db/marketing.sql
    db/marketing.counts.tsv
    operations-data/             the operations volume: proofs, resumes, ledger, ...
    marketing-data/              the marketing volume
    host-config.tar              env file, release record, nginx, TLS, systemd units, scripts
    manifest.json                written last, by `manifest`

    ta_backup_verify.py manifest STAGE > STAGE/manifest.json
    ta_backup_verify.py files    RESTORED_STAGE           # every file re-hashed against the manifest
    ta_backup_verify.py counts   RESTORED_STAGE DB RESTORED_COUNTS.tsv

"Restorable" is proven, not assumed: `files` re-hashes every file restic gave
back and `counts` compares the row count of every table restored into a scratch
Postgres with the count measured from production when the dump was taken.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

DATABASES = ("operations", "marketing")
# Rows written or deleted between the dump and the count are not corruption.
COUNT_TOLERANCE_ABS = 200
COUNT_TOLERANCE_REL = 0.02
# Files the business cannot run without; a backup missing one is not a backup.
REQUIRED_FILES = ("operations-data/payment_verification_ledger.json", "db/operations.sql", "db/marketing.sql")
MANIFEST = "manifest.json"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def walk_files(root: str) -> dict[str, dict[str, Any]]:
    """{relative/path: {size, sha256}} for every regular file under root, manifest excluded."""
    out: dict[str, dict[str, Any]] = {}
    for base, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel == MANIFEST or os.path.islink(path) or not os.path.isfile(path):
                continue
            out[rel] = {"size": os.path.getsize(path), "sha256": sha256_file(path)}
    return out


def parse_counts(path: str) -> dict[str, int]:
    """`table<TAB>count` lines, as psql -At -F'\\t' writes them."""
    counts: dict[str, int] = {}
    if not os.path.isfile(path):
        return counts
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line and "\t" in line:
                table, value = line.rsplit("\t", 1)
                counts[table] = int(value)
    return counts


def build_manifest(stage: str) -> dict[str, Any]:
    files = walk_files(stage)
    data = [p for p in files if p.startswith("operations-data/")]
    return {
        "format": 2,
        "files": files,
        "row_counts": {db: parse_counts(os.path.join(stage, "db", f"{db}.counts.tsv")) for db in DATABASES},
        "summary": {
            "files": len(files),
            "bytes": sum(meta["size"] for meta in files.values()),
            "operations_data_files": len(data),
            "operations_proof_files": sum(1 for p in data if p.startswith("operations-data/candidates_proofs/")),
        },
    }


def verify_files(restored: str) -> list[str]:
    """Problems found re-hashing a restored stage. Empty means sound."""
    path = os.path.join(restored, MANIFEST)
    if not os.path.isfile(path):
        return ["manifest.json missing from the restored snapshot"]
    with open(path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    expected = manifest.get("files") or {}
    actual = walk_files(restored)
    problems: list[str] = []
    missing = sorted(set(expected) - set(actual))
    changed = sorted(p for p in expected if p in actual and actual[p]["sha256"] != expected[p]["sha256"])
    if missing:
        problems.append(f"{len(missing)} file(s) missing after restore, e.g. {missing[0]}")
    if changed:
        problems.append(f"{len(changed)} file(s) differ from the manifest after restore, e.g. {changed[0]}")
    for required in REQUIRED_FILES:
        if required not in expected:
            problems.append(f"backup has no {required}")
    if not expected:
        problems.append("manifest lists no files")
    return problems


def compare_counts(expected: dict[str, int], restored: dict[str, int]) -> list[str]:
    if not expected:
        return ["no row counts recorded for this database"]
    problems: list[str] = []
    missing = sorted(set(expected) - set(restored))
    if missing:
        problems.append(f"{len(missing)} table(s) not restored: {', '.join(missing[:5])}")
    for table, live in sorted(expected.items()):
        if table in restored:
            allowed = max(COUNT_TOLERANCE_ABS, int(live * COUNT_TOLERANCE_REL))
            if abs(restored[table] - live) > allowed:
                problems.append(f"{table}: restored {restored[table]} rows, production had {live}")
    return problems


def verify_counts(restored: str, database: str, restored_counts_path: str) -> list[str]:
    with open(os.path.join(restored, MANIFEST), encoding="utf-8") as stream:
        manifest = json.load(stream)
    return compare_counts(manifest["row_counts"].get(database) or {}, parse_counts(restored_counts_path))


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "manifest":
        json.dump(build_manifest(argv[2]), sys.stdout, indent=1, sort_keys=True)
        return 0
    if len(argv) == 3 and argv[1] == "files":
        problems = verify_files(argv[2])
    elif len(argv) == 5 and argv[1] == "counts":
        problems = verify_counts(argv[2], argv[3], argv[4])
    else:
        print(__doc__, file=sys.stderr)
        return 2
    for problem in problems:
        print(f"VERIFY FAILED: {problem}", file=sys.stderr)
    if not problems:
        print("verified")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
