# CoinGecko API Key Automation — How It Works

This project automates the creation of CoinGecko demo accounts and API keys using disposable email addresses, browser automation, and CAPTCHA solving. It then exposes those keys through a local Flask dashboard.

---

## Architecture Overview

```
main.py  ──►  temp_email.py   (create disposable mailbox via mail.tm)
         ──►  coingecko.py    (browser automation via Camoufox/Playwright)
         ──►  storage.py      (persist to SQLite accounts.db)

dashboard/app.py  ──►  storage.py   (read accounts)
                  ──►  CoinGecko API (proxy live price/market data)

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

Creates and manages `accounts.db` in the project root on import.

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
| `/` | GET | Shows all accounts, highlights the active key |
| `/create` | POST | Launches `main.py --count 1` as a subprocess |
| `/pin/<id>` | POST | Pins an account as the active key |
| `/unpin` | POST | Unpins all; reverts to oldest-with-calls logic |
| `/delete/<id>` | POST | Removes an account from the database |
| `/api/prices` | GET | Proxies CoinGecko `/simple/price` (BTC, ETH, SOL, BNB, XRP, ADA) |
| `/api/global` | GET | Proxies CoinGecko `/global` market stats |
| `/api/trending` | GET | Proxies CoinGecko `/search/trending` |
| `/api/markets` | GET | Proxies CoinGecko `/coins/markets` (top 10 by market cap) |
| `/api/debug-key` | GET | Returns locally-tracked usage for the active key |

Each API proxy call automatically increments `calls_used` / decrements `calls_left` in the DB for real-time feedback. Returns HTTP 429 passthrough if CoinGecko rate-limits.

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

---

## File Structure

```
qa api/
├── main.py              # Entry point — orchestrates account creation
├── coingecko.py         # All browser automation (register, login, get key)
├── temp_email.py        # Disposable mailbox via mail.tm API
├── storage.py           # SQLite persistence (accounts.db)
├── captcha_solver.py    # CapSolver / 2captcha integration
├── api_demo.py          # CLI demo of CoinGecko API endpoints
├── accounts.db          # SQLite database (auto-created on first run)
├── requirements.txt     # Python dependencies
├── dashboard/
│   ├── app.py           # Flask web dashboard
│   └── templates/       # HTML templates
├── debug_flow.py        # Debug script with screenshots
├── debug_api_dash.py    # Debug script for API dashboard flow
├── run_one.py           # Full run with screenshots (needs playwright-stealth)
└── get_sitekey.py       # Sitekey scraper (needs playwright-stealth)
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

- The browser runs **headful** (visible window) by default so flows can be observed. Do not make it headless unless Cloudflare/hCaptcha issues are resolved.
- `accounts.db` may contain generated emails, passwords, and API keys — treat it as a secret file and do not commit it to public repositories.
- Each CoinGecko demo API key has a **10,000 calls/month** free limit. Usage is tracked locally since the `/key` status endpoint requires a PRO subscription.
- All account creation and key generation scripts call **live external services**. Only run them when you have explicit authorization to do so.
