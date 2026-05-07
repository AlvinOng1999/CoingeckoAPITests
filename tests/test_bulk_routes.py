import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dashboard"))

import storage


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(storage, "DB_PATH", db_file)
    storage.init_db()
    storage.init_bulk_db()
    yield


@pytest.fixture
def client(fresh_db):
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_bulk_register_page_returns_200(client):
    resp = client.get("/bulk-register")
    assert resp.status_code == 200
    assert b"Bulk" in resp.data


def test_bulk_start_creates_run(client):
    resp = client.post("/api/bulk-start", json={
        "mode": "http",
        "count": 10,
        "run_forever": False,
        "verify_email": False,
        "max_workers": 2,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "run_id" in data
    assert data["run_id"] > 0


def test_bulk_stop_returns_ok(client):
    start = client.post("/api/bulk-start", json={
        "mode": "http", "count": 10, "run_forever": False,
        "verify_email": False, "max_workers": 2,
    })
    run_id = start.get_json()["run_id"]
    resp = client.post("/api/bulk-stop", json={"run_id": run_id})
    assert resp.status_code == 200


def test_bulk_accounts_returns_list(client):
    resp = client.get("/api/bulk-accounts")
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)


def test_bulk_accounts_filter_by_run_id(client):
    run_id = storage.create_bulk_run("http", 5, False, True)
    storage.save_bulk_account(run_id, "a@b.com", "pw", "verified")
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        resp = c.get(f"/api/bulk-accounts?run_id={run_id}")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["email"] == "a@b.com"
