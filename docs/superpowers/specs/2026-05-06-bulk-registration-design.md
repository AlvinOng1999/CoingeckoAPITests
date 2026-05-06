# Bulk Account Registration — Design Spec

**Date:** 2026-05-06  
**Status:** Approved

---

## Overview

A new **Bulk Register** page in the CoinGecko QA dashboard that lets testers create hundreds or thousands of CoinGecko accounts simultaneously to stress-test the registration endpoint. Accounts are stored in a separate SQLite table from the existing API key accounts. Results are viewable in a live-updating table and exportable to Excel.

---

## Goals

- Create large volumes of CoinGecko accounts in parallel to find registration rate limits or endpoint failures
- Support two distinct creation modes: lightweight HTTP requests (Mode B) and real browser automation (Mode C)
- Show live progress during runs via SSE log feed
- Persist all created accounts (email, password, verification status) to a separate local DB table
- Export results to Excel for analysis

---

## Architecture

### Option selected: In-process workers + SSE

Both modes run as background threads inside the existing Flask process. Progress streams to the frontend via Server-Sent Events, matching the pattern already used by the stress test endpoint. Results are written to SQLite as they arrive.

### New files

| File | Purpose |
|---|---|
| `dashboard/bulk_register.py` | Worker logic for Mode B and Mode C |
| `dashboard/templates/bulk_register.html` | New `/bulk-register` page |
| `logs/` | Per-run plain-text log files |

### New routes (added to `dashboard/app.py`)

| Route | Method | Purpose |
|---|---|---|
| `/bulk-register` | GET | Renders the bulk register page |
| `/api/bulk-start` | POST | Starts a run, returns `run_id` |
| `/api/bulk-stop` | POST | Signals workers to stop gracefully |
| `/api/bulk-stream/<run_id>` | GET | SSE stream of live per-account events |
| `/api/bulk-accounts` | GET | JSON list of accounts (filterable by run_id, status, mode) |
| `/api/bulk-export` | GET | Download Excel file (`?run_id=<id>` or all) |

---

## Data Model

Two new SQLite tables, separate from the existing `accounts` table.

```sql
CREATE TABLE IF NOT EXISTS bulk_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mode          TEXT,        -- 'http' | 'browser'
    target_count  INTEGER,     -- NULL if run_forever
    run_forever   INTEGER,     -- 0 | 1
    verify_email  INTEGER,     -- 0 | 1
    status        TEXT,        -- 'running' | 'stopped' | 'done'
    started_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    total_created INTEGER DEFAULT 0,
    total_failed  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bulk_accounts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER REFERENCES bulk_runs(id),
    email      TEXT,
    password   TEXT,
    verified   INTEGER DEFAULT 0,   -- 0 | 1
    status     TEXT,                -- 'pending' | 'verified' | 'unverified' | 'failed'
    error      TEXT,                -- error message if failed, else NULL
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

---

## UI — Bulk Register Page (`/bulk-register`)

### Layout

- **Navbar**: adds "Bulk Register" link between Dashboard and Stress Test
- **Mode tabs**: Mode B (HTTP Blast) | Mode C (Browser Pool)
- **Control panel** (per tab):
  - Number of accounts input (disabled when Run Forever is on)
  - Run Forever toggle
  - Include Email Verification toggle
  - Concurrency input: Mode B default 50, max 200 / Mode C default 5, max 20
  - Start button / Stop button (Stop disabled when not running)
- **Progress panel** (shown during/after a run):
  - Progress bar (hidden when Run Forever is on)
  - Summary line: `N accounts created · M failed · K rate-limited`
  - Live log feed (dark terminal-style box, auto-scrolls, 150px height)
- **Accounts table** (separate from main dashboard):
  - Columns: `#`, `Email`, `Password`, `Verification`, `Mode`, `Created`
  - Filter dropdowns: All modes / HTTP / Browser — All statuses / Verified / Unverified / Failed
  - Export button: downloads `.xlsx`
  - Refreshes every 3 seconds via polling `/api/bulk-accounts`

### Account table columns

| Column | Notes |
|---|---|
| # | Row number, newest first |
| Email | Full email address |
| Password | Full plaintext password (monospace font) |
| Verification | Verified / Unverified / Pending / Failed badge |
| Mode | HTTP or Browser badge |
| Created | Relative timestamp |

Passwords are shown in full — no masking. Credentials are stored in local SQLite only.

---

## Mode B — HTTP Blast

**Concurrency**: `ThreadPoolExecutor(max_workers=N)`, default 50, max 200.

**Per-worker sequence**:
1. Create disposable mailbox via `temp_email.create_mailbox()`
2. GET CoinGecko signup page → extract CSRF token + session cookie
3. Solve Cloudflare Turnstile via `captcha_solver.solve_turnstile()`
4. POST email step to CoinGecko registration endpoint
5. Solve hCaptcha via `captcha_solver.solve_hcaptcha()`
6. POST password step to CoinGecko registration endpoint
7. If `verify_email=True`: poll mail.tm inbox → GET verification link
8. Write result to `bulk_accounts`; emit SSE event; append to run log file

**Captcha note**: Two captchas per account (Turnstile + hCaptcha). CapSolver takes ~5–30s each. Realistic throughput: ~1 account per 30–60s per worker. With 50 workers: 50–100 accounts/minute.

**CSRF / endpoint discovery**: Mode B requires reverse-engineering CoinGecko's registration form POST targets, CSRF header names, and response format. This is done once during implementation by inspecting browser network traffic.

---

## Mode C — Browser Pool

**Concurrency**: `ThreadPoolExecutor(max_workers=N)`, default 5, max 20.  
**RAM estimate**: ~200–400 MB per Camoufox instance → 20 workers = 4–8 GB peak.

**Per-worker sequence**:
1. Create disposable mailbox via `temp_email.create_mailbox()`
2. Launch Camoufox browser instance
3. Call existing `coingecko.register(page, email, password)` — no changes to this function
4. If `verify_email=True`: call `temp_email.poll_inbox()` + `coingecko.confirm_email()`
5. Close browser
6. Write result to `bulk_accounts`; emit SSE event; append to run log file
7. Pick up next account from the queue

Mode C reuses all existing browser automation code unchanged.

---

## SSE Streaming Protocol

Endpoint: `GET /api/bulk-stream/<run_id>`

Each event is a JSON object on a `data:` line:

```json
{"done": 42, "total": 100, "email": "abc@mail.tm", "status": "verified",
 "run_id": 3, "errors": 1, "rate_limited": 0, "complete": false}
```

Final event when run ends:
```json
{"done": 100, "total": 100, "complete": true, "errors": 3, "rate_limited": 2, "run_id": 3}
```

Frontend updates:
- Progress bar `width`: `(done / total) * 100%` — hidden when Run Forever is on
- Summary line: updated each event
- Log feed: one line appended per event, auto-scrolls to bottom
- Table: polled every 3 seconds independently via `/api/bulk-accounts`

---

## Stop Mechanism

A module-level dict in `bulk_register.py`:

```python
_STOP_EVENTS: dict[int, threading.Event] = {}
```

- `POST /api/bulk-start` creates a new `threading.Event`, stores it keyed by `run_id`
- Workers check `stop_event.is_set()` before starting each new account
- `POST /api/bulk-stop` calls `stop_event.set()` — workers drain gracefully within one account cycle
- Run status in DB updated to `'stopped'`

---

## Loop / Run Forever

When `run_forever=True`:
- The target count input is disabled in the UI
- After each batch of workers completes, the executor starts a new batch immediately
- Total count accumulates (no reset between batches)
- The Stop button is the only exit
- DB `bulk_runs.target_count` is stored as `NULL`; `run_forever=1`

---

## Excel Export

Route: `GET /api/bulk-export?run_id=<id>` (omit `run_id` for all accounts)

Library: `openpyxl`

**Sheet 1 — Accounts**:

| # | Email | Password | Verified | Status | Mode | Run ID | Created At |
|---|---|---|---|---|---|---|---|

**Sheet 2 — Run Log**:

| Timestamp | Email | Status | Error |
|---|---|---|---|

Content matches the persistent log file at `logs/bulk_run_<id>.txt`.

---

## Persistent Log Files

Each run writes a log file to `logs/bulk_run_<run_id>.txt` as accounts are created. Format:

```
[2026-05-06 14:23:01] abc@mail.tm — verified
[2026-05-06 14:23:04] xyz@mail.tm — failed: 429 Too Many Requests
[2026-05-06 14:23:07] def@mail.tm — unverified (verification skipped)
```

The log directory is created automatically if it doesn't exist. Logs persist after the browser tab is closed.

---

## Dependencies

New package required:
- `openpyxl` — for Excel export

Already available:
- `camoufox` — Mode C browsers
- `captcha_solver` — Mode B captcha solving
- `temp_email` — disposable mailboxes
- `coingecko` — Mode C browser automation
- `flask`, `sqlite3`, `threading`, `concurrent.futures` — all stdlib or already installed

---

## Out of Scope

- API key retrieval (this feature is registration-only)
- Username capture (email + password only)
- Integration with the main `accounts` table
- Proxy rotation
