"""Test records that reached production: kept, but left out of money figures.

Fixtures written to the live store (placeholder phones, ids like "lead" and
"booked", a reused payment reference) were counted as revenue, handler
earnings and pending collections. Deleting them would destroy the evidence and
their audit trail, so they stay in the store untouched; the figures skip the
ids listed in `candidate_test_record_exclusions` (migration 041).

Only reporting reads go through `without_test_records`. Never filter what
`candidate_store._load()` returns: that snapshot is saved back whole, and a row
missing from it must never look like a row to remove.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

_TTL_SECONDS = 30.0
_cache: frozenset[str] | None = None
_cache_at = 0.0
_override: frozenset[str] | None = None

log = logging.getLogger(__name__)


def excluded_ids() -> frozenset[str]:
    """Ids of rows marked as test data and not revoked."""
    global _cache, _cache_at
    if _override is not None:
        return _override
    now = time.monotonic()
    if _cache is not None and now - _cache_at < _TTL_SECONDS:
        return _cache
    from core.db.connection import get_connection, use_postgres

    ids: frozenset[str] = frozenset()
    if use_postgres():
        try:
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT candidate_id FROM candidate_test_record_exclusions WHERE revoked_at IS NULL"
                )
                ids = frozenset(str(row[0]) for row in cur.fetchall())
        except Exception:
            # Before migration 041 has run, or a read failure: count everything,
            # exactly as before, rather than fail the figures.
            log.exception("Could not read candidate_test_record_exclusions")
            ids = frozenset()
    _cache, _cache_at = ids, now
    return ids


def is_test_record(row: dict) -> bool:
    return str((row or {}).get("id") or "") in excluded_ids()


def without_test_records(rows: Iterable[dict]) -> list[dict]:
    excluded = excluded_ids()
    if not excluded:
        return list(rows)
    return [row for row in rows if str((row or {}).get("id") or "") not in excluded]


def set_for_tests(ids: Iterable[str] | None) -> None:
    """Pin the excluded ids (tests only); None returns to the database."""
    global _override, _cache
    _override = None if ids is None else frozenset(ids)
    _cache = None
