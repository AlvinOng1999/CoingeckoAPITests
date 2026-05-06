# Bulk Account Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/bulk-register` page to the CoinGecko QA dashboard that creates hundreds of CoinGecko accounts in parallel (Mode B: HTTP requests; Mode C: browser pool), streams live progress via SSE, saves results to a dedicated SQLite table, and exports to Excel.

**Architecture:** Two `ThreadPoolExecutor` worker pools run inside Flask as background threads, emitting JSON events via SSE (same pattern as the existing `/api/stress-test`). A new `bulk_register.py` file holds all worker logic. Two new SQLite tables (`bulk_runs`, `bulk_accounts`) are added to `storage.py`. The UI is a new Bootstrap 5 template at `/bulk-register` with tabs for Mode B and Mode C.

**Tech Stack:** Python 3, Flask, SQLite, `concurrent.futures.ThreadPoolExecutor`, `threading.Event`, `openpyxl`, `requests`, Camoufox, `captcha_solver`, `temp_email`, Bootstrap 5

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Modify | `requirements.txt` | Add `openpyxl`, `pytest` |
| Modify | `storage.py` | Add `bulk_runs`/`bulk_accounts` tables + CRUD |
| Create | `tests/test_bulk_storage.py` | Unit tests for new storage functions |
| Create | `dashboard/bulk_register.py` | Worker logic, stop mechanism, SSE generators |
| Create | `tests/test_bulk_routes.py` | Flask route tests |
| Modify | `dashboard/app.py` | Add 6 new routes |
| Create | `scripts/discover_cg_registration.py` | One-time script to capture CoinGecko HTTP endpoints |
| Create | `dashboard/templates/bulk_register.html` | Full UI |
| Modify | `dashboard/templates/index.html` | Add Bulk Register nav link |
| Modify | `dashboard/templates/search.html` | Add Bulk Register nav link |
| Modify | `dashboard/templates/backtest.html` | Add Bulk Register nav link |

---

### Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add openpyxl and pytest to requirements.txt**

Open `requirements.txt` and add two lines so the full file reads:

```
playwright==1.44.0
requests==2.32.3
Flask==3.0.3
rich==13.7.1
camoufox[geoip]>=0.4.0
capsolver>=1.0.0
2captcha-python>=1.3.0
openpyxl>=3.1.0
pytest>=8.0.0
```

- [ ] **Step 2: Install new packages**

```bash
pip install openpyxl pytest
```

Expected: both install without error.

- [ ] **Step 3: Verify import**

```bash
python -c "import openpyxl; import pytest; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add openpyxl and pytest dependencies"
```

---

### Task 2: DB Schema for Bulk Tables

**Files:**
- Modify: `storage.py` (add after the existing `init_db()` block at the bottom)

- [ ] **Step 1: Add `init_bulk_db()` and `_migrate_bulk()` to storage.py**

Add these functions at the end of `storage.py`, before the final `init_db()` / `_migrate()` calls:

```python
def init_bulk_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bulk_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                mode          TEXT,
                target_count  INTEGER,
                run_forever   INTEGER DEFAULT 0,
                verify_email  INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'running',
                started_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                total_created INTEGER DEFAULT 0,
                total_failed  INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS bulk_accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER REFERENCES bulk_runs(id),
                email      TEXT,
                password   TEXT,
                verified   INTEGER DEFAULT 0,
                status     TEXT DEFAULT 'pending',
                error      TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.commit()
```

- [ ] **Step 2: Call `init_bulk_db()` at module load**

The bottom of `storage.py` currently reads:

```python
init_db()
_migrate()
```

Change it to:

```python
init_db()
_migrate()
init_bulk_db()
```

- [ ] **Step 3: Verify tables are created**

```bash
python -c "import storage; import sqlite3; con = sqlite3.connect('accounts.db'); print([r[0] for r in con.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"
```

Expected output includes: `['accounts', 'bulk_runs', 'bulk_accounts']`

- [ ] **Step 4: Commit**

```bash
git add storage.py
git commit -m "feat: add bulk_runs and bulk_accounts DB tables"
```

---

### Task 3: Storage CRUD Functions + Tests

**Files:**
- Modify: `storage.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_bulk_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/__init__.py` (empty file), then create `tests/test_bulk_storage.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_bulk_storage.py -v
```

Expected: `AttributeError: module 'storage' has no attribute 'create_bulk_run'` (or similar — functions don't exist yet).

- [ ] **Step 3: Add CRUD functions to storage.py**

Add after `init_bulk_db()`:

```python
def create_bulk_run(mode: str, target_count, run_forever: bool, verify_email: bool) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO bulk_runs (mode, target_count, run_forever, verify_email, status) VALUES (?,?,?,?,'running')",
            (mode, target_count, int(run_forever), int(verify_email)),
        )
        con.commit()
        return cur.lastrowid


def get_bulk_run(run_id: int) -> dict | None:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM bulk_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def update_bulk_run_status(run_id: int, status: str):
    with _conn() as con:
        con.execute("UPDATE bulk_runs SET status=? WHERE id=?", (status, run_id))
        con.commit()


def increment_bulk_run_counts(run_id: int, created: int = 0, failed: int = 0):
    with _conn() as con:
        con.execute(
            "UPDATE bulk_runs SET total_created=total_created+?, total_failed=total_failed+? WHERE id=?",
            (created, failed, run_id),
        )
        con.commit()


def save_bulk_account(run_id: int, email: str, password: str, status: str, error: str = None):
    verified = 1 if status == "verified" else 0
    with _conn() as con:
        con.execute(
            "INSERT INTO bulk_accounts (run_id, email, password, verified, status, error) VALUES (?,?,?,?,?,?)",
            (run_id, email, password, verified, status, error),
        )
        con.commit()


def get_bulk_accounts(run_id: int = None, status: str = None, mode: str = None) -> list[dict]:
    query = """
        SELECT ba.*, br.mode
        FROM bulk_accounts ba
        JOIN bulk_runs br ON ba.run_id = br.id
        WHERE 1=1
    """
    params = []
    if run_id is not None:
        query += " AND ba.run_id=?"
        params.append(run_id)
    if status:
        query += " AND ba.status=?"
        params.append(status)
    if mode:
        query += " AND br.mode=?"
        params.append(mode)
    query += " ORDER BY ba.id DESC"
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(query, params).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests — all should pass**

```bash
pytest tests/test_bulk_storage.py -v
```

Expected:
```
tests/test_bulk_storage.py::test_create_bulk_run_returns_id PASSED
tests/test_bulk_storage.py::test_create_bulk_run_forever_has_null_target PASSED
tests/test_bulk_storage.py::test_save_and_get_bulk_account PASSED
tests/test_bulk_storage.py::test_save_bulk_account_failed_stores_error PASSED
tests/test_bulk_storage.py::test_increment_bulk_run_counts PASSED
tests/test_bulk_storage.py::test_update_bulk_run_status PASSED
tests/test_bulk_storage.py::test_get_bulk_accounts_filter_by_status PASSED
7 passed
```

- [ ] **Step 5: Commit**

```bash
git add storage.py tests/__init__.py tests/test_bulk_storage.py
git commit -m "feat: add bulk_runs/bulk_accounts CRUD to storage.py"
```

---

### Task 4: bulk_register.py Skeleton

**Files:**
- Create: `dashboard/bulk_register.py`

- [ ] **Step 1: Create dashboard/bulk_register.py with stop mechanism and helpers**

```python
"""
Bulk account registration workers for Mode B (HTTP) and Mode C (Browser).
Both modes expose a generator that yields SSE-ready JSON strings.
"""
import os
import sys
import json
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import storage

_STOP_EVENTS: dict[int, threading.Event] = {}

HOMEPAGE = "https://www.coingecko.com/"
SIGNUP_URL = "https://www.coingecko.com/en/users/sign_up"


def start_run(mode: str, target_count, run_forever: bool, verify_email: bool) -> int:
    run_id = storage.create_bulk_run(mode, target_count, run_forever, verify_email)
    _STOP_EVENTS[run_id] = threading.Event()
    return run_id


def stop_run(run_id: int):
    if run_id in _STOP_EVENTS:
        _STOP_EVENTS[run_id].set()
    storage.update_bulk_run_status(run_id, "stopped")


def _log_dir() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(path, exist_ok=True)
    return path


def _append_log(run_id: int, email: str, status: str, error: str = ""):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {email} — {status}"
    if error:
        line += f": {error}"
    log_path = os.path.join(_log_dir(), f"bulk_run_{run_id}.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _make_event(run_id, done, total, email, status, errors, rate_limited=0, complete=False) -> str:
    return _sse({
        "run_id": run_id,
        "done": done,
        "total": total,
        "email": email,
        "status": status,
        "errors": errors,
        "rate_limited": rate_limited,
        "complete": complete,
    })
```

- [ ] **Step 2: Verify the file imports cleanly**

```bash
python -c "import sys; sys.path.insert(0,'dashboard'); import bulk_register; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add dashboard/bulk_register.py
git commit -m "feat: add bulk_register.py skeleton with stop mechanism and SSE helpers"
```

---

### Task 5: Mode C Worker (Browser Pool)

**Files:**
- Modify: `dashboard/bulk_register.py`

- [ ] **Step 1: Add Mode C worker and generator to bulk_register.py**

Append to `dashboard/bulk_register.py`:

```python
# ── Mode C: Browser Pool ──────────────────────────────────────────────────────

def _mode_c_worker(run_id: int, verify_email: bool, stop_event: threading.Event):
    """
    Creates one CoinGecko account using a real Camoufox browser.
    Returns (email, password, status, error_str).
    """
    from camoufox.sync_api import Camoufox
    import temp_email
    import coingecko

    if stop_event.is_set():
        return "", "", "stopped", ""

    email = ""
    password = ""
    try:
        mailbox = temp_email.create_mailbox()
        email = mailbox["address"]
        password = mailbox["cg_password"]

        with Camoufox(headless=True, geoip=True) as browser:
            page = browser.new_page()
            coingecko.register(page, email, password)

            if verify_email and not stop_event.is_set():
                body = temp_email.poll_inbox(mailbox["token"], timeout=120)
                link = temp_email.extract_verification_link(body)
                coingecko.confirm_email(page, link, password)
                status = "verified"
            else:
                status = "unverified"

        storage.save_bulk_account(run_id, email, password, status)
        _append_log(run_id, email, status)
        return email, password, status, ""

    except Exception as exc:
        err = str(exc)
        if email:
            storage.save_bulk_account(run_id, email, password, "failed", err)
            _append_log(run_id, email, "failed", err)
        return email, password, "failed", err


def run_mode_c(run_id: int, target_count, run_forever: bool,
               verify_email: bool, max_workers: int = 5):
    """
    SSE generator for Mode C. Yields SSE-formatted JSON strings.
    Runs until target_count is reached or stop_run() is called.
    If run_forever=True, loops indefinitely until stopped.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stop_event = _STOP_EVENTS.get(run_id, threading.Event())
    done = 0
    errors = 0
    batch = 0

    while True:
        batch += 1
        count = target_count if not run_forever else 50  # 50 per loop iteration

        futures_map = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for _ in range(count):
                if stop_event.is_set():
                    break
                f = pool.submit(_mode_c_worker, run_id, verify_email, stop_event)
                futures_map[f] = True

            for future in as_completed(futures_map):
                if stop_event.is_set():
                    break
                email, _pw, status, err = future.result()
                if status in ("verified", "unverified"):
                    done += 1
                    storage.increment_bulk_run_counts(run_id, created=1)
                else:
                    errors += 1
                    storage.increment_bulk_run_counts(run_id, failed=1)

                total_display = None if run_forever else target_count
                yield _make_event(run_id, done, total_display, email, status, errors)

        if stop_event.is_set() or not run_forever:
            break

    storage.update_bulk_run_status(run_id, "done" if not stop_event.is_set() else "stopped")
    yield _make_event(run_id, done, target_count, "", "complete", errors, complete=True)
```

- [ ] **Step 2: Verify file still imports cleanly**

```bash
python -c "import sys; sys.path.insert(0,'dashboard'); import bulk_register; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add dashboard/bulk_register.py
git commit -m "feat: add Mode C browser pool worker to bulk_register.py"
```

---

### Task 6: Discover Mode B HTTP Endpoints

**Files:**
- Create: `scripts/discover_cg_registration.py`

This is a one-time script. Run it once to capture exactly what HTTP requests CoinGecko's registration modal makes, so Task 7 can replicate them without a browser.

- [ ] **Step 1: Create scripts/discover_cg_registration.py**

```python
"""
Run once to discover CoinGecko's registration HTTP endpoints.
Output: prints POST request details to stdout.

Usage:
    python scripts/discover_cg_registration.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import temp_email
import coingecko
from camoufox.sync_api import Camoufox

captured = []

mailbox = temp_email.create_mailbox()
email = mailbox["address"]
password = mailbox["cg_password"]
print(f"Registering with: {email}")

with Camoufox(headless=False, geoip=True) as browser:
    page = browser.new_page()

    def on_request(request):
        if "coingecko.com" in request.url and request.method in ("POST", "PUT", "PATCH"):
            captured.append({
                "url": request.url,
                "method": request.method,
                "content_type": request.headers.get("content-type", ""),
                "post_data": request.post_data,
            })

    page.on("request", on_request)
    coingecko.register(page, email, password)

print("\n=== Captured Registration Requests ===")
for i, r in enumerate(captured, 1):
    print(f"\n[{i}] {r['method']} {r['url']}")
    print(f"    Content-Type: {r['content_type']}")
    if r["post_data"]:
        print(f"    Body: {r['post_data'][:500]}")

with open("registration_requests.json", "w") as f:
    json.dump(captured, f, indent=2)
print("\nFull details saved to registration_requests.json")
```

- [ ] **Step 2: Run the discovery script**

```bash
python scripts/discover_cg_registration.py
```

Expected: browser opens, completes registration, then prints captured POST requests. A `registration_requests.json` file is created.

- [ ] **Step 3: Note the discovered endpoints**

Open `registration_requests.json` and note:
- The URL of the email submission POST (e.g. `https://www.coingecko.com/en/users`)
- The field names used (e.g. `user[email]`, `authenticity_token`, `cf-turnstile-response`)
- Whether it's form-encoded or JSON
- The URL and fields for the password submission step

These values are used in Task 7 to implement the HTTP worker. Update the constants at the top of `dashboard/bulk_register.py` (added in Task 7) based on what you see here.

- [ ] **Step 4: Commit the discovery script**

```bash
git add scripts/discover_cg_registration.py
git commit -m "chore: add script to discover CoinGecko registration HTTP endpoints"
```

---

### Task 7: Mode B Worker (HTTP Blast)

**Files:**
- Modify: `dashboard/bulk_register.py`

Update the URL constants below based on what `registration_requests.json` from Task 6 revealed. The structure below follows Rails/Devise conventions — adjust field names if CoinGecko's response shows different keys.

- [ ] **Step 1: Add Mode B constants and CSRF helper to bulk_register.py**

Add after the `SIGNUP_URL` constant at the top of `bulk_register.py`:

```python
import re
import requests as _req

# ── Mode B constants (update based on scripts/discover_cg_registration.py output) ──
_CG_EMAIL_POST_URL    = "https://www.coingecko.com/en/users"
_CG_PASSWORD_POST_URL = "https://www.coingecko.com/en/users"
_EMAIL_FIELD          = "user[email]"
_PASSWORD_FIELD       = "user[password]"
_PASSWORD_CONF_FIELD  = "user[password_confirmation]"
_TURNSTILE_FIELD      = "cf-turnstile-response"
_HCAPTCHA_FIELD       = "response_token"
_CSRF_META_RE         = re.compile(r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)', re.I)


def _extract_csrf(html: str) -> str:
    m = _CSRF_META_RE.search(html)
    return m.group(1) if m else ""
```

- [ ] **Step 2: Add Mode B worker and generator to bulk_register.py**

Append to `dashboard/bulk_register.py`:

```python
# ── Mode B: HTTP Blast ────────────────────────────────────────────────────────

def _mode_b_worker(run_id: int, verify_email: bool, stop_event: threading.Event):
    """
    Creates one CoinGecko account using direct HTTP requests (no browser).
    Returns (email, password, status, error_str).
    Requires CAPTCHA_API_KEY env var to be set for captcha solving.
    """
    import temp_email
    import captcha_solver

    if stop_event.is_set():
        return "", "", "stopped", ""

    email = ""
    password = ""
    try:
        mailbox = temp_email.create_mailbox()
        email = mailbox["address"]
        password = mailbox["cg_password"]

        session = _req.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        # Step 1: GET signup page → extract CSRF token + set cookies
        resp = session.get(SIGNUP_URL, timeout=15)
        resp.raise_for_status()
        csrf = _extract_csrf(resp.text)

        # Step 2: Solve Cloudflare Turnstile
        ts_token = captcha_solver.solve_turnstile(HOMEPAGE)
        if not ts_token:
            raise RuntimeError("Turnstile solve returned None — check CAPTCHA_API_KEY")

        # Step 3: POST email step
        resp = session.post(
            _CG_EMAIL_POST_URL,
            data={
                "authenticity_token": csrf,
                _EMAIL_FIELD: email,
                _TURNSTILE_FIELD: ts_token,
            },
            headers={
                "X-CSRF-Token": csrf,
                "Referer": SIGNUP_URL,
                "Origin": "https://www.coingecko.com",
            },
            timeout=15,
            allow_redirects=True,
        )
        if resp.status_code == 429:
            raise RuntimeError("Rate limited by CoinGecko (429)")
        if resp.status_code not in (200, 201, 302, 422):
            raise RuntimeError(f"Email POST returned {resp.status_code}")

        # Refresh CSRF from response page (Rails rotates it after each request)
        new_csrf = _extract_csrf(resp.text)
        if new_csrf:
            csrf = new_csrf

        # Step 4: Solve hCaptcha
        hc_token = captcha_solver.solve_hcaptcha(HOMEPAGE)
        if not hc_token:
            raise RuntimeError("hCaptcha solve returned None — check CAPTCHA_API_KEY")

        # Step 5: POST password step
        resp = session.post(
            _CG_PASSWORD_POST_URL,
            data={
                "authenticity_token": csrf,
                _PASSWORD_FIELD: password,
                _PASSWORD_CONF_FIELD: password,
                _HCAPTCHA_FIELD: hc_token,
            },
            headers={
                "X-CSRF-Token": csrf,
                "Referer": SIGNUP_URL,
                "Origin": "https://www.coingecko.com",
            },
            timeout=15,
            allow_redirects=True,
        )
        if resp.status_code == 429:
            raise RuntimeError("Rate limited by CoinGecko (429)")

        # Step 6: Optional email verification
        if verify_email and not stop_event.is_set():
            body = temp_email.poll_inbox(mailbox["token"], timeout=120)
            link = temp_email.extract_verification_link(body)
            session.get(link, timeout=15)
            status = "verified"
        else:
            status = "unverified"

        storage.save_bulk_account(run_id, email, password, status)
        _append_log(run_id, email, status)
        return email, password, status, ""

    except Exception as exc:
        err = str(exc)
        if email:
            storage.save_bulk_account(run_id, email, password, "failed", err)
            _append_log(run_id, email, "failed", err)
        return email, password, "failed", err


def run_mode_b(run_id: int, target_count, run_forever: bool,
               verify_email: bool, max_workers: int = 50):
    """
    SSE generator for Mode B. Yields SSE-formatted JSON strings.
    Runs until target_count reached or stop_run() called.
    If run_forever=True, loops in batches of 200 until stopped.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stop_event = _STOP_EVENTS.get(run_id, threading.Event())
    done = 0
    errors = 0
    rate_limited = 0

    while True:
        count = target_count if not run_forever else 200

        futures_map = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for _ in range(count):
                if stop_event.is_set():
                    break
                f = pool.submit(_mode_b_worker, run_id, verify_email, stop_event)
                futures_map[f] = True

            for future in as_completed(futures_map):
                if stop_event.is_set():
                    break
                email, _pw, status, err = future.result()
                if "rate" in err.lower() or "429" in err:
                    rate_limited += 1
                if status in ("verified", "unverified"):
                    done += 1
                    storage.increment_bulk_run_counts(run_id, created=1)
                else:
                    errors += 1
                    storage.increment_bulk_run_counts(run_id, failed=1)

                total_display = None if run_forever else target_count
                yield _make_event(run_id, done, total_display, email, status,
                                  errors, rate_limited)

        if stop_event.is_set() or not run_forever:
            break

    storage.update_bulk_run_status(run_id, "done" if not stop_event.is_set() else "stopped")
    yield _make_event(run_id, done, target_count, "", "complete", errors,
                      rate_limited, complete=True)
```

- [ ] **Step 3: Verify the file imports cleanly**

```bash
python -c "import sys; sys.path.insert(0,'dashboard'); import bulk_register; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add dashboard/bulk_register.py
git commit -m "feat: add Mode B HTTP blast worker to bulk_register.py"
```

---

### Task 8: Flask Routes

**Files:**
- Modify: `dashboard/app.py`
- Create: `tests/test_bulk_routes.py`

- [ ] **Step 1: Write failing route tests**

Create `tests/test_bulk_routes.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd dashboard && pytest ../tests/test_bulk_routes.py -v 2>&1 | head -30
```

Expected: ImportError or 404 — routes not defined yet.

- [ ] **Step 3: Add new routes to dashboard/app.py**

Add the following imports at the top of `dashboard/app.py` (after the existing imports):

```python
import threading
```

Add these imports where the existing `sys.path.insert` is:

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bulk_register  # noqa: E402 — must be after sys.path insert
```

Add all new routes at the end of `dashboard/app.py`, before `if __name__ == "__main__":`:

```python
# ── Bulk Register ─────────────────────────────────────────────────────────────

@app.route("/bulk-register")
def bulk_register_page():
    return render_template("bulk_register.html")


@app.route("/api/bulk-start", methods=["POST"])
def api_bulk_start():
    body = request.get_json(force=True)
    mode        = body.get("mode", "http")
    count       = body.get("count", 100)
    run_forever = bool(body.get("run_forever", False))
    verify      = bool(body.get("verify_email", True))
    max_workers = int(body.get("max_workers", 50 if mode == "http" else 5))
    max_workers = min(max_workers, 200 if mode == "http" else 20)

    run_id = bulk_register.start_run(mode, None if run_forever else count, run_forever, verify)

    def _run():
        gen = (bulk_register.run_mode_b if mode == "http" else bulk_register.run_mode_c)(
            run_id, count, run_forever, verify, max_workers
        )
        # Consume generator in background thread (SSE stream handled separately)
        for _ in gen:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"run_id": run_id, "mode": mode})


@app.route("/api/bulk-stop", methods=["POST"])
def api_bulk_stop():
    body = request.get_json(force=True)
    run_id = int(body.get("run_id", 0))
    bulk_register.stop_run(run_id)
    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/bulk-stream/<int:run_id>")
def api_bulk_stream(run_id):
    run = storage.get_bulk_run(run_id)
    if not run:
        def _err():
            yield f"data: {json.dumps({'error': 'run not found'})}\n\n"
        return Response(_err(), mimetype="text/event-stream")

    mode        = run["mode"]
    run_forever = bool(run["run_forever"])
    verify      = bool(run["verify_email"])
    count       = run["target_count"]

    stop_event = bulk_register._STOP_EVENTS.get(run_id, threading.Event())
    bulk_register._STOP_EVENTS[run_id] = stop_event

    def generate():
        gen = (bulk_register.run_mode_b if mode == "http" else bulk_register.run_mode_c)(
            run_id, count, run_forever, verify
        )
        for event in gen:
            yield event

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/bulk-accounts")
def api_bulk_accounts():
    run_id = request.args.get("run_id", type=int)
    status = request.args.get("status")
    mode   = request.args.get("mode")
    return jsonify(storage.get_bulk_accounts(run_id=run_id, status=status, mode=mode))
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd dashboard && pytest ../tests/test_bulk_routes.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py tests/test_bulk_routes.py
git commit -m "feat: add bulk register Flask routes to app.py"
```

---

### Task 9: Excel Export Route

**Files:**
- Modify: `dashboard/app.py`

- [ ] **Step 1: Add the /api/bulk-export route to app.py**

Add after the `/api/bulk-accounts` route:

```python
@app.route("/api/bulk-export")
def api_bulk_export():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from flask import send_file
    import io

    run_id = request.args.get("run_id", type=int)
    accounts = storage.get_bulk_accounts(run_id=run_id)

    wb = openpyxl.Workbook()

    # ── Sheet 1: Accounts ──────────────────────────────────────────────────────
    ws1 = wb.active
    ws1.title = "Accounts"
    headers = ["#", "Email", "Password", "Status", "Verified", "Mode", "Run ID", "Created At"]
    ws1.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="000000")
    for col, _ in enumerate(headers, 1):
        cell = ws1.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for i, acc in enumerate(accounts, 1):
        ws1.append([
            i,
            acc["email"],
            acc["password"],
            acc["status"],
            "Yes" if acc["verified"] else "No",
            acc.get("mode", ""),
            acc["run_id"],
            acc["created_at"],
        ])

    for col in ws1.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        ws1.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)

    # ── Sheet 2: Run Log ────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Run Log")
    ws2.append(["Timestamp", "Email", "Status", "Error"])
    for col in range(1, 5):
        cell = ws2.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill

    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    if run_id:
        log_files = [os.path.join(log_dir, f"bulk_run_{run_id}.txt")]
    else:
        import glob as _glob
        log_files = sorted(_glob.glob(os.path.join(log_dir, "bulk_run_*.txt")))

    log_re = re.compile(r"\[(.+?)\] (.+?) — (.+?)(?:: (.+))?$")
    for lf in log_files:
        if not os.path.exists(lf):
            continue
        with open(lf, encoding="utf-8") as f:
            for line in f:
                m = log_re.match(line.strip())
                if m:
                    ws2.append([m.group(1), m.group(2), m.group(3), m.group(4) or ""])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"bulk_accounts_run{run_id}.xlsx" if run_id else "bulk_accounts_all.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
```

Also add `import re` at the top of `app.py` if not already present (check the existing imports — `re` is used in the export route for log parsing).

- [ ] **Step 2: Manual test — download an export**

With `dashboard/app.py` running (`python dashboard/app.py`), open:

```
http://localhost:5000/api/bulk-export
```

Expected: browser downloads an `.xlsx` file. Open it — it should have two sheets ("Accounts" and "Run Log"), even if both are empty except headers.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: add Excel export route /api/bulk-export with two-sheet workbook"
```

---

### Task 10: Bulk Register UI

**Files:**
- Create: `dashboard/templates/bulk_register.html`

- [ ] **Step 1: Create the full bulk_register.html template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bulk Register — CoinGecko QA</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { background: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; font-size: 14px; color: #0a0a0a; }
    .navbar { background: #000 !important; padding: 14px 28px; border-bottom: 1px solid #000; }
    .navbar-brand { color: #fff !important; font-size: 1rem; letter-spacing: .01em; }
    .navbar .nav-pill { color: #777; font-size: .85rem; text-decoration: none; padding: 5px 12px; border-radius: 6px; transition: color .15s, background .15s; }
    .navbar .nav-pill:hover { color: #fff; background: #222; }
    .navbar .nav-pill.active { color: #fff; }
    .page-wrap { max-width: 1100px; margin: 0 auto; padding: 28px 20px; }
    .section-title { font-size: 1.15rem; font-weight: 700; margin-bottom: 4px; }
    .section-sub { font-size: .82rem; color: #777; margin-bottom: 20px; }

    /* Tabs */
    .mode-tabs { display: flex; border-bottom: 2px solid #e0e0e0; margin-bottom: 24px; }
    .mode-tab { padding: 9px 22px; font-size: .88rem; font-weight: 600; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; color: #888; user-select: none; }
    .mode-tab.active { color: #000; border-bottom-color: #000; }
    .badge-mode { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: .72rem; font-weight: 700; }
    .badge-http { background: #e8f4fd; color: #0d6efd; }
    .badge-browser { background: #fff3cd; color: #856404; }

    /* Control panel */
    .ctrl-panel { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 20px 22px; margin-bottom: 20px; }
    .ctrl-row { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
    .ctrl-group { display: flex; flex-direction: column; gap: 5px; }
    .ctrl-group label { font-size: .75rem; font-weight: 600; color: #555; text-transform: uppercase; letter-spacing: .05em; }
    .ctrl-group input[type=number] { width: 110px; padding: 7px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: .9rem; }
    .ctrl-group input[type=number]:disabled { background: #f5f5f5; color: #aaa; }
    .toggle-row { display: flex; align-items: center; gap: 10px; font-size: .88rem; color: #333; }
    .toggle { position: relative; width: 40px; height: 22px; flex-shrink: 0; }
    .toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
    .tslider { position: absolute; inset: 0; background: #ccc; border-radius: 22px; cursor: pointer; transition: .2s; }
    .tslider:before { content: ""; position: absolute; height: 16px; width: 16px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: .2s; }
    .toggle input:checked + .tslider { background: #000; }
    .toggle input:checked + .tslider:before { transform: translateX(18px); }
    .ctrl-hint { margin-top: 10px; font-size: .78rem; color: #999; }
    .btn-start { background: #000; color: #fff; border: none; padding: 8px 24px; border-radius: 6px; font-size: .88rem; font-weight: 600; cursor: pointer; }
    .btn-start:hover { background: #222; }
    .btn-stop { background: #dc3545; color: #fff; border: none; padding: 8px 24px; border-radius: 6px; font-size: .88rem; font-weight: 600; cursor: pointer; }
    .btn-stop:disabled { opacity: .4; cursor: not-allowed; }

    /* Progress */
    .prog-panel { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 18px 20px; margin-bottom: 20px; display: none; }
    .prog-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
    .prog-label { font-size: .82rem; font-weight: 600; color: #444; }
    .prog-stats { font-size: .82rem; color: #888; }
    .prog-bar-wrap { background: #f0f0f0; border-radius: 6px; height: 8px; margin-bottom: 14px; }
    .prog-bar-fill { background: #000; height: 8px; border-radius: 6px; width: 0%; transition: width .3s; }
    .log-feed { background: #1a1a1a; border-radius: 8px; padding: 12px 14px; font-family: monospace; font-size: .78rem; color: #aaa; height: 150px; overflow-y: auto; }
    .log-ok { color: #4ade80; }
    .log-err { color: #f87171; }
    .log-info { color: #60a5fa; }

    /* Table panel */
    .tbl-panel { background: #fff; border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden; }
    .tbl-header { padding: 14px 20px; border-bottom: 1px solid #e8e8e8; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
    .tbl-title { font-size: .9rem; font-weight: 700; }
    .tbl-controls { display: flex; gap: 8px; align-items: center; }
    select.tbl-filter { font-size: .78rem; padding: 4px 8px; border: 1px solid #ccc; border-radius: 5px; }
    .btn-export { background: #fff; border: 1px solid #ccc; padding: 5px 14px; border-radius: 6px; font-size: .78rem; font-weight: 600; cursor: pointer; color: #333; }
    .btn-export:hover { border-color: #000; color: #000; }
    table.accts { width: 100%; border-collapse: collapse; font-size: .82rem; }
    table.accts th { padding: 9px 14px; text-align: left; font-size: .72rem; text-transform: uppercase; letter-spacing: .06em; color: #888; border-bottom: 1px solid #e8e8e8; }
    table.accts td { padding: 9px 14px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }
    table.accts tr:last-child td { border-bottom: none; }
    table.accts td.mono { font-family: monospace; }
    .s-verified { color: #16a34a; font-weight: 600; }
    .s-unverified { color: #dc2626; }
    .s-pending { color: #d97706; }
    .s-failed { color: #6b7280; }
    .tbl-empty { padding: 32px; text-align: center; color: #aaa; font-size: .85rem; }
  </style>
</head>
<body>

<nav class="navbar navbar-dark px-0" style="padding-left:28px!important;padding-right:28px!important">
  <span class="navbar-brand fw-bold">CoinGecko QA</span>
  <div class="ms-4 d-flex gap-1 align-items-center">
    <a href="/" class="nav-pill">Dashboard</a>
    <a href="/bulk-register" class="nav-pill active">Bulk Register</a>
    <a href="/search" class="nav-pill">Search</a>
    <a href="/backtest" class="nav-pill">Backtest</a>
  </div>
</nav>

<div class="page-wrap">
  <div class="section-title">Bulk Account Registration</div>
  <div class="section-sub">Create hundreds of CoinGecko accounts in parallel to test registration endpoint limits</div>

  <!-- Mode tabs -->
  <div class="mode-tabs">
    <div class="mode-tab active" onclick="switchMode('http')">
      Mode B — HTTP Blast &nbsp;<span class="badge-mode badge-http">lightweight</span>
    </div>
    <div class="mode-tab" onclick="switchMode('browser')">
      Mode C — Browser Pool &nbsp;<span class="badge-mode badge-browser">stealth</span>
    </div>
  </div>

  <!-- Control panel -->
  <div class="ctrl-panel">
    <div class="ctrl-row">
      <div class="ctrl-group">
        <label>Number of Accounts</label>
        <input type="number" id="inp-count" value="100" min="1" max="5000">
      </div>
      <div class="ctrl-group">
        <label>Concurrency</label>
        <input type="number" id="inp-workers" value="50" min="1" max="200">
      </div>
      <div class="ctrl-group" style="justify-content:flex-end;padding-bottom:2px">
        <div class="toggle-row">
          <label class="toggle">
            <input type="checkbox" id="tog-forever" onchange="onForeverToggle()">
            <span class="tslider"></span>
          </label>
          Run Forever (loop)
        </div>
      </div>
      <div class="ctrl-group" style="justify-content:flex-end;padding-bottom:2px">
        <div class="toggle-row">
          <label class="toggle">
            <input type="checkbox" id="tog-verify" checked>
            <span class="tslider"></span>
          </label>
          Include Email Verification
        </div>
      </div>
      <div class="ctrl-group" style="justify-content:flex-end;padding-bottom:2px">
        <div style="display:flex;gap:8px">
          <button class="btn-start" id="btn-start" onclick="startRun()">▶ Start</button>
          <button class="btn-stop" id="btn-stop" disabled onclick="stopRun()">■ Stop</button>
        </div>
      </div>
    </div>
    <div class="ctrl-hint" id="mode-hint">Mode B: direct HTTP requests — up to 200 concurrent workers, no browser required.</div>
  </div>

  <!-- Progress panel -->
  <div class="prog-panel" id="prog-panel">
    <div class="prog-header">
      <span class="prog-label" id="prog-label">Run in progress</span>
      <span class="prog-stats" id="prog-stats">0 created · 0 failed</span>
    </div>
    <div class="prog-bar-wrap" id="prog-bar-wrap">
      <div class="prog-bar-fill" id="prog-bar"></div>
    </div>
    <div class="log-feed" id="log-feed"></div>
  </div>

  <!-- Accounts table -->
  <div class="tbl-panel">
    <div class="tbl-header">
      <span class="tbl-title">Registered Accounts <span id="acct-count" style="font-weight:400;color:#999;font-size:.82rem;"></span></span>
      <div class="tbl-controls">
        <select class="tbl-filter" id="fil-mode" onchange="loadAccounts()">
          <option value="">All modes</option>
          <option value="http">HTTP Blast</option>
          <option value="browser">Browser Pool</option>
        </select>
        <select class="tbl-filter" id="fil-status" onchange="loadAccounts()">
          <option value="">All statuses</option>
          <option value="verified">Verified</option>
          <option value="unverified">Unverified</option>
          <option value="failed">Failed</option>
        </select>
        <button class="btn-export" onclick="exportXlsx()">⬇ Export Excel</button>
      </div>
    </div>
    <div id="tbl-body">
      <div class="tbl-empty">No accounts yet — start a run above.</div>
    </div>
  </div>
</div>

<script>
let currentMode = 'http';
let currentRunId = null;
let sseSource = null;
let pollInterval = null;

function switchMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-tab').forEach((t, i) => {
    t.classList.toggle('active', (i === 0 && mode === 'http') || (i === 1 && mode === 'browser'));
  });
  const maxWorkers = mode === 'http' ? 200 : 20;
  const defWorkers = mode === 'http' ? 50 : 5;
  const inp = document.getElementById('inp-workers');
  inp.max = maxWorkers;
  inp.value = defWorkers;
  document.getElementById('mode-hint').textContent = mode === 'http'
    ? 'Mode B: direct HTTP requests — up to 200 concurrent workers, no browser required.'
    : 'Mode C: real Camoufox browser per worker — stealth but heavier. Up to 20 concurrent browsers (~200–400 MB each).';
}

function onForeverToggle() {
  const forever = document.getElementById('tog-forever').checked;
  document.getElementById('inp-count').disabled = forever;
  document.getElementById('prog-bar-wrap').style.display = forever ? 'none' : 'block';
}

async function startRun() {
  const count = parseInt(document.getElementById('inp-count').value) || 100;
  const workers = parseInt(document.getElementById('inp-workers').value) || 50;
  const forever = document.getElementById('tog-forever').checked;
  const verify = document.getElementById('tog-verify').checked;

  const resp = await fetch('/api/bulk-start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mode: currentMode, count, run_forever: forever, verify_email: verify, max_workers: workers}),
  });
  const data = await resp.json();
  currentRunId = data.run_id;

  document.getElementById('btn-start').disabled = true;
  document.getElementById('btn-stop').disabled = false;
  document.getElementById('prog-panel').style.display = 'block';
  document.getElementById('prog-label').textContent = `Run #${currentRunId} in progress`;
  document.getElementById('log-feed').innerHTML = '';

  sseSource = new EventSource(`/api/bulk-stream/${currentRunId}`);
  sseSource.onmessage = (e) => {
    const d = JSON.parse(e.data);
    updateProgress(d);
    if (d.complete) {
      sseSource.close();
      onRunComplete();
    }
  };

  pollInterval = setInterval(loadAccounts, 3000);
}

function updateProgress(d) {
  const done = d.done || 0;
  const total = d.total;
  const errors = d.errors || 0;
  const forever = document.getElementById('tog-forever').checked;

  if (!forever && total) {
    document.getElementById('prog-bar').style.width = `${Math.min(100, (done / total) * 100)}%`;
  }
  document.getElementById('prog-stats').textContent =
    `${done} created · ${errors} failed` + (d.rate_limited ? ` · ${d.rate_limited} rate-limited` : '');

  if (d.email && d.status) {
    const feed = document.getElementById('log-feed');
    const line = document.createElement('div');
    const cls = d.status === 'verified' ? 'log-ok' : d.status === 'unverified' ? 'log-ok' : d.status === 'failed' ? 'log-err' : 'log-info';
    const label = d.status === 'verified' ? 'registered & verified' : d.status === 'unverified' ? 'registered (no verification)' : `failed`;
    line.className = cls;
    line.textContent = `[${d.status === 'failed' ? '✗' : '✓'}] ${d.email} — ${label}`;
    feed.appendChild(line);
    feed.scrollTop = feed.scrollHeight;
  }
}

function onRunComplete() {
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').disabled = true;
  document.getElementById('prog-label').textContent = `Run #${currentRunId} — complete`;
  clearInterval(pollInterval);
  loadAccounts();
}

async function stopRun() {
  if (!currentRunId) return;
  await fetch('/api/bulk-stop', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({run_id: currentRunId}),
  });
  if (sseSource) sseSource.close();
  clearInterval(pollInterval);
  document.getElementById('btn-start').disabled = false;
  document.getElementById('btn-stop').disabled = true;
  document.getElementById('prog-label').textContent = `Run #${currentRunId} — stopped`;
  loadAccounts();
}

function statusBadge(s) {
  if (s === 'verified')   return '<span class="s-verified">✓ Verified</span>';
  if (s === 'unverified') return '<span class="s-unverified">✗ Unverified</span>';
  if (s === 'pending')    return '<span class="s-pending">⏳ Pending</span>';
  return '<span class="s-failed">✗ Failed</span>';
}

function modeBadge(m) {
  return m === 'http'
    ? '<span class="badge-mode badge-http">HTTP</span>'
    : '<span class="badge-mode badge-browser">Browser</span>';
}

function relTime(ts) {
  if (!ts) return '';
  const diff = Math.floor((Date.now() - new Date(ts + 'Z').getTime()) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  return `${Math.floor(diff/3600)}h ago`;
}

async function loadAccounts() {
  const mode = document.getElementById('fil-mode').value;
  const status = document.getElementById('fil-status').value;
  let url = '/api/bulk-accounts?';
  if (mode) url += `mode=${mode}&`;
  if (status) url += `status=${status}&`;

  const resp = await fetch(url);
  const accounts = await resp.json();

  document.getElementById('acct-count').textContent = `(${accounts.length} total)`;

  if (!accounts.length) {
    document.getElementById('tbl-body').innerHTML = '<div class="tbl-empty">No accounts match the current filters.</div>';
    return;
  }

  const rows = accounts.map((a, i) => `
    <tr>
      <td style="color:#aaa">${accounts.length - i}</td>
      <td class="mono">${a.email}</td>
      <td class="mono">${a.password}</td>
      <td>${statusBadge(a.status)}</td>
      <td>${modeBadge(a.mode)}</td>
      <td style="color:#aaa">${relTime(a.created_at)}</td>
    </tr>
  `).join('');

  document.getElementById('tbl-body').innerHTML = `
    <table class="accts">
      <thead><tr>
        <th>#</th><th>Email</th><th>Password</th>
        <th>Verification</th><th>Mode</th><th>Created</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function exportXlsx() {
  const runId = currentRunId;
  const url = runId ? `/api/bulk-export?run_id=${runId}` : '/api/bulk-export';
  window.location.href = url;
}

// Initial load
loadAccounts();
</script>

</body>
</html>
```

- [ ] **Step 2: Verify the page renders**

Start the dashboard:
```bash
python dashboard/app.py
```

Open `http://localhost:5000/bulk-register` in the browser.

Expected: page loads with Mode B/Mode C tabs, control panel, empty accounts table.

- [ ] **Step 3: Commit**

```bash
git add dashboard/templates/bulk_register.html
git commit -m "feat: add bulk_register.html UI with tabs, live log feed, and accounts table"
```

---

### Task 11: Navbar Links + Final Smoke Test

**Files:**
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/templates/search.html`
- Modify: `dashboard/templates/backtest.html`

- [ ] **Step 1: Add Bulk Register nav link to index.html**

In `dashboard/templates/index.html`, find the existing navbar links section (around line 257):

```html
<a href="/search" class="nav-pill">Search</a>
<a href="/backtest" class="nav-pill">Backtest</a>
```

Replace with:

```html
<a href="/bulk-register" class="nav-pill">Bulk Register</a>
<a href="/search" class="nav-pill">Search</a>
<a href="/backtest" class="nav-pill">Backtest</a>
```

- [ ] **Step 2: Add Bulk Register nav link to search.html and backtest.html**

In `dashboard/templates/search.html`, find the navbar and add:
```html
<a href="/bulk-register" class="nav-pill">Bulk Register</a>
```
before the existing nav links.

Repeat for `dashboard/templates/backtest.html`.

- [ ] **Step 3: Run all tests**

```bash
pytest tests/ -v
```

Expected: all tests pass (7 storage tests + 5 route tests = 12 total).

- [ ] **Step 4: Smoke test the full flow**

Start the dashboard:
```bash
python dashboard/app.py
```

1. Open `http://localhost:5000` — verify "Bulk Register" link appears in navbar.
2. Click "Bulk Register" — page loads with two tabs.
3. Switch to Mode C tab — concurrency max changes to 20, hint text updates.
4. Toggle "Run Forever" on — count input greys out, progress bar hides.
5. Toggle "Run Forever" off — count input re-enables.
6. Click Export Excel — downloads a `.xlsx` file with two sheets.
7. Open `http://localhost:5000/api/bulk-accounts` — returns `[]` JSON.

- [ ] **Step 5: Final commit**

```bash
git add dashboard/templates/index.html dashboard/templates/search.html dashboard/templates/backtest.html
git commit -m "feat: add Bulk Register navbar link to all templates"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Two modes: HTTP Blast + Browser Pool | Task 5 (Mode C), Task 7 (Mode B) |
| Parallel creation with ThreadPoolExecutor | Task 5, Task 7 |
| Mode B max 200 workers | Task 8 (`min(max_workers, 200)`) |
| Mode C max 20 workers | Task 8 (`min(max_workers, 20)`) |
| Run Forever toggle | Task 8 (`run_forever` param), Task 10 (UI toggle) |
| Fixed count option | Task 8, Task 10 |
| Email verification toggle | Task 5, Task 7, Task 10 |
| SSE live progress | Task 8 (`/api/bulk-stream`) |
| Log feed in UI | Task 10 (`updateProgress`) |
| Accounts table (email, password, status) | Task 10 |
| Separate from main dashboard table | Task 2 (`bulk_accounts` table) |
| Full password visible | Task 10 (no masking in template) |
| Filter by mode/status | Task 8 (`/api/bulk-accounts`), Task 10 |
| Stop mechanism | Task 4 (`_STOP_EVENTS`), Task 8 |
| Persistent log files | Task 4 (`_append_log`) |
| Excel export, two sheets | Task 9 |
| Navbar link | Task 11 |
| `openpyxl` dependency | Task 1 |
| `bulk_runs` + `bulk_accounts` DB tables | Task 2 |
| Mode B endpoint discovery | Task 6 |

All spec requirements covered. No gaps found.
