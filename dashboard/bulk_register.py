"""
Bulk account registration workers for Mode B (HTTP) and Mode C (Browser).
Both modes expose a generator that yields SSE-ready JSON strings.
"""
import os
import sys
import json
import time
import threading
import re
import requests as _req

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

    while True:
        count = target_count if not run_forever else 50  # 50 per loop iteration

        futures_map = {}
        pool = ThreadPoolExecutor(max_workers=max_workers)
        try:
            for _ in range(count):
                if stop_event.is_set():
                    break
                f = pool.submit(_mode_c_worker, run_id, verify_email, stop_event)
                futures_map[f] = True

            for future in as_completed(futures_map):
                if stop_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                email, _pw, status, err = future.result()
                if status == "stopped":
                    continue
                elif status in ("verified", "unverified"):
                    done += 1
                    storage.increment_bulk_run_counts(run_id, created=1)
                else:
                    errors += 1
                    storage.increment_bulk_run_counts(run_id, failed=1)

                total_display = None if run_forever else target_count
                yield _make_event(run_id, done, total_display, email, status, errors)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if stop_event.is_set() or not run_forever:
            break

    storage.update_bulk_run_status(run_id, "done" if not stop_event.is_set() else "stopped")
    yield _make_event(run_id, done, target_count, "", "complete", errors, complete=True)


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
        pool = ThreadPoolExecutor(max_workers=max_workers)
        try:
            for _ in range(count):
                if stop_event.is_set():
                    break
                f = pool.submit(_mode_b_worker, run_id, verify_email, stop_event)
                futures_map[f] = True

            for future in as_completed(futures_map):
                if stop_event.is_set():
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                email, _pw, status, err = future.result()
                if "rate" in err.lower() or "429" in err:
                    rate_limited += 1
                if status == "stopped":
                    continue
                elif status in ("verified", "unverified"):
                    done += 1
                    storage.increment_bulk_run_counts(run_id, created=1)
                else:
                    errors += 1
                    storage.increment_bulk_run_counts(run_id, failed=1)

                total_display = None if run_forever else target_count
                yield _make_event(run_id, done, total_display, email, status,
                                  errors, rate_limited)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if stop_event.is_set() or not run_forever:
            break

    storage.update_bulk_run_status(run_id, "done" if not stop_event.is_set() else "stopped")
    yield _make_event(run_id, done, target_count, "", "complete", errors,
                      rate_limited, complete=True)
