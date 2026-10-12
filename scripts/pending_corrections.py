"""Owner-approved corrections from the 11 Oct 2026 audit. Dry run unless --apply.

Run inside the operations-api container:

    docker exec -i -w /app -e PYTHONPATH=/app teleautomation-production-operations-api-1 \\
        python - --item F < scripts/pending_corrections.py                    # dry run
    ... python - --item F --apply --confirm 12 < scripts/pending_corrections.py  # apply

Every item prints exactly what it would change (before -> after) and how many
records. --apply writes only with the item letter AND --confirm equal to that
count, so a plan that moved since it was approved refuses to run. Writes go
through the store's own functions (the same ones the dashboard uses), never by
editing storage directly. Nothing is deleted: a resume copy or orphan file is
moved to the `_superseded` history folder, an alert is dismissed (kept), a test
screenshot keeps its record and state and gains a note. Payment amounts are not
an item here: they need the owner's figures.

Items (the records are named on the command line, never in this file):
  K  dismiss "Booking blocked" mail alerts raised before --before (default 2026-09-19)
  F  --name NAME --from-email OLD --to-email NEW   (every row of that candidate)
  C1 --name NAME --proof-sha PREFIX [PREFIX ...]     note those proofs as test screenshots (state unchanged)
  C2 --name NAME --utr UTR                          confirm that proof's receiver as the company (verifies it)
  J  archive identical stored resume copies and orphan resume files to history
  B  --row NAME --cancel | --date YYYY-MM-DD        a slot dated 2027 or later
  E  --name NAME --phone NUMBER                     every row of that candidate holding another number
  N1 post verified company/referrer payments that never reached the ledger (one credit per transaction)

Prints names, dates and counts only: no record ids, no secrets.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

REVIEWER = "owner-approved 2026-10-11"
TEST_IMAGE_NOTE = "TEST / DEMO screenshot - not a real payment (owner review, 11 Oct 2026)"


def _cs():
    from features import candidate_store as cs
    return cs


def _rows(cs):
    return cs._load(force=True).get("candidates") or []


def _named(rows, name):
    want = " ".join(str(name or "").lower().split())
    if not want:
        raise SystemExit("--name is required for this item")
    return [r for r in rows if " ".join(str(r.get("name") or "").lower().split()) == want]


# ── each item returns a list of (description, apply_fn) ──────────────────────

def item_k(cs, args):
    from core import recruitment_mail_store as store
    vis, params = store.notification_visibility_sql()
    with store.get_connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT id, created_at, candidate_email, COALESCE(booking_block_reason_code,'')
                       FROM mail_monitoring_notifications
                       WHERE dismissed_at IS NULL AND booking_status IN ('Blocked','Processing Failed')
                         AND created_at < %s AND (""" + vis + ") ORDER BY created_at", [args.before, *params])
        found = cur.fetchall()
    return [(f"dismiss alert {str(at)[:16]} {email} {code}",
             lambda nid=nid: store.update_notification(nid, "dismiss", reviewer=REVIEWER,
                                                       notes="Historical blocked alert dismissed on owner approval; evidence kept."))
            for nid, at, email, code in found]


def item_f(cs, args):
    if not args.from_email or not args.to_email:
        raise SystemExit("F needs --from-email and --to-email")
    old = args.from_email.strip().lower()
    rows = [r for r in _named(_rows(cs), args.name) if str(r.get("email") or "").strip().lower() == old]
    return [(f"{r.get('name')} slot {r.get('date') or '-'}: email {r.get('email')} -> {args.to_email}",
             lambda cid=r["id"]: cs.update_candidate(cid, {"email": args.to_email}))
            for r in rows]


def item_c1(cs, args):
    shas = tuple(s.lower() for s in (args.proof_sha or []) if len(s) >= 8)
    if not shas:
        raise SystemExit("C1 needs --proof-sha with at least 8 characters each")
    out = []
    for r in _named(_rows(cs), args.name):
        for p in r.get("payment_proofs") or []:
            if str(p.get("sha256") or "").lower().startswith(shas) and p.get("note") != TEST_IMAGE_NOTE:
                out.append((f"{r.get('name')} slot {r.get('date') or '-'}: proof {str(p.get('sha256'))[:8]} "
                            f"({p.get('verification_state')}) note {p.get('note')!r} -> {TEST_IMAGE_NOTE!r}",
                            lambda cid=r["id"], pid=p["id"]: cs.update_proof_note(cid, pid, TEST_IMAGE_NOTE)))
    return out


def item_c2(cs, args):
    if not args.utr:
        raise SystemExit("C2 needs --utr")
    out = []
    for r in _named(_rows(cs), args.name):
        for p in r.get("payment_proofs") or []:
            if p.get("verification_state") == "INCOMPLETE_PAYMENT_EVIDENCE" and str(p.get("utr_number")) == args.utr:
                out.append((f"{r.get('name')} slot {r.get('date') or '-'}: Rs {p.get('verified_amount')} UTR {p.get('utr_number')} "
                            f"receiver {p.get('receiver_name')!r} {p.get('receiver_upi_id')!r}: INCOMPLETE -> confirmed company receiver",
                            lambda cid=r["id"], pid=p["id"]: cs.confirm_proof_receiver(
                                cid, pid, receiver_type="company", receiver_name=str(p.get("receiver_name") or ""),
                                reason="The masked UPI on the screenshot is the company's registered account; the app caption "
                                       "after the handle blocked the automatic match (fixed in 5de6936).",
                                reviewer=REVIEWER)))
    return out


def item_j(cs, args):
    rows = _rows(cs)
    by_digest = {}
    for r in rows:
        for it in r.get("resumes") or []:
            if cs._resume_file_present(str(r.get("id")), it):
                by_digest.setdefault(cs._resume_digest(str(r.get("id")), it), []).append((r, it))
    out = []
    for copies in by_digest.values():
        if len(copies) < 2:
            continue
        copies.sort(key=lambda pair: str(pair[1].get("uploaded_at") or ""))
        for r, it in copies[:-1]:  # the newest copy stays the active one
            out.append((f"{r.get('name')}: archive copy uploaded {str(it.get('uploaded_at'))[:16]} "
                        f"({it.get('original_name') or it.get('filename')}); newest copy stays",
                        lambda cid=r["id"], rid=it.get("id"): _archive_resume(cs, cid, rid)))
    referenced = {(str(r.get("id")), str(it.get("filename") or "")) for r in rows for it in (r.get("resumes") or [])}
    history = {(str(r.get("id")), str(h.get("filename") or "")) for r in rows for h in (r.get("resume_history") or [])}
    if os.path.isdir(cs.RESUMES_DIR):
        for folder in sorted(os.listdir(cs.RESUMES_DIR)):
            path = os.path.join(cs.RESUMES_DIR, folder)
            if not os.path.isdir(path):
                continue
            for fn in sorted(os.listdir(path)):
                if fn != cs.SUPERSEDED_RESUME_DIR and (folder, fn) not in referenced and (folder, fn) not in history \
                        and os.path.isfile(os.path.join(path, fn)):
                    out.append((f"orphan file ({os.path.getsize(os.path.join(path, fn))} bytes, no record) -> history folder",
                                lambda path=path, fn=fn: _archive_file(cs, path, fn)))
    return out


def _archive_file(cs, folder, filename):
    archive = os.path.join(folder, cs.SUPERSEDED_RESUME_DIR)
    os.makedirs(archive, exist_ok=True)
    target = os.path.join(archive, filename)
    if os.path.exists(target):
        raise RuntimeError("an archived file with that name already exists; nothing moved")
    os.replace(os.path.join(folder, filename), target)
    return target


def _archive_resume(cs, cid, rid):
    """Move one resume record to the row's history, then its file to `_superseded`.

    The record moves first, in a single-row patch: if that fails nothing has
    moved. If the file move then fails, the history record still names it and
    the file stays where it was (still referenced, never orphaned)."""
    from datetime import datetime, timezone

    seen = {}

    def change(row):
        resumes = list(row.get("resumes") or [])
        gone = [it for it in resumes if it.get("id") == rid]
        if len(gone) != 1:
            raise RuntimeError("the resume record moved since the dry run; nothing changed")
        record = dict(gone[0])
        record["superseded_at"] = datetime.now(timezone.utc).isoformat()
        record["superseded_reason"] = "identical copy archived on owner approval (11 Oct 2026)"
        record["archived_file"] = os.path.join(cs.SUPERSEDED_RESUME_DIR, str(record.get("filename") or ""))
        seen["filename"] = str(record.get("filename") or "")
        return {"resumes": [it for it in resumes if it.get("id") != rid],
                "resume_history": list(row.get("resume_history") or []) + [record]}

    row = cs._patch_row_fields(cid, change)
    if row is None:
        return None
    folder = os.path.join(cs.RESUMES_DIR, str(cid))
    if seen.get("filename") and os.path.isfile(os.path.join(folder, seen["filename"])):
        _archive_file(cs, folder, seen["filename"])
    return row


def _far_rows(cs, name):
    return [r for r in _rows(cs) if str(r.get("name") or "").strip().lower() == name.strip().lower()
            and str(r.get("date") or "")[:4] >= "2027"]


def item_b(cs, args):
    if not args.row or not (args.cancel or args.date):
        raise SystemExit("B needs --row NAME and --cancel or --date YYYY-MM-DD")
    out = []
    for r in _far_rows(cs, args.row):
        if args.cancel:
            out.append((f"{r.get('name')} {r.get('date')} {r.get('time')}: mark cancelled (date and history kept)",
                        lambda cid=r["id"]: cs.set_interview_attendance(cid, status="cancelled", remark="Mis-dated booking cancelled on owner approval (11 Oct 2026)",
                                                                        by=REVIEWER, allow_future=True)))
        else:
            out.append((f"{r.get('name')} {r.get('date')} -> {args.date}", lambda cid=r["id"]: cs.update_candidate(cid, {"date": args.date})))
    return out


def item_e(cs, args):
    if not args.phone:
        raise SystemExit("E needs --phone NUMBER")
    want = cs.candidate_phone_identity(args.phone)
    return [(f"{r.get('name')} {r.get('interview_round')} {r.get('date')}: phone {r.get('phone')} -> {args.phone}",
             lambda cid=r["id"]: cs.update_candidate(cid, {"phone": args.phone}))
            for r in _named(_rows(cs), args.name) if cs.candidate_phone_identity(r.get("phone")) != want]


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def item_n1(cs, args):
    """Verified payments the ledger never credited (12 Oct audit, item N1).

    Found by transaction: a proof already linked to an entry, or whose UTR or
    payment already has one, is skipped. Profile bookings copy one receipt onto
    every booking row, so each transaction is posted once and every row
    carrying it is linked. The ledger writer's idempotency keys make a re-run a
    no-op."""
    from features import payment_verification_engine as pve

    entries = pve.ledger_entries()
    posted_refs = {_digits(e.get("external_transaction_id")) for e in entries} - {""}
    posted_payments = {e.get("payment_id") for e in entries if e.get("payment_id")}
    groups = {}
    for r in _rows(cs):
        for p in r.get("payment_proofs") or []:
            if p.get("verification_state") not in ("VERIFIED_COMPANY_PAYMENT", "VERIFIED_REFERRER_PAYMENT"):
                continue
            ref = _digits(p.get("utr_number") or p.get("transaction_id"))
            if p.get("ledger_entry_id") or (ref and ref in posted_refs) or p.get("payment_id") in posted_payments:
                continue
            groups.setdefault(ref or str(p.get("sha256") or p.get("id")), []).append((r, p))
    out = []
    for key, holders in sorted(groups.items(), key=lambda kv: str(kv[1][0][1].get("uploaded_at") or "")):
        holders.sort(key=lambda rp: str(rp[0].get("created_at") or ""))
        r, p = holders[0]

        def apply(r=r, p=p, holders=holders):
            links = pve.credit_confirmed_payment(
                p, source_module="owner_approved_backfill", candidate_id=str(r["id"]),
                entity_name=str(r.get("name") or ""), referrer_id=str(r.get("reference") or ""),
                payment_scope=str(p.get("payment_scope") or "")
                or ("ROUND" if r.get("service_type") == "round_wise" else "PROFILE"),
            )
            if not links.get("ledger_entry_id"):
                return None
            for row, proof in holders:
                cs.link_proof_to_ledger(str(row["id"]), str(proof["id"]), links)
            return links

        out.append((f"{r.get('name')} Rs {p.get('verified_amount')} UTR ...{key[-4:]} paid {str(p.get('uploaded_at') or '')[:10]} "
                    f"({p.get('verification_state')}): post the credit to the ledger; link {len(holders)} row(s)", apply))
    return out


ITEMS = {"K": item_k, "F": item_f, "C1": item_c1, "C2": item_c2, "J": item_j, "B": item_b, "E": item_e, "N1": item_n1}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--item", required=True, choices=sorted(ITEMS))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", type=int)
    ap.add_argument("--row"), ap.add_argument("--cancel", action="store_true"), ap.add_argument("--date"), ap.add_argument("--phone")
    ap.add_argument("--name"), ap.add_argument("--from-email"), ap.add_argument("--to-email"), ap.add_argument("--utr")
    ap.add_argument("--proof-sha", nargs="+"), ap.add_argument("--before", default="2026-09-19")
    args = ap.parse_args(argv)
    cs = _cs()
    fn = ITEMS[args.item]
    changes = fn(cs, args)
    for text, _ in changes:
        print(("APPLY  " if args.apply else "WOULD  ") + text)
    print(f"item {args.item}: {len(changes)} change(s)")
    if not args.apply:
        return 0
    if args.confirm != len(changes):
        print(f"refused: --confirm {args.confirm} does not match the {len(changes)} change(s) planned now; nothing written")
        return 2
    for text, apply in changes:
        result = apply()
        if result is None:
            print(f"FAILED (no row/record returned) at: {text}; earlier changes in this run stand")
            return 1
    print(f"done: {len(changes)} change(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
