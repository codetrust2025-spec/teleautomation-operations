"""Test runs can never reach the production database.

26 Nov 2026: sixteen fixture rows ("Lost Person", "Round Person", "Plain", "Fine",
placeholder phones 9000000xxx, reference "Test Owner") were found in the
production candidates_store and removed (backup on KVM1 under
/var/backups/teleautomation-test-record-cleanup/20261126). Tests point the
candidate store at a temporary JSON file, but with DATABASE_URL set every write
went to Postgres instead. Under pytest, only a disposable local database is used.
"""
import pytest

from core.db import connection


@pytest.fixture()
def fresh(monkeypatch):
    monkeypatch.setattr(connection, "_url_checked", False)
    monkeypatch.setattr(connection, "_database_url", None)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    return monkeypatch


@pytest.mark.parametrize("url", [
    "postgresql://user:secret@operations-db:5432/teleautomation_operations",
    "postgresql://user:secret@187.127.164.90:5432/teleautomation_operations",
    "postgresql://user:secret@localhost:5432/teleautomation_operations",
    "postgresql://user:secret@db.example.com:5432/business_ci",
])
def test_a_test_run_refuses_any_database_that_is_not_a_disposable_local_one(fresh, url):
    fresh.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError, match="non-test database"):
        connection.use_postgres()
    with pytest.raises(RuntimeError, match="non-test database"):
        with connection.get_connection():
            pass
    assert connection._url_checked is False, "the refusal is never cached as an allowed URL"


@pytest.mark.parametrize("url", [
    "postgresql://business_ci:business_ci@localhost:5432/business_ci",
    "postgresql://u:p@127.0.0.1:5432/operations_test",
])
def test_the_ci_database_and_local_test_databases_stay_usable(fresh, url):
    fresh.setenv("DATABASE_URL", url)
    assert connection.use_postgres() is True


def test_without_a_database_url_tests_keep_using_json_files(fresh):
    fresh.delenv("DATABASE_URL", raising=False)
    assert connection.use_postgres() is False


def test_outside_a_test_run_production_is_untouched(fresh):
    fresh.setattr(connection, "_under_test", lambda: False)
    fresh.setenv("DATABASE_URL", "postgresql://user:secret@operations-db:5432/teleautomation_operations")
    assert connection.use_postgres() is True
