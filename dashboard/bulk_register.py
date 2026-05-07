"""
Bulk account registration workers using Camoufox browser pool (Mode C).
Exposes a generator that yields SSE-ready JSON strings.
"""
import os
import sys
import json
import time
import random
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import storage

_STOP_EVENTS: dict[int, threading.Event] = {}

# Limit concurrent mailbox creations so we don't burst mail.tm's API
_MAILBOX_SEM = threading.Semaphore(2)


def start_run(mode: str, target_count: int, verify_email: bool) -> int:
    run_id = storage.create_bulk_run(mode, target_count, False, verify_email)
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


def _clean_error(exc: Exception) -> str:
    """Return a single-line summary, stripping Playwright's verbose Call log block."""
    msg = str(exc)
    for marker in (
        "=========================== logs ===",
        "\n  Call log:\n",
        "\nCall log:\n",
    ):
        idx = msg.find(marker)
        if idx != -1:
            msg = msg[:idx]
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    return lines[0] if lines else msg


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _make_event(run_id, done, total, email, status, errors, complete=False) -> str:
    return _sse({
        "run_id": run_id,
        "done": done,
        "total": total,
        "email": email,
        "status": status,
        "errors": errors,
        "complete": complete,
    })


# ── Browser Pool ──────────────────────────────────────────────────────────────

def _worker(run_id: int, verify_email: bool, stop_event: threading.Event,
            stagger_delay: float = 0.0):
    """
    Creates one CoinGecko account using a real Camoufox browser.
    Retries the full mailbox + registration flow up to MAX_ATTEMPTS times.
    Returns (email, password, status, error_str).
    """
    from camoufox.sync_api import Camoufox
    import temp_email
    import coingecko

    if stagger_delay > 0 and not stop_event.is_set():
        time.sleep(stagger_delay)

    if stop_event.is_set():
        return "", "", "stopped", ""

    MAX_ATTEMPTS = 3
    last_err = ""
    final_email = ""
    final_password = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if stop_event.is_set():
            return "", "", "stopped", ""

        email = ""
        password = ""
        try:
            if attempt > 1:
                backoff = random.uniform(10, 20) * (attempt - 1)
                _step_log(run_id, "—",
                          f"Retrying (attempt {attempt}/{MAX_ATTEMPTS}) after {backoff:.0f}s backoff...")
                time.sleep(backoff)

            # ── Step 1: Fresh mailbox for every attempt ──────────────────────────
            _step_log(run_id, "—", "Creating disposable mailbox...")
            with _MAILBOX_SEM:
                mailbox = temp_email.create_mailbox()
            email = mailbox["address"]
            password = mailbox["cg_password"]
            final_email = email
            final_password = password

            # ── Step 2: Register ─────────────────────────────────────────────────
            _step_log(run_id, email, f"Launching browser (attempt {attempt}/{MAX_ATTEMPTS})...")
            with Camoufox(headless=True, geoip=True) as browser:
                page = browser.new_page()
                coingecko.register(page, email, password)
            _step_log(run_id, email, "Registration submitted — browser closed")

            # ── Step 3: Email verification (soft failure — no retry) ─────────────
            status = "unverified"
            if verify_email and not stop_event.is_set():
                try:
                    _step_log(run_id, email, "Polling inbox for verification email...")
                    body = temp_email.poll_inbox(mailbox["token"], timeout=180)
                    link = temp_email.extract_verification_link(body)
                    _step_log(run_id, email, "Verification link found — confirming email...")
                    with Camoufox(headless=True, geoip=True) as browser:
                        page = browser.new_page()
                        coingecko.confirm_email(page, link, password)
                    status = "verified"
                    _step_log(run_id, email, "Email confirmed")
                except Exception as verify_exc:
                    status = "unverified"
                    _step_log(run_id, email,
                              f"Verification skipped (saved as unverified): {_clean_error(verify_exc)}")

            storage.save_bulk_account(run_id, email, password, status)
            _append_log(run_id, email, status)
            return email, password, status, ""

        except Exception as exc:
            last_err = _clean_error(exc)
            label = email or "—"
            _step_log(run_id, label, f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {last_err}")
            if attempt == MAX_ATTEMPTS:
                if email:
                    storage.save_bulk_account(run_id, email, password, "failed", last_err)
                _append_log(run_id, label, "failed", last_err)

    return final_email, final_password, "failed", last_err


def run_bulk(run_id: int, target_count: int, verify_email: bool, max_workers: int = 5):
    """SSE generator. Yields SSE-formatted JSON strings for target_count accounts."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    stop_event = _STOP_EVENTS.get(run_id, threading.Event())
    done = 0
    errors = 0

    yield _sse({"message": f"Initialising — launching {max_workers} browser workers..."})

    futures_map = {}
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        for i in range(target_count):
            if stop_event.is_set():
                break
            delay = random.uniform(0, max_workers * 3)
            f = pool.submit(_worker, run_id, verify_email, stop_event, delay)
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

            yield _make_event(run_id, done, target_count, email, status, errors)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    storage.update_bulk_run_status(run_id, "done" if not stop_event.is_set() else "stopped")
    yield _make_event(run_id, done, target_count, "", "complete", errors, complete=True)
