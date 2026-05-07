# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup (Windows, one-time):**
```bat
setup.bat
```
This creates a venv, installs dependencies, and installs Playwright browsers.

**Manual setup (any OS):**
```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

**Run the main flow (single account):**
```bash
python main.py --count 1
```

**Start the Flask dashboard:**
```bash
python dashboard\app.py
# Open http://localhost:5000
```

**Exercise a saved API key:**
```bash
python api_demo.py
```

**Syntax validation (no test runner configured):**
```bash
python -m py_compile main.py coingecko.py storage.py temp_email.py api_demo.py debug_flow.py debug_api_dash.py run_one.py get_sitekey.py dashboard\app.py
```

**Single-file validation:**
```bash
python -m py_compile <path-to-file.py>
```

**Import smoke check:**
```bash
python -c "import storage, temp_email, coingecko, main; print('imports OK')"
```

**Run tests:**
```bash
pytest
pytest tests/test_bulk_storage.py
pytest tests/test_bulk_routes.py
pytest tests/test_bulk_storage.py::test_create_bulk_run_returns_id
pytest -v
```

> `run_one.py` and `get_sitekey.py` require `playwright-stealth`, which is not in `requirements.txt`. Install it separately with `python -m pip install playwright-stealth` before using those scripts.

## Architecture

This is a Python proof-of-concept that automates CoinGecko account and demo API key creation using disposable mailboxes, Camoufox/Playwright browser automation, SQLite persistence, and a Flask dashboard.

**Entry points:**
- `main.py` — CLI orchestrator: calls `temp_email.py` → `coingecko.py` → `storage.py` to create one account end-to-end
- `dashboard/app.py` — Flask web UI on `:5000` with 40+ routes for account management, bulk registration, API proxying, stress testing, and backtesting
- `api_demo.py` — calls CoinGecko demo endpoints using the newest saved key from `accounts.db`
- `debug_flow.py`, `debug_api_dash.py`, `run_one.py`, `get_sitekey.py` — diagnostic scripts that run visible browsers and save screenshots/HTML to `debug_screenshots\`, `run_screenshots\`, etc.

**Core modules:**
- `coingecko.py` — all CoinGecko browser interactions: `register()`, `confirm_email()`, `login()`, `get_api_key()`, plus shared selectors, timeouts (`NAV_TIMEOUT=60s`, `ELEM_TIMEOUT=20s`), and Turnstile/CAPTCHA handling. Centralize any selector or flow changes here.
- `temp_email.py` — all `mail.tm` API calls: domain discovery, mailbox creation, token retrieval, inbox polling (5s interval, 3min timeout), verification-link extraction.
- `storage.py` — owns the SQLite DB path (`accounts.db`), auto-initializes tables on import. Three tables: `accounts` (keys + usage), `bulk_runs` (bulk sessions), `bulk_accounts` (per-run records). Uses WAL mode with 30s lock timeout for concurrency.
- `captcha_solver.py` — optional CapSolver/2captcha integration.
- `dashboard/bulk_register.py` — `ThreadPoolExecutor` worker pool (default 5, max 20) for parallel account creation. Streams SSE events; logs steps to `logs/bulk_run_<id>.txt`.

**Dashboard imports root modules via `sys.path.insert(0, "..")`** — keep shared modules one directory above `dashboard/`.

**Tests** use pytest fixtures with `tmp_path` and `monkeypatch` for DB isolation. Storage tests use in-memory SQLite (`:memory:`); route tests use Flask's test client.

## Key conventions

- **Synchronous Playwright only.** Do not convert scripts to async unless the entire call chain is updated.
- **Headful by default for debugging**, headless only for bulk registration workers. Preserve headless/headful distinctions when modifying flows.
- **Selectors target the CoinGecko auth modal**, not the page body. Common patterns: `input[name='user[email]']`, `.gecko-modal input[type='email']`.
- **Keep browser context values consistent** (desktop Chrome user agent, viewport, locale, timezone) when changing browser setup — several flows depend on realistic fingerprints.
- **Turnstile handling:** wait for `input[name='cf-turnstile-response']` but continue after timeout. Preserve that fallback behavior unless intentionally changing the flow.
- **Usage counter default:** free tier is `10000` calls/month if CoinGecko's `/key` response omits `rate_limit_request_per_month`.
- **Debug scripts save screenshots/HTML at each step** before exiting on failure. Follow that pattern when adding new diagnostics.
- **`accounts.db` and all generated screenshots/HTML are local artifacts that may contain secrets.** Do not commit them.
- **Do not run account-creation or key-generation flows** unless the user has explicitly requested and confirmed authorization — these call live external services (`mail.tm`, CoinGecko, Cloudflare-protected pages).

## External services

| Service | Used by | Purpose |
|---|---|---|
| mail.tm | `temp_email.py` | Disposable mailboxes for registration |
| CoinGecko | `coingecko.py`, `dashboard/app.py` | Account registration, API key retrieval, demo API calls |
| CapSolver / 2captcha | `captcha_solver.py` | Optional CAPTCHA solving (Camoufox usually bypasses without it) |
