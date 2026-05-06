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


def _step_log(run_id: int, email: str, msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path = os.path.join(_log_dir(), f"bulk_run_{run_id}.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {email} > {msg}\n")


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
        _step_log(run_id, "—", "Creating disposable mailbox...")
        mailbox = temp_email.create_mailbox()
        email = mailbox["address"]
        password = mailbox["cg_password"]
        _step_log(run_id, email, "Mailbox ready — launching Camoufox browser...")

        with Camoufox(headless=True, geoip=True) as browser:
            page = browser.new_page()
            _step_log(run_id, email, "Browser launched — running registration flow...")
            coingecko.register(page, email, password)
            _step_log(run_id, email, "Registration form submitted")

            if verify_email and not stop_event.is_set():
                _step_log(run_id, email, "Polling inbox for verification email...")
                body = temp_email.poll_inbox(mailbox["token"], timeout=120)
                link = temp_email.extract_verification_link(body)
                _step_log(run_id, email, "Verification link found — confirming email...")
                coingecko.confirm_email(page, link, password)
                status = "verified"
                _step_log(run_id, email, "Email confirmed")
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


# ── Endpoint discovery (auto-runs once if registration_requests.json is missing) ──

_ENDPOINTS_FILE = os.path.join(os.path.dirname(__file__), "..", "registration_requests.json")
_ENDPOINTS_LOCK = threading.Lock()
_endpoints_cache = None  # type: dict | None


def _parse_endpoints_from_captured(captured: list) -> dict:
    from urllib.parse import parse_qs

    result = {
        "email_post_url": "https://www.coingecko.com/en/users",
        "password_post_url": "https://www.coingecko.com/en/users",
        "email_field": "user[email]",
        "password_field": "user[password]",
        "password_confirm_field": "user[password_confirmation]",
        "turnstile_field": "cf-turnstile-response",
        "hcaptcha_field": "response_token",
    }
    for req in captured:
        raw = req.get("post_data") or ""
        ct = req.get("content_type", "")
        url = req.get("url", "")
        try:
            if "application/json" in ct:
                body = json.loads(raw)
                keys = set(body.keys()) if isinstance(body, dict) else set()
            else:
                keys = set(parse_qs(raw, keep_blank_values=True).keys())
        except Exception:
            continue

        email_keys = [k for k in keys if "email" in k.lower() and "confirm" not in k.lower()]
        pass_keys = [k for k in keys if "password" in k.lower() and "confirm" not in k.lower()]
        pass_conf_keys = [k for k in keys if "password" in k.lower() and "confirm" in k.lower()]

        if email_keys:
            result["email_post_url"] = url
            result["email_field"] = email_keys[0]
            for k in keys:
                if "turnstile" in k.lower() or (k.startswith("cf-") and "response" in k.lower()):
                    result["turnstile_field"] = k

        if pass_keys:
            result["password_post_url"] = url
            result["password_field"] = pass_keys[0]
            if pass_conf_keys:
                result["password_confirm_field"] = pass_conf_keys[0]
            for k in keys:
                if ("response" in k.lower() or "token" in k.lower()) \
                        and "authenticity" not in k.lower() and "email" not in k.lower():
                    result["hcaptcha_field"] = k
    return result


def _discover_endpoints() -> dict:
    """Runs a real browser registration to capture endpoints. Called once, result cached in file."""
    from camoufox.sync_api import Camoufox
    import temp_email
    import coingecko

    captured: list = []
    mailbox = temp_email.create_mailbox()

    try:
        with Camoufox(headless=True, geoip=True) as browser:
            page = browser.new_page()

            def _on_req(r):
                if "coingecko.com" in r.url and r.method in ("POST", "PUT", "PATCH"):
                    captured.append({
                        "url": r.url,
                        "method": r.method,
                        "content_type": r.headers.get("content-type", ""),
                        "post_data": r.post_data,
                    })

            page.on("request", _on_req)
            coingecko.register(page, mailbox["address"], mailbox["cg_password"])
    except Exception:
        pass

    try:
        with open(_ENDPOINTS_FILE, "w", encoding="utf-8") as f:
            json.dump(captured, f, indent=2)
    except Exception:
        pass

    return _parse_endpoints_from_captured(captured)


def _load_endpoints() -> dict:
    """Return endpoint config, loading from registration_requests.json or auto-discovering."""
    global _endpoints_cache
    if _endpoints_cache is not None:
        return _endpoints_cache
    with _ENDPOINTS_LOCK:
        if _endpoints_cache is not None:
            return _endpoints_cache
        if os.path.exists(_ENDPOINTS_FILE):
            try:
                with open(_ENDPOINTS_FILE, encoding="utf-8") as f:
                    _endpoints_cache = _parse_endpoints_from_captured(json.load(f))
                return _endpoints_cache
            except Exception:
                pass
        _endpoints_cache = _discover_endpoints()
        return _endpoints_cache


# ── Mode B constants (fallback defaults — overridden by registration_requests.json) ──
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
        ep = _load_endpoints()
        _step_log(run_id, "—", "Creating disposable mailbox...")
        mailbox = temp_email.create_mailbox()
        email = mailbox["address"]
        password = mailbox["cg_password"]
        _step_log(run_id, email, "Mailbox ready")

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

        _step_log(run_id, email, "Loading signup page + extracting CSRF...")
        resp = session.get(SIGNUP_URL, timeout=15)
        resp.raise_for_status()
        csrf = _extract_csrf(resp.text)

        _step_log(run_id, email, "Solving Cloudflare Turnstile...")
        ts_token = captcha_solver.solve_turnstile(HOMEPAGE)
        if not ts_token:
            raise RuntimeError("Turnstile solve returned None — check CAPTCHA_API_KEY")
        _step_log(run_id, email, "Turnstile solved")

        _step_log(run_id, email, f"POSTing email step to {ep['email_post_url']}...")
        resp = session.post(
            ep["email_post_url"],
            data={
                "authenticity_token": csrf,
                ep["email_field"]: email,
                ep["turnstile_field"]: ts_token,
            },
            headers={
                "X-CSRF-Token": csrf,
                "Referer": SIGNUP_URL,
                "Origin": "https://www.coingecko.com",
            },
            timeout=15,
            allow_redirects=True,
        )
        _step_log(run_id, email, f"Email step → HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise RuntimeError("Rate limited by CoinGecko (429)")
        if resp.status_code not in (200, 201, 302, 422):
            raise RuntimeError(f"Email POST returned {resp.status_code}")

        new_csrf = _extract_csrf(resp.text)
        if new_csrf:
            csrf = new_csrf

        _step_log(run_id, email, "Solving hCaptcha...")
        hc_token = captcha_solver.solve_hcaptcha(HOMEPAGE)
        if not hc_token:
            raise RuntimeError("hCaptcha solve returned None — check CAPTCHA_API_KEY")
        _step_log(run_id, email, "hCaptcha solved")

        _step_log(run_id, email, f"POSTing password step to {ep['password_post_url']}...")
        resp = session.post(
            ep["password_post_url"],
            data={
                "authenticity_token": csrf,
                ep["password_field"]: password,
                ep["password_confirm_field"]: password,
                ep["hcaptcha_field"]: hc_token,
            },
            headers={
                "X-CSRF-Token": csrf,
                "Referer": SIGNUP_URL,
                "Origin": "https://www.coingecko.com",
            },
            timeout=15,
            allow_redirects=True,
        )
        _step_log(run_id, email, f"Password step → HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise RuntimeError("Rate limited by CoinGecko (429)")

        if verify_email and not stop_event.is_set():
            _step_log(run_id, email, "Polling inbox for verification email...")
            body = temp_email.poll_inbox(mailbox["token"], timeout=120)
            link = temp_email.extract_verification_link(body)
            _step_log(run_id, email, "Verification link found — clicking...")
            session.get(link, timeout=15)
            status = "verified"
            _step_log(run_id, email, "Verified")
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
