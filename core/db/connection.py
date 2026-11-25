"""Postgres connection helpers (optional — local dev uses JSON files)."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

_url_checked = False
_database_url: str | None = None


def _resolve_database_url() -> str | None:
    global _url_checked, _database_url
    if _url_checked:
        return _database_url
    _url_checked = True
    url = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
    if url and _under_test() and not is_disposable_test_database(url):
        _url_checked = False
        raise RuntimeError(
            "Refusing to use a non-test database from a test run. Tests may only reach a disposable "
            "local database whose name contains 'test' or 'ci' (for example business_ci on localhost)."
        )
    _database_url = url or None
    return _database_url


# 26 Nov 2026: fixture rows ("Lost Person", "Round Person", placeholder phones 9000000xxx) reached the
# production candidates_store. Tests point candidate_store at a temporary JSON file, but whenever
# DATABASE_URL was set (a test run with production environment loaded) every write went to Postgres.
# A test run may now only reach a disposable local database; anything else is refused before connecting.
_TEST_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _under_test() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def is_disposable_test_database(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    name = (parsed.path or "").lstrip("/").lower()
    return (parsed.hostname or "").lower() in _TEST_HOSTS and bool(name) and ("test" in name or "ci" in name.split("_"))


def use_postgres() -> bool:
    return _resolve_database_url() is not None


@contextmanager
def get_connection() -> Iterator[object]:
    url = _resolve_database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 is required when DATABASE_URL is set") from exc
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
