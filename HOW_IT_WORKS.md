# CoinGecko API Key Automation — How It Works

This project automates the creation of CoinGecko demo accounts and API keys using disposable email addresses and stealth browser automation (Camoufox — a Firefox-based anti-fingerprint browser). It then exposes those keys through a local Flask dashboard.

---

## Architecture Overview

```
main.py  ──►  temp_email.py             (create disposable mailbox via mail.tm)
         ──►  coingecko.py              (browser automation via Camoufox/Playwright)
         ──►  storage.py                (persist to SQLite accounts.db)

dashboard/app.py  ──►  storage.py       (read accounts & bulk run data)
                  ──►  CoinGecko API    (proxy live price/market/backtest data)
                  ──►  bulk_register.py (parallel account creation workers)

dashboard/bulk_register.py  ──►  temp_email.py  (disposable mailboxes)
                            ──►  coingecko.py   (browser automation)
                            ──►  storage.py     (persist bulk run & account data)

api_demo.py  ──►  storage.py        (pick newest key)
             ──►  CoinGecko API     (call demo endpoints directly)

scripts/discover_cg_registration.py  (standalone registration analysis tool)
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
| `poll_inbox()` | Polls every 5 seconds (with 0–3 s jitter before starting) for up to 3 minutes until a CoinGecko email arrives; backs off exponentially (up to 30 s) on connection errors so concurrent workers don't cascade-fail |
| `extract_verification_link()` | Uses regex to pull the `https://...coingecko.com.../confirm...` URL from the email HTML; runs `html.unescape()` to convert `&amp;` → `&` so the token query-string is valid |

The mailbox password is separate from the CoinGecko account password. A `_strong_password()` helper generates a password meeting CoinGecko's requirements (uppercase, lowercase, digits, special chars).

---

### `coingecko.py` — Browser Automation

Contains all Playwright interactions with the CoinGecko website. Uses the **synchronous Playwright `Page` API** — the actual browser (headful or headless Firefox via Camoufox) is provided by the caller (`main.py` uses headful, `bulk_register.py` uses headless).

#### Key constants
| Constant | Value |
|---|---|
| `HOMEPAGE` | `https://www.coingecko.com/` |
| `SIGNIN_URL` | `https://www.coingecko.com/en/users/sign_in` |
| `API_DASH` | `https://www.coingecko.com/en/developers/dashboard` |
| `API_PRICING` | `https://www.coingecko.com/en/api/pricing` |
| `NAV_TIMEOUT` | 60 seconds |
| `ELEM_TIMEOUT` | 20 seconds |

#### Main public functions

**`register(page, email, password)`**
1. Opens the CoinGecko homepage
2. Dismisses the cookie banner
3. Clicks the sign-up entry point via `_click_signup_entry()` (tries multiple button/link text variants: "Sign up", "Get Started", "Create Account")
4. Clicks **Continue with email**
5. Types the disposable email address
6. Waits for Cloudflare Turnstile to load, then solves or waits for auto-solve
7. Submits the email step; handles the case where the button stays disabled (force-click fallback)
8. Detects the password form (which appears in the same modal after email submission), types the generated password
9. Clicks the hCaptcha checkbox iframe (`_click_hcaptcha_checkbox()`) and polls for `window.captchaVerified` — Camoufox's clean fingerprint typically auto-verifies; if a challenge overlay appears, presses Escape and continues
10. Waits up to 15 s for the **Sign up** submit button (`[data-auth-target='signUpSubmit']`) to become enabled; falls back to force-click if captchaVerified stays false

**`confirm_email(page, link, password)`**
- Navigates to the verification URL via `_goto()` (which waits out any Cloudflare challenge before proceeding)
- Fills in the password again if CoinGecko shows a "set password" prompt after confirmation

**`login(page, email, password)`**
- Opens the homepage, clicks **Login**, fills email → Continue with email → password → Login
- Handles modals that show the email input directly or require a "Continue with email" step first

**`get_api_key(page, email, password)`**

Uses a multi-stage strategy with fallbacks:
1. Navigates directly to the developer dashboard (`API_DASH`)
2. Re-logs in if redirected to the auth page
3. If already on the dashboard: scans for an existing key via `_scan_for_api_key()`; if found, returns immediately
4. Checks for an onboarding modal (`_wait_for_modal()`); if found, fills it via `_fill_demo_account_modal()`
5. If still no key: navigates to the pricing page and clicks the free CTA via `_click_pricing_free_cta()`
6. Re-logs in if the CTA redirects to auth
7. Polls for a modal or key (up to 6 s); fills the modal if it appears
8. Polls for key on the resulting page (up to 5 s)
9. Final fallback: navigates back to dashboard, polls for key + clicks any "Create API Key" button (up to 10 s)
10. Saves `debug_api_dash.png` and raises `RuntimeError` if no key is found

#### Key private helpers

| Helper | What it does |
|---|---|
| `_click_signup_entry(page)` | Tries multiple sign-up button/link text patterns to open the auth modal |
| `_click_pricing_free_cta(page)` | Pure JS DOM pass — scrolls and clicks the first "Get started free" / "Create free account" element |
| `_wait_for_modal(page)` | Polls via JS until a dialog, heading, or form with ≥2 inputs is detected |
| `_fill_demo_account_modal(page)` | Fills the full demo account form: company (random), team size "5", role "Developer", use-case radio (Research), referral radio (Word of mouth), description textarea, ticks all checkboxes, then clicks "Create Demo Account" → "Create API Key" |
| `_ensure_api_key_form_submitted(page)` | Wraps `_wait_for_modal` + `_fill_demo_account_modal` |
| `_random_company_name()` | Generates a random company name from word lists (e.g. "Nova Analytics") |
| `_scan_for_api_key(page)` | Scans input values, visible text elements, and raw HTML for a `CG-[A-Za-z0-9]{15,}` pattern |
| `_solve_and_inject_turnstile(page)` | Calls CAPTCHA service → falls back to 20 s auto-solve wait → proceeds anyway |
| `_click_hcaptcha_checkbox(page)` | Finds the hCaptcha checkbox iframe, clicks it, polls 30 s for `captchaVerified` |
| `_inject_token(page, token)` | Writes a Turnstile token into the hidden input and calls `turnstileCallback` |
| `_inject_hcaptcha_token(page, token)` | Injects hCaptcha token and sets `window.captchaVerified = true` |
| `_is_logged_in(page)` | Returns `True` if the Login button is absent or hidden |
| `_modal_scroll(page, amount)` | Scrolls inside the modal container (falls back to `window.scrollBy`) |
| `_scroll_and_click_button(page, pattern)` | Scrolls then clicks the first visible+enabled button or link matching `pattern` |
| `_first_visible(page, selectors)` | Returns the first visible locator from a list of CSS selectors |
| `_click_visible_button(page, pattern)` | Clicks the first visible+enabled button whose text matches a regex |
| `_goto(page, url)` | Navigates and waits for Cloudflare challenge to clear |
| `_dismiss_cookie_banner(page)` | Clicks the cookie accept button if present |
| `_wait_cloudflare(page)` | Polls page content/URL until the Cloudflare challenge clears |

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

Used only by `coingecko.py` (the single-account flow in `main.py`). **Not used by bulk registration** — Camoufox's browser fingerprint handles CAPTCHAs automatically there.

Configured via environment variables (optional):
```bash
set CAPTCHA_SERVICE=capsolver   # or "2captcha"
set CAPTCHA_API_KEY=your_key_here
```

| Service | Turnstile cost | hCaptcha cost |
|---|---|---|
| [CapSolver](https://capsolver.com) | ~$0.80 / 1000 | ~$1.00 / 1000 |
| [2captcha](https://2captcha.com) | ~$2.99 / 1000 | ~$2.99 / 1000 |

If no API key is set, both functions return `None` and the flow falls back to the auto-solve wait (Camoufox fingerprint usually passes within 20 seconds).

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
| `mode` | TEXT | Always `'browser'` |
| `target_count` | INTEGER | Number of accounts to create |
| `run_forever` | INTEGER | 0 = stop after target_count (always 0 currently) |
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
| `/` | GET | Main dashboard — API keys table with password and full API key (show/hide toggles), usage stat cards, sync bar |
| `/create` | POST | Launches `main.py --count 1` as a subprocess |
| `/pin/<id>` | POST | Pins an account as the active key |
| `/unpin` | POST | Unpins all; reverts to oldest-with-calls logic |
| `/delete/<id>` | POST | Removes an account from the database |
| `/refresh/<api_key>` | POST | Syncs usage from CoinGecko `GET /key`, then redirects to `/` |
| `/refresh-all` | POST | Syncs all keys from CoinGecko, then redirects to `/` |
| `/bulk-register` | GET | Bulk account registration stress-test page |
| `/search` | GET | Coin search page |
| `/backtest` | GET | Strategy backtesting page |
| `/api/prices` | GET | Proxies CoinGecko `/simple/price` (BTC, ETH, SOL, BNB, XRP, ADA) |
| `/api/global` | GET | Proxies CoinGecko `/global` market stats |
| `/api/trending` | GET | Proxies CoinGecko `/search/trending` |
| `/api/markets` | GET | Proxies CoinGecko `/coins/markets` (top 10 by market cap) |
| `/api/search` | GET | Proxies CoinGecko `/search?query=<q>` (coin search) |
| `/api/backtest` | GET | Fetches price history and runs a backtest strategy (see below) |
| `/api/usage-stats` | GET | JSON usage totals for all keys — polled every few seconds by the dashboard |
| `/api/sync-all` | POST | Syncs all keys from CoinGecko `GET /key`; returns `{synced, total, pro_required, keys[]}` |
| `/api/debug-key` | GET | Tries live sync from `GET /key`; falls back to local DB; returns `{source, calls_used, calls_left}` |
| `/api/stress-test` | GET | SSE stream — burns through N API calls cycling across 6 endpoints, emitting live progress + latency |
| `/api/bulk-start` | POST | Starts a bulk run; returns `{run_id, mode, max_workers}` |
| `/api/bulk-stop` | POST | Signals workers to stop gracefully |
| `/api/bulk-stream/<run_id>` | GET | SSE stream of live per-account events |
| `/api/bulk-accounts` | GET | JSON list of bulk accounts (filterable by `run_id`, `status`, `mode`) |
| `/api/bulk-delete` | POST | Deletes bulk accounts by ID list `{ids: [...]}` |
| `/api/bulk-export` | GET | Download `.xlsx` file (`?run_id=<id>` or all accounts) with Accounts + Run Log sheets |
| `/api/bulk-log/<run_id>` | GET | Last 200 lines of the run's plain-text step log |

Each API proxy call automatically increments `calls_used` / decrements `calls_left` in the DB for real-time feedback. Returns HTTP 429 passthrough if CoinGecko rate-limits.

#### Backtesting (`/api/backtest`)

Fetches OHLCV data from CoinGecko `/coins/{id}/market_chart` and runs a trading simulation via `compute_backtest()`. Supports three strategies:

| Strategy | Parameter | Logic |
|---|---|---|
| **Buy & Hold** | `strategy=hold` | Buy on day 1, sell on last day |
| **SMA Crossover** | `strategy=sma` | 7-day / 21-day moving average crossover signals |
| **RSI** | `strategy=rsi` | 14-period RSI; buy when RSI < 30, sell when RSI > 70 |

Returns: `strategy_name`, `trades`, `initial`, `final`, `return_pct`, `benchmark_return`, daily price series, and `trade_signals`.

#### Stress test (`/api/stress-test`)

SSE endpoint that fires N requests (1–500) cycling across 6 CoinGecko endpoints in sequence. Each event includes: `done`, `total`, `status`, `errors`, `rate_limited`, `calls_used`, `calls_left`, `endpoint`, `latency_ms`. Pauses 15 s automatically on HTTP 429.

#### Usage sync

The dashboard tracks API usage locally (counting only calls made through the app). To sync with CoinGecko's actual count, the app calls `GET /api/v3/key` using the demo key. If CoinGecko returns usage data (`current_total_monthly_calls`, `current_remaining_monthly_calls`), the DB is updated in-place. If the endpoint returns 401/403 (Pro subscription required), the dashboard shows a warning and continues with local counts.

Sync is triggered in three ways:
- **`↻` row button** in the API keys table — syncs that specific key
- **`↻ Sync from CoinGecko` button** on the stats bar — syncs all keys via AJAX (no page reload)
- **`/api/debug-key`** — always attempts a live sync before returning usage data

---

#### Main dashboard UI features

- **API keys table** — columns: Email, Password (hidden by default), API Key (hidden by default), Calls Used, Calls Left, Usage Bar, Created, Actions. Password and API key each have a 👁 / 🙈 toggle to reveal/hide the full value.
- **Stat cards** — Total Keys, Calls Remaining, Calls Used, Active Key. Updated every few seconds without a page reload via `/api/usage-stats`.
- **Sync bar** — shown below the stat cards. Displays a note about local tracking. "↻ Sync from CoinGecko" button calls `/api/sync-all` and updates the stat cards in place; shows green success or a red Pro-required warning.
- **Global Market, Trending, Markets** — live CoinGecko data panels, auto-refreshing.
- **Stress Test** — controlled API blast with live SSE progress, latency chart, and monthly usage bar.

---

### `dashboard/bulk_register.py` — Bulk Registration Workers

Runs parallel **headless** Camoufox browser workers that each create one CoinGecko account. Exposes an SSE generator (`run_bulk`) that yields progress events to the frontend. No CAPTCHA API key required — Camoufox's clean browser fingerprint handles Cloudflare Turnstile and hCaptcha automatically.

**Module-level state:**
- `_STOP_EVENTS` — dict mapping `run_id → threading.Event` for graceful stop

**Concurrency:** `ThreadPoolExecutor` with default 5 workers, max 20. Each browser uses ~200–400 MB RAM.

**Per-worker sequence:**
1. Sleep a random stagger delay (`random.uniform(0, max_workers × 3)` seconds) to spread browser launches
2. Create disposable mailbox via `temp_email.create_mailbox()`
3. Launch **headless** Camoufox browser, call `coingecko.register(page, email, password)` — auto-handles CAPTCHAs. Retries once on failure with a 5–10 s pause
4. Close browser immediately after registration (no browser held open during inbox wait)
5. If `verify_email=True`: poll inbox → extract link → open new **headless** browser → `coingecko.confirm_email()`. Failure here saves as `unverified` (not `failed`)
6. Write result to DB; emit SSE event; append to log

**Realistic throughput:** ~1 account / 60–120s per worker (browser launch + CAPTCHA wait). With 5 workers running in parallel: ~3–5 accounts/minute.

#### Step logging

Workers write granular per-step entries to `logs/bulk_run_<id>.txt` using `>` as separator (e.g., `[ts] email > Browser launched...`). Final result lines use `—` as separator (e.g., `[ts] email — verified`). The dashboard's Step Log panel reads this file every 2 seconds.

#### Stop mechanism

`POST /api/bulk-stop` sets the run's `threading.Event`. Workers check it before their stagger sleep and after completing their task. The executor is shut down with `shutdown(wait=False, cancel_futures=True)` to cancel any queued work immediately.

#### Bulk accounts table (on `/bulk-register`)

- **Pagination** — 25 / 50 / 100 accounts per page selector; Prev / Next buttons; pager row hidden when all results fit on one page. Filter by status resets to page 1.
- **Select / Select All** — checkbox per row; header checkbox selects or deselects all rows on the current page (indeterminate when partially selected). Selections are preserved across page navigation and the 3-second polling refresh.
- **Delete** — "Delete (N)" button shows the selection count; disabled when nothing is selected. Clicking confirms, then calls `POST /api/bulk-delete` with the selected IDs and reloads the table.
- **Log panels** — two side-by-side panels in the progress area. **Live Events** (left) shows SSE summary events in real time; **Step Log** (right) polls `/api/bulk-log/<run_id>` every 2 seconds and shows granular per-step entries from the log file. Both panels have a **Clear** button in their header to wipe the display without affecting the underlying log file.
- **Stats** — created count displayed in green, failed count in red.
- **Progress bar** — always shown, fills as accounts complete against the fixed target count.

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
   └─ Navigate directly to API_DASH
   └─ Re-login if redirected to auth page
   └─ Scan dashboard for existing key → return immediately if found
   └─ Check for onboarding modal → fill and continue
   └─ Navigate to pricing page → click "Get started free" CTA
   └─ Re-login if CTA redirects to auth
   └─ Poll for Demo Account modal or key (up to 6 s)
   └─ _fill_demo_account_modal():
       ├─ Company name (random, e.g. "Nova Analytics")
       ├─ Team size: "5"
       ├─ Role: "Developer"
       ├─ Use-case radio: Research
       ├─ Referral radio: Word of mouth
       ├─ Textarea: QA testing description
       ├─ Tick all unchecked checkboxes
       └─ Click "Create Demo Account" → "Create API Key"
   └─ Poll for key after modal (up to 5 s)
   └─ Final fallback: navigate to dashboard, poll + click "Create API Key" (up to 10 s)
   └─ Save debug_api_dash.png and raise RuntimeError if key not found

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
# No CAPTCHA API key needed — Camoufox handles challenges automatically

# Launch the dashboard — navigate to /bulk-register
python dashboard/app.py
```

No extra setup needed beyond the existing Camoufox installation. The live events panel shows `⏳ Initialising — launching N browser workers...` when a run starts so you know it's running.

---

## File Structure

```
qa api/
├── main.py                      # Entry point — orchestrates account creation
├── coingecko.py                 # All browser automation (register, login, get key)
├── temp_email.py                # Disposable mailbox via mail.tm API
├── storage.py                   # SQLite persistence (accounts.db + bulk tables)
├── captcha_solver.py            # CapSolver / 2captcha integration (optional, for main.py flow)
├── api_demo.py                  # CLI demo of CoinGecko API endpoints
├── accounts.db                  # SQLite database (auto-created on first run)
├── requirements.txt             # Python dependencies
├── scripts/
│   └── discover_cg_registration.py  # Standalone registration analysis tool
├── dashboard/
│   ├── app.py                   # Flask web dashboard + all API routes
│   ├── bulk_register.py         # Browser pool worker logic (headless Camoufox, no CAPTCHA key needed)
│   └── templates/
│       ├── index.html           # Main dashboard (API keys, passwords, usage)
│       ├── bulk_register.html   # Bulk registration stress-test page
│       ├── search.html          # Coin search page
│       └── backtest.html        # Strategy backtesting page (Buy & Hold, SMA, RSI)
├── logs/
│   └── bulk_run_<id>.txt        # Per-run step logs (auto-created)
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
| [CapSolver](https://capsolver.com) | CAPTCHA solving (optional, for `main.py` only) | Paid |
| [2captcha](https://2captcha.com) | CAPTCHA solving (optional, for `main.py` only) | Paid |

---

## Important Notes

- `main.py` runs the browser **headful** (visible window) by default. Bulk registration uses **headless** Camoufox — this is intentional since bulk runs don't need to be observed.
- `accounts.db` and `logs/` may contain generated emails, passwords, and API keys — treat them as secret files and do not commit them to public repositories.
- Each CoinGecko demo API key has a **10,000 calls/month** free limit. Usage is tracked locally and can be synced from CoinGecko's `GET /key` endpoint via the dashboard's sync button or the `↻` row button. If `GET /key` returns 401/403 (Pro subscription required), the dashboard falls back to local counts only.
- All account creation and key generation scripts call **live external services**. Only run them when you have explicit authorization to do so.
- **No CAPTCHA costs for bulk registration**: The Camoufox browser fingerprint is clean enough to auto-pass Cloudflare Turnstile and hCaptcha without a paid CAPTCHA service. `captcha_solver.py` is only used by `main.py` (single-account flow) and is optional there too.
