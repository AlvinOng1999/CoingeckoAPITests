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
