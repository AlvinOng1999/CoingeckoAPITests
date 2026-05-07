import os
import sys
import sqlite3
import pytest

# Point to project root so `import storage` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use an in-memory DB for tests — monkeypatch DB_PATH before importing storage
os.environ["BULK_TEST_DB"] = ":memory:"

import storage


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(storage, "DB_PATH", db_file)
    storage.init_db()
    storage.init_bulk_db()
    yield
    # cleanup handled by tmp_path


def test_create_bulk_run_returns_id():
    run_id = storage.create_bulk_run("http", target_count=100, run_forever=False, verify_email=True)
    assert isinstance(run_id, int)
    assert run_id > 0


def test_create_bulk_run_forever_has_null_target():
    run_id = storage.create_bulk_run("browser", target_count=None, run_forever=True, verify_email=False)
    run = storage.get_bulk_run(run_id)
    assert run["run_forever"] == 1
    assert run["target_count"] is None


def test_save_and_get_bulk_account():
    run_id = storage.create_bulk_run("http", 10, False, True)
    storage.save_bulk_account(run_id, "a@b.com", "Pw1!xxxx", "verified")
    accounts = storage.get_bulk_accounts(run_id=run_id)
    assert len(accounts) == 1
    assert accounts[0]["email"] == "a@b.com"
    assert accounts[0]["status"] == "verified"
    assert accounts[0]["password"] == "Pw1!xxxx"


def test_save_bulk_account_failed_stores_error():
    run_id = storage.create_bulk_run("http", 10, False, False)
    storage.save_bulk_account(run_id, "x@y.com", "", "failed", error="429 Too Many Requests")
    accounts = storage.get_bulk_accounts(run_id=run_id)
    assert accounts[0]["error"] == "429 Too Many Requests"


def test_increment_bulk_run_counts():
    run_id = storage.create_bulk_run("browser", 5, False, True)
    storage.increment_bulk_run_counts(run_id, created=3, failed=1)
    run = storage.get_bulk_run(run_id)
    assert run["total_created"] == 3
    assert run["total_failed"] == 1


def test_update_bulk_run_status():
    run_id = storage.create_bulk_run("http", 10, False, True)
    storage.update_bulk_run_status(run_id, "done")
    run = storage.get_bulk_run(run_id)
    assert run["status"] == "done"


def test_get_bulk_accounts_filter_by_status():
    run_id = storage.create_bulk_run("http", 10, False, True)
    storage.save_bulk_account(run_id, "a@a.com", "pw", "verified")
    storage.save_bulk_account(run_id, "b@b.com", "pw", "failed")
    verified = storage.get_bulk_accounts(run_id=run_id, status="verified")
    assert len(verified) == 1
    assert verified[0]["email"] == "a@a.com"
