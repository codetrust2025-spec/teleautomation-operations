"""The Postgres write path is where the booking corruption actually happened.

Production runs on Postgres, and `pg_save` branches on the snapshot version map
it is handed:

* id present in `_snapshot_versions` -> version-guarded UPDATE
* id absent                          -> `INSERT ... ON CONFLICT (id) DO NOTHING`

A whole-store save built from a snapshot taken before a booking landed hits the
second branch for the freshly created booking row, so the write is accepted and
silently discarded — and every other row is rewritten from that stale snapshot
at the same time. The file backend's merge hides both effects, so these tests
drive `pg_save` directly against a recording stub.
"""

import json

import pytest

from core.db import candidates_pg
from features import candidate_store as cs

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeCursor:
    """An in-memory stand-in for the candidates_store table.

    It applies the writes it is given, honouring the same version guards as the
    real statements, so a read-back sees what a write actually did. A recorder
    that only logged statements would make the store's verify-and-retry loop
    spin, and would not prove the row survived the round trip.
    """

    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self._fetched = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.statements.append((text, params))
        verb = text.split()[0].upper()
        if verb == "SELECT" and "FROM candidates_store" in text:
            self._fetched = [(json.dumps(row),) for row in self.rows]
        elif verb == "UPDATE":
            payload, row_id, expected = params
            for index, row in enumerate(self.rows):
                if str(row.get("id")) == row_id and str(row.get("_store_updated_at") or "") == expected:
                    self.rows[index] = json.loads(payload)
        elif verb == "INSERT":
            row_id, payload = params
            if not any(str(row.get("id")) == row_id for row in self.rows):
                self.rows.append(json.loads(payload))
        elif verb == "DELETE":
            row_id, expected = params
            self.rows = [
                row for row in self.rows
                if not (
                    str(row.get("id")) == row_id
                    and str(row.get("_store_updated_at") or "") == expected
                )
            ]

    def fetchall(self):
        return self._fetched

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def cursor(monkeypatch):
    cur = FakeCursor(rows=[])
    monkeypatch.setattr(candidates_pg, "get_connection", lambda: FakeConnection(cur))
    return cur


def verbs(cursor):
    return [sql.split()[0].upper() for sql, _params in cursor.statements]


def test_a_row_with_a_known_version_is_updated_under_that_guard(cursor):
    candidates_pg.pg_save({
        "candidates": [{"id": "b3500fe1b0", "date": "2026-08-14", "time": "13:00"}],
        "_snapshot_versions": {"b3500fe1b0": "2026-08-13T14:24:00+05:30"},
        "updated_at": "2026-08-13T14:24:06+05:30",
    })

    updates = [(sql, params) for sql, params in cursor.statements if sql.startswith("UPDATE")]
    assert len(updates) == 1
    _sql, params = updates[0]
    assert params[1] == "b3500fe1b0"
    assert params[2] == "2026-08-13T14:24:00+05:30", "the version guard must be the loaded one"
    assert "DELETE" not in verbs(cursor)


def test_a_row_missing_from_the_version_map_is_never_updated(cursor):
    """The silent-drop branch, pinned so it stays visible.

    This is why a targeted patch must always supply the target's version: an id
    absent from the map takes the insert branch, which is a no-op for a row that
    already exists.
    """
    candidates_pg.pg_save({
        "candidates": [{"id": "b3500fe1b0", "date": "2026-08-14", "time": "13:00"}],
        "_snapshot_versions": {},
        "updated_at": "2026-08-13T14:24:06+05:30",
    })

    assert verbs(cursor) == ["SELECT", "INSERT"], "a version-less row cannot update anything"
    insert_sql = cursor.statements[1][0]
    assert "ON CONFLICT (id) DO NOTHING" in insert_sql


def test_a_stale_whole_store_save_cannot_delete_a_newer_row(cursor):
    """Deletion is version-guarded, so a row created after the snapshot lives."""
    candidates_pg.pg_save({
        "candidates": [{"id": "kept"}],
        "_snapshot_versions": {"kept": "v1", "dropped": "v1"},
        "updated_at": "2026-08-13T14:24:06+05:30",
    })

    deletes = [(sql, params) for sql, params in cursor.statements if sql.startswith("DELETE")]
    assert len(deletes) == 1
    sql, params = deletes[0]
    assert params == ("dropped", "v1")
    assert "COALESCE(payload->>'_store_updated_at', '') = %s" in sql


def test_the_targeted_patch_writes_one_guarded_update_and_nothing_else(monkeypatch, cursor):
    """The end of the corruption: one row in, one UPDATE out, no DELETE."""
    booked = {
        "id": "b3500fe1b0", "name": "Aniket", "date": "2026-08-14",
        "time": "13:00", "time_end": "13:45", "slot_confirmed": True,
        "_store_updated_at": "2026-08-13T14:24:00+05:30",
    }
    other = {
        "id": "0475c0fbbf", "name": "Aniket", "date": "2026-08-14",
        "time": "14:00", "time_end": "15:00", "slot_confirmed": True,
        "_store_updated_at": "2026-08-13T09:10:00+05:30",
    }
    cursor.rows = [booked, other]
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: True)
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)

    cs._patch_row_fields("b3500fe1b0", {"slot_screenshot_proof_id": "proof-9"})

    updates = [(sql, params) for sql, params in cursor.statements if sql.startswith("UPDATE")]
    assert len(updates) == 1, "only the target row may be written"
    payload = json.loads(updates[0][1][0])
    assert payload["id"] == "b3500fe1b0"
    assert payload["slot_screenshot_proof_id"] == "proof-9"
    # The booking itself rides along untouched rather than being re-derived.
    assert payload["date"] == "2026-08-14"
    assert payload["time"] == "13:00"
    assert payload["time_end"] == "13:45"
    assert payload["slot_confirmed"] is True
    assert updates[0][1][2] == "2026-08-13T14:24:00+05:30", "guarded on the loaded version"
    assert "DELETE" not in verbs(cursor), "a targeted patch must never delete"
    assert "INSERT" not in verbs(cursor), "the target must not take the silent no-op branch"
    assert all(
        json.loads(params[0])["id"] != "0475c0fbbf"
        for sql, params in cursor.statements
        if sql.startswith(("UPDATE", "INSERT"))
    ), "the other booking must not be rewritten at all"

    # And the stored table agrees: the booking is intact and its neighbour is
    # byte-for-byte what it was.
    saved = {str(row["id"]): row for row in cursor.rows}
    assert saved["b3500fe1b0"]["slot_confirmed"] is True
    assert saved["b3500fe1b0"]["time"] == "13:00"
    assert saved["b3500fe1b0"]["time_end"] == "13:45"
    assert saved["b3500fe1b0"]["slot_screenshot_proof_id"] == "proof-9"
    assert saved["0475c0fbbf"] == other


def test_the_evidence_write_cannot_revert_a_booking_made_after_the_snapshot(monkeypatch, cursor):
    """The Production failure, reproduced on the Postgres path.

    A pre-booking snapshot of the row is saved back after the booking landed —
    exactly what the old whole-store write did 6 ms after booking. The version
    guard rejects it, so the slot stands.
    """
    unbooked = {
        "id": "b3500fe1b0", "name": "Aniket", "date": "", "time": "",
        "time_end": "", "slot_confirmed": False,
        "_store_updated_at": "2026-08-13T09:00:00+05:30",
    }
    cursor.rows = [dict(unbooked)]
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: True)
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)

    stale_snapshot = {
        "candidates": [dict(unbooked)],
        "_snapshot_versions": {"b3500fe1b0": "2026-08-13T09:00:00+05:30"},
    }

    # The booking lands, moving the row's version forward.
    cs._save({
        "candidates": [{
            **unbooked, "date": "2026-08-14", "time": "13:00",
            "time_end": "13:45", "slot_confirmed": True,
        }],
        "_snapshot_versions": {"b3500fe1b0": "2026-08-13T09:00:00+05:30"},
    })
    assert cursor.rows[0]["slot_confirmed"] is True

    # The stale write arrives afterwards and must not win.
    cs._save(stale_snapshot)

    assert cursor.rows[0]["date"] == "2026-08-14"
    assert cursor.rows[0]["time"] == "13:00"
    assert cursor.rows[0]["time_end"] == "13:45"
    assert cursor.rows[0]["slot_confirmed"] is True


def test_evidence_reaches_the_row_even_when_the_cache_predates_the_booking(monkeypatch, tmp_path, cursor):
    """A 15-second read cache is what made the old write stale.

    `_load()` hands back a cached snapshot for up to `_LOAD_CACHE_TTL`. When the
    booking was committed by another worker after that snapshot was taken, the
    old whole-store write built its save from pre-booking rows and a pre-booking
    version map — so the evidence write was accepted and discarded, and the
    booking it described kept no pointer to it. The targeted write re-reads
    first, so it lands on the row as it actually stands.
    """
    monkeypatch.setattr("core.db.connection.use_postgres", lambda: True)
    monkeypatch.setattr(cs, "PROOFS_DIR", str(tmp_path / "proofs"))
    monkeypatch.setattr(cs, "_load_cache", None)
    monkeypatch.setattr(cs, "_load_cache_at", 0.0)
    cursor.rows = [{
        "id": "b3500fe1b0", "name": "Aniket", "date": "", "time": "",
        "time_end": "", "slot_confirmed": False, "slot_screenshot_proofs": [],
        "_store_updated_at": "2026-08-13T09:00:00+05:30",
    }]

    cs._load()  # warms the cache with the pre-booking snapshot

    # Another worker books the interview; the row and its version move on.
    cursor.rows = [{
        "id": "b3500fe1b0", "name": "Aniket", "date": "2026-08-14",
        "time": "13:00", "time_end": "13:45", "slot_confirmed": True,
        "slot_screenshot_proofs": [],
        "_store_updated_at": "2026-08-13T14:24:00+05:30",
    }]

    proof = cs.attach_public_slot_screenshot(
        "b3500fe1b0", data=PNG, original_name="evidence.png",
        mime_type="image/png", source="AI Mail Monitoring",
    )

    stored = cursor.rows[0]
    assert proof and proof["id"]
    assert stored["slot_screenshot_proof_id"] == proof["id"], "evidence was written but not stored"
    assert len(stored["slot_screenshot_proofs"]) == 1
    assert stored["date"] == "2026-08-14"
    assert stored["time"] == "13:00"
    assert stored["time_end"] == "13:45"
    assert stored["slot_confirmed"] is True
