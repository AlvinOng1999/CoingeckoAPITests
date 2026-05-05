# Copilot instructions for this repository

## Commands

- Install Python dependencies: `python -m pip install -r requirements.txt`
- Install the Playwright browser used by the automation: `python -m playwright install chromium`
- Run the main account/API-key flow with one browser session: `python main.py --count 1`
- Start the Flask dashboard at `http://localhost:5000`: `python dashboard\app.py`
- Exercise an existing saved API key against CoinGecko endpoints: `python api_demo.py`
- Run browser/debug flows that save screenshots and HTML:
  - `python debug_flow.py`
  - `python debug_api_dash.py <email> <password>`
  - `python run_one.py`
- `run_one.py` and `get_sitekey.py` import `playwright_stealth`, which is not listed in `requirements.txt`; install it with `python -m pip install playwright-stealth` before using those scripts.
- There is no configured test or lint runner. For syntax validation across the project, run `python -m py_compile main.py coingecko.py storage.py temp_email.py api_demo.py debug_flow.py debug_api_dash.py run_one.py get_sitekey.py dashboard\app.py`.
- For a single-file validation while iterating, run `python -m py_compile <path-to-file.py>`.
- For an import smoke check after dependency setup, run `python -c "import storage, temp_email, coingecko, main; print('imports OK')"`.

## Architecture

- This is a Python proof-of-concept for CoinGecko account/API-key workflow testing. It combines `mail.tm` disposable mailboxes, synchronous Playwright browser automation, SQLite persistence, and a small Flask dashboard.
- `main.py` is the primary orchestrator: it creates a mailbox through `temp_email.py`, drives registration/email confirmation/API-key retrieval through `coingecko.py`, then stores the result through `storage.py`.
- `coingecko.py` contains the reusable CoinGecko browser-flow helpers, constants, selectors, timeouts, Turnstile waiting, login, registration, API dashboard form filling, and API-key extraction. Prefer centralizing reusable selector fixes here rather than copying them into scripts.
- `temp_email.py` owns all `mail.tm` API calls: domain discovery, mailbox creation, token retrieval, inbox polling, and verification-link extraction.
- `storage.py` owns the root-level SQLite database path (`accounts.db`), initializes the `accounts` table on import, and provides the only persistence helpers used by the CLI, dashboard, and API demo.
- `dashboard\app.py` is a Flask UI over `storage.py`. It reads saved accounts, refreshes usage via CoinGecko `/api/v3/key`, and starts `main.py --count 1` in a subprocess from the `/create` route.
- `api_demo.py` uses the newest saved account from `accounts.db` to call CoinGecko demo endpoints with the `x-cg-demo-api-key` header.
- `debug_flow.py`, `debug_api_dash.py`, `run_one.py`, and `get_sitekey.py` are diagnostic scripts for DOM/selector investigation. They intentionally run visible browsers and write artifacts to `debug_screenshots\`, `run_screenshots\`, or `debug_api_dash.png`.

## Key conventions

- Browser automation uses Playwright's synchronous API and mostly headful Chromium (`headless=False`) so flows can be observed and debugged. Avoid converting a single script to async Playwright unless the whole call chain is updated.
- Selectors target CoinGecko's auth modal, not the page body; email inputs commonly use `input[name='user[email]']` or `.gecko-modal input[type='email']`.
- Several flows depend on realistic browser context values such as desktop Chrome user agent, viewport, locale, and timezone. Keep those values consistent when changing browser setup.
- The automation waits for Cloudflare Turnstile by checking `input[name='cf-turnstile-response']`, but continues after timeout in some flows. Preserve that behavior unless changing the flow intentionally.
- `accounts.db` contains generated emails, passwords, API keys, usage counters, and timestamps. Treat it and generated screenshots/HTML as local artifacts that may contain secrets or session data.
- The dashboard imports root modules by inserting the repository root into `sys.path`; keep dashboard code aware that shared modules live one directory up.
- Usage counters assume CoinGecko's free monthly limit defaults to `10000` if the `/key` response omits `rate_limit_request_per_month`.
- Debug scripts prefer saving screenshots/HTML at each step before exiting on selector or flow failures; follow that pattern when adding new diagnostics.
- The scripts call live external services (`mail.tm`, CoinGecko, Cloudflare-protected pages). Do not run account-creation or key-generation flows unless the user has explicitly requested it and confirms authorization.
