# CoinGecko API Key Automation — How It Works

This project automates the creation of CoinGecko demo accounts and API keys using disposable email addresses, browser automation, and CAPTCHA solving. It then exposes those keys through a local Flask dashboard.

---

## Architecture Overview

```
main.py  ──►  temp_email.py   (create disposable mailbox via mail.tm)
         ──►  coingecko.py    (browser automation via Camoufox/Playwright)
         ──►  storage.py      (persist to SQLite accounts.db)

dashboard/app.py  ──►  storage.py         (read accounts & bulk run data)
                  ──►  CoinGecko API      (proxy live price/market data)
                  ──►  bulk_register.py   (parallel account creation workers)

bulk_register.py  ──►  temp_email.py      (disposable mailboxes)
                  ──►  coingecko.py       (Mode C browser automation)
                  ──►  captcha_solver.py  (Mode B captcha solving)
                  ──►  storage.py         (persist bulk run & account data)

api_demo.py  ──►  storage.py        (pick newest key)
             ──►  CoinGecko API     (call demo endpoints directly)
```

---

## Module Breakdown

### `main.py` — Orchestrator

The entry point. Accepts `--count N` to create N accounts in sequence.

For each account it:
1. Calls `temp_email.create_mailbox()` to get a fresh disposable inbox
2. Opens a Camoufox browser and calls `coingecko.register()`
3. Polls the inbox for the verification email via `temp_email.poll_inbox()`
4. Extracts the confirmation link and calls `coingecko.confirm_email()`
5. Navigates to the CoinGecko developer dashboard and calls `coingecko.get_api_key()`
6. Saves the result to SQLite via `storage.save_account()`
7. Prints a summary table using the `rich` library

```
python main.py --count 1
```

---

### `temp_email.py` — Disposable Mailbox

Uses the [mail.tm](https://api.mail.tm) public API (no signup required).

| Function | What it does |
|---|---|
| `_get_domain()` | Fetches the first available `@xxx.com` domain from mail.tm |
| `create_mailbox()` | Registers a random `user@domain` address; also generates a strong CoinGecko password |
| `get_token()` | Authenticates the mailbox and returns a Bearer token |
| `poll_inbox()` | Polls every 4 seconds for up to 3 minutes until a CoinGecko email arrives |
| `extract_verification_link()` | Uses regex to pull the `https://...coingecko.com.../confirm...` URL from the email HTML |

The mailbox password is separate from the CoinGecko account password. A `_strong_password()` helper generates a password meeting CoinGecko's requirements (uppercase, lowercase, digits, special chars).

---

### `coingecko.py` — Browser Automation

Contains all Playwright/Camoufox interactions with the CoinGecko website. Uses the **synchronous Playwright API** with a **headful Chromium** window (visible browser).

#### Key constants
| Constant | Value |
|---|---|
| `HOMEPAGE` | `https://www.coingecko.com/` |
| `SIGNIN_URL` | `https://www.coingecko.com/en/users/sign_in` |
| `API_DASH` | `https://www.coingecko.com/en/developers/dashboard` |
| `NAV_TIMEOUT` | 60 seconds |
| `ELEM_TIMEOUT` | 20 seconds |

#### Main public functions

**`register(page, email, password)`**
1. Opens the CoinGecko homepage
2. Dismisses the cookie banner
3. Clicks **Sign up** → **Continue with email**
4. Types the disposable email address
5. Waits for Cloudflare Turnstile to load, then solves or waits for auto-solve
6. Submits the email step
7. Detects the password form, types the generated password
8. Handles hCaptcha on the password step (click checkbox → wait for `captchaVerified`)
9. Clicks the **Sign up** submit button

**`confirm_email(page, link, password)`**
- Navigates directly to the verification URL extracted from the inbox email
- Fills in the password again if CoinGecko shows a "set password" prompt

**`login(page, email, password)`**
- Opens the homepage, clicks **Login**, fills email → Continue → password → Login

**`get_api_key(page, email, password)`**
- Logs in, navigates to the API pricing page, clicks a "Get started free" CTA
- Fills in the demo account form (company name, use case, etc.)
- Scans the resulting page for a `CG-XXXXXXXXXXXXXXX` key using `_scan_for_api_key()`

#### CAPTCHA handling
Two CAPTCHAs appear in the signup flow:

| Step | CAPTCHA Type | Sitekey |
|---|---|---|
| Email submission | Cloudflare Turnstile | `0x4AAAAAABkuQIhLBgY-YxvO` |
| Password submission | hCaptcha | `d7b4358f-5390-46d4-a479-eb9a1fa28033` |

Solving strategy (in priority order):
1. **CAPTCHA service** — if `CAPTCHA_API_KEY` env var is set, uses CapSolver or 2captcha
2. **Auto-solve wait** — Camoufox's clean browser fingerprint often passes Turnstile automatically; waits up to 20 seconds
3. **Force-submit** — proceeds anyway if neither method works

---

### `captcha_solver.py` — CAPTCHA Service Integration

Configured via environment variables:

```bash
set CAPTCHA_SERVICE=capsolver   # or "2captcha"
set CAPTCHA_API_KEY=your_key_here
```

| Service | Turnstile cost | hCaptcha cost |
|---|---|---|
| [CapSolver](https://capsolver.com) | ~$0.80 / 1000 | ~$1.00 / 1000 |
| [2captcha](https://2captcha.com) | ~$2.99 / 1000 | ~$2.99 / 1000 |

If no API key is set, both functions return `None` and the flow falls back to the auto-solve wait.

---

### `storage.py` — SQLite Persistence

Creates and manages `accounts.db` in the project root on import. Uses WAL journal mode and a 30-second lock timeout for high-concurrency bulk runs.

**Table: `accounts`**

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment row ID |
| `email` | TEXT UNIQUE | Disposable email used to register |
| `cg_password` | TEXT | Password for the CoinGecko account |
| `api_key` | TEXT | The `CG-XXXXXXXXXXXXXXX` demo API key |
| `calls_used` | INTEGER | Locally-tracked usage counter |
| `calls_left` | INTEGER | Locally-tracked remaining calls (default 10,000) |
| `is_pinned` | INTEGER | 1 = this key is the forced active key |
| `created_at` | TEXT | UTC timestamp of creation |

**Table: `bulk_runs`** (bulk registration runs)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Run ID |
| `mode` | TEXT | `'http'` or `'browser'` |
| `target_count` | INTEGER | Target accounts (NULL if run_forever) |
| `run_forever` | INTEGER | 1 = loop indefinitely |
| `verify_email` | INTEGER | 1 = verify email after registration |
| `status` | TEXT | `'running'`, `'stopped'`, or `'done'` |
| `started_at` | TEXT | UTC timestamp |
| `total_created` | INTEGER | Successful registrations |
| `total_failed` | INTEGER | Failed attempts |

**Table: `bulk_accounts`** (accounts created by bulk runs)

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Row ID |
| `run_id` | INTEGER | FK → `bulk_runs.id` |
| `email` | TEXT | Disposable email |
| `password` | TEXT | Plaintext CoinGecko password |
| `verified` | INTEGER | 1 = email verified |
| `status` | TEXT | `'pending'`, `'verified'`, `'unverified'`, or `'failed'` |
| `error` | TEXT | Error message if failed, else NULL |
| `created_at` | TEXT | UTC timestamp |

**Active key selection logic** (`get_active_account()`):
- Returns the pinned account if one exists
- Otherwise returns the oldest account with `calls_left > 0`

---

### `dashboard/app.py` — Flask Web Dashboard

Starts a local web server at `http://localhost:5000`.

```
python dashboard/app.py
```

**Routes:**

| Route | Method | Action |
|---|---|---|
| `/` | GET | Shows all accounts with password and full API key (show/hide toggles) |
| `/create` | POST | Launches `main.py --count 1` as a subprocess |
| `/pin/<id>` | POST | Pins an account as the active key |
| `/unpin` | POST | Unpins all; reverts to oldest-with-calls logic |
| `/delete/<id>` | POST | Removes an account from the database |
| `/bulk-register` | GET | Bulk account registration stress-test page |
| `/search` | GET | Coin search page |
| `/backtest` | GET | Strategy backtesting page |
| `/api/prices` | GET | Proxies CoinGecko `/simple/price` (BTC, ETH, SOL, BNB, XRP, ADA) |
| `/api/global` | GET | Proxies CoinGecko `/global` market stats |
| `/api/trending` | GET | Proxies CoinGecko `/search/trending` |
| `/api/markets` | GET | Proxies CoinGecko `/coins/markets` (top 10 by market cap) |
| `/api/debug-key` | GET | Returns locally-tracked usage for the active key |
| `/api/bulk-start` | POST | Starts a bulk run; returns `run_id` |
| `/api/bulk-stop` | POST | Signals workers to stop gracefully |
| `/api/bulk-stream/<run_id>` | GET | SSE stream of live per-account events |
| `/api/bulk-accounts` | GET | JSON list of bulk accounts (filterable by `run_id`, `status`, `mode`) |
| `/api/bulk-export` | GET | Download `.xlsx` file (`?run_id=<id>` or all accounts) |
| `/api/bulk-log/<run_id>` | GET | Last 200 lines of the run's plain-text step log |

Each API proxy call automatically increments `calls_used` / decrements `calls_left` in the DB for real-time feedback. Returns HTTP 429 passthrough if CoinGecko rate-limits.

---

### `dashboard/bulk_register.py` — Bulk Registration Workers

Contains the worker logic for both bulk registration modes. Both modes expose SSE generator functions that yield progress events to the frontend.

**Module-level state:**
- `_STOP_EVENTS` — dict mapping `run_id → threading.Event` for graceful stop
- `_endpoints_cache` — cached endpoint config parsed from `registration_requests.json`

#### Mode B — HTTP Blast

Direct HTTP requests without a browser. Handles CAPTCHAs programmatically.

**Concurrency:** `ThreadPoolExecutor` with default 50 workers, max 200.

**Per-worker sequence:**
1. Check if `registration_requests.json` exists → load endpoints; if not, auto-run discovery (one browser session to capture POST URLs and field names, saved to file)
2. Create disposable mailbox via `temp_email.create_mailbox()`
3. GET CoinGecko signup page → extract CSRF token
4. Solve Cloudflare Turnstile via `captcha_solver.solve_turnstile()`
5. POST email step to discovered endpoint
6. Solve hCaptcha via `captcha_solver.solve_hcaptcha()`
7. POST password step to discovered endpoint
8. If `verify_email=True`: poll inbox → GET verification link
9. Write to `bulk_accounts` table; emit SSE event; append to run log file

**Auto-discovery:** On the first Mode B run, if `registration_requests.json` is missing, `_discover_endpoints()` launches a headless Camoufox browser, intercepts all POST requests during `coingecko.register()`, and saves them. Subsequent runs use the cached file. Delete `registration_requests.json` to force re-discovery.

**Realistic throughput:** ~1 account / 30–60s per worker (dominated by captcha solve time). With 50 workers: ~50–100 accounts/minute.

#### Mode C — Browser Pool

Real Camoufox browser per worker. Stealth but heavier on RAM.

**Concurrency:** `ThreadPoolExecutor` with default 5 workers, max 20. Each browser uses ~200–400 MB RAM.

**Per-worker sequence:**
1. Create disposable mailbox
2. Launch headless Camoufox browser
3. Call `coingecko.register(page, email, password)` — reuses existing automation code
4. If `verify_email=True`: poll inbox → `coingecko.confirm_email()`
5. Close browser
6. Write result to DB; emit SSE event; append to log

#### Step logging

Both modes write granular per-step entries to `logs/bulk_run_<id>.txt` using `>` as separator (e.g., `[ts] email > Solving hCaptcha...`). Final result lines use `—` as separator (e.g., `[ts] email — verified`). The dashboard's Step Log panel reads this file every 2 seconds.

#### Stop mechanism

`POST /api/bulk-stop` sets the run's `threading.Event`. Workers check it before starting each new account and after completing `as_completed` futures. The executor is shut down with `shutdown(wait=False, cancel_futures=True)` for immediate cancellation.

---

### `api_demo.py` — CLI API Demo

A standalone script that picks the most recently created account from `accounts.db` and calls three CoinGecko endpoints directly:

1. `/simple/price` — Bitcoin & Ethereum USD price
2. `/coins/markets` — Top 10 coins by market cap
3. `/search/trending` — Trending coins

Uses the `x-cg-demo-api-key` header. Output is formatted with `rich`.

```
python api_demo.py
```

---

## Full Flow — Step by Step

```
python main.py --count 1
```

```
1. temp_email.create_mailbox()
   └─ GET  mail.tm/domains          → pick domain (e.g. @tgore.com)
   └─ POST mail.tm/accounts         → create random@tgore.com
   └─ POST mail.tm/token            → get Bearer token
   └─ generate strong CoinGecko password

2. Camoufox browser opens (headful Chromium)

3. coingecko.register()
   └─ GET  coingecko.com/           → homepage + Cloudflare check
   └─ click "Sign up" button
   └─ click "Continue with email"
   └─ type email address
   └─ solve Cloudflare Turnstile (service or auto-wait)
   └─ click "Continue with email" to submit
   └─ type password in new-password field
   └─ click hCaptcha checkbox → wait for captchaVerified
   └─ click "Sign up" submit

4. temp_email.poll_inbox()
   └─ GET mail.tm/messages every 4s (up to 3 min)
   └─ finds email from CoinGecko
   └─ GET mail.tm/messages/<id>     → fetch full body

5. temp_email.extract_verification_link()
   └─ regex search for coingecko.com/en/users/confirmation URL

6. coingecko.confirm_email()
   └─ GET  <verification URL>       → confirms account

7. coingecko.get_api_key()
   └─ coingecko.login()
   └─ GET  coingecko.com/en/api/pricing
   └─ click "Get started for free" CTA
   └─ fill demo account form (company name, use case)
   └─ scan page DOM + HTML for CG-XXXXXXXXXXXXXXX key

8. storage.save_account(email, password, api_key)
   └─ INSERT into accounts.db

9. Rich table printed to terminal with all created accounts
```

---

## Debug & Diagnostic Scripts

| Script | Purpose |
|---|---|
| `debug_flow.py` | Runs registration with screenshots at each step → `debug_screenshots/` |
| `debug_api_dash.py <email> <pw>` | Tests the API dashboard flow for an existing account → `debug_api_dash.png` |
| `run_one.py` | Full single-account run saving screenshots to `run_screenshots/` |
| `get_sitekey.py` | Scrapes the current Turnstile/hCaptcha sitekeys from CoinGecko's pages |

> **Note:** `run_one.py` and `get_sitekey.py` require `playwright-stealth` which is not in `requirements.txt`. Install separately:
> ```
> python -m pip install playwright-stealth
> ```

---

## Setup & Installation

```bash
# 1. Install Python dependencies
python -m pip install -r requirements.txt

# 2. Install the Playwright Chromium browser
python -m playwright install chromium

# 3. (Optional) Set CAPTCHA service credentials
set CAPTCHA_SERVICE=capsolver
set CAPTCHA_API_KEY=your_api_key

# 4. Run the main flow
python main.py --count 1

# 5. (Optional) Launch the dashboard
python dashboard/app.py
```

### Bulk Registration (stress testing)

```bash
# All dependencies are already in requirements.txt
# Set CAPTCHA credentials (required for Mode B HTTP Blast)
set CAPTCHA_SERVICE=capsolver
set CAPTCHA_API_KEY=your_api_key

# Launch the dashboard — navigate to /bulk-register
python dashboard/app.py
```

**Mode B first run:** When you start a Mode B run for the first time, a browser session launches automatically to discover CoinGecko's registration endpoints and save them to `registration_requests.json`. This takes 1–2 minutes. All subsequent runs use the cached file.

**Mode C:** No extra setup needed — uses the existing Camoufox installation.

---

## File Structure

```
qa api/
├── main.py                      # Entry point — orchestrates account creation
├── coingecko.py                 # All browser automation (register, login, get key)
├── temp_email.py                # Disposable mailbox via mail.tm API
├── storage.py                   # SQLite persistence (accounts.db + bulk tables)
├── captcha_solver.py            # CapSolver / 2captcha integration
├── api_demo.py                  # CLI demo of CoinGecko API endpoints
├── accounts.db                  # SQLite database (auto-created on first run)
├── registration_requests.json   # Captured Mode B endpoints (auto-created on first run)
├── requirements.txt             # Python dependencies
├── dashboard/
│   ├── app.py                   # Flask web dashboard + bulk register routes
│   ├── bulk_register.py         # Mode B (HTTP) + Mode C (browser) worker logic
│   └── templates/
│       ├── index.html           # Main dashboard (API keys, passwords, usage)
│       ├── bulk_register.html   # Bulk registration stress-test page
│       ├── search.html          # Coin search page
│       └── backtest.html        # Strategy backtesting page
├── logs/
│   └── bulk_run_<id>.txt        # Per-run step logs (auto-created)
├── scripts/
│   └── discover_cg_registration.py  # Manual endpoint discovery (runs automatically now)
├── tests/
│   ├── test_bulk_storage.py     # Storage layer unit tests
│   └── test_bulk_routes.py      # Flask route tests
├── debug_flow.py                # Debug script with screenshots
├── debug_api_dash.py            # Debug script for API dashboard flow
├── run_one.py                   # Full run with screenshots (needs playwright-stealth)
└── get_sitekey.py               # Sitekey scraper (needs playwright-stealth)
```

---

## External Services Used

| Service | Purpose | Cost |
|---|---|---|
| [mail.tm](https://mail.tm) | Disposable email inboxes | Free |
| [CoinGecko](https://coingecko.com) | Account registration & API keys | Free (demo tier) |
| [CapSolver](https://capsolver.com) | CAPTCHA solving (optional) | Paid |
| [2captcha](https://2captcha.com) | CAPTCHA solving (optional) | Paid |

---

## Important Notes

- `main.py` runs the browser **headful** (visible window) by default. Bulk Register Mode C uses headless Camoufox — this is intentional since bulk runs don't need to be observed.
- `accounts.db`, `registration_requests.json`, and `logs/` may contain generated emails, passwords, and API keys — treat them as secret files and do not commit them to public repositories.
- Each CoinGecko demo API key has a **10,000 calls/month** free limit. Usage is tracked locally since the `/key` status endpoint requires a PRO subscription.
- All account creation and key generation scripts call **live external services**. Only run them when you have explicit authorization to do so.
- **Mode B auto-discovery**: On the first Mode B run, a browser session is launched automatically to capture CoinGecko's registration endpoints (costs ~2 captcha solves). This only happens once — `registration_requests.json` is reused on all subsequent runs. Delete this file to force re-discovery if the registration flow changes.
- **CAPTCHA costs**: Mode B requires two captcha solves per account (Turnstile + hCaptcha). At CapSolver rates, expect ~$0.0018 per account. Set `CAPTCHA_SERVICE` and `CAPTCHA_API_KEY` env vars before running Mode B.
