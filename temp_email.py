import re
import time
import random
import string
import threading
import requests
from requests.exceptions import ConnectionError as _ConnErr, Timeout as _Timeout

BASE = "https://api.mail.tm"

# Cache the domain so concurrent workers don't all hit /domains simultaneously
_domain_cache: str | None = None
_domain_cache_ts: float = 0.0
_domain_cache_ttl: float = 300.0
_domain_lock = threading.Lock()


def _random_string(length=12):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _strong_password(length=14):
    """Generate a password meeting CoinGecko's requirements: upper, lower, digit, special."""
    upper   = random.choices(string.ascii_uppercase, k=2)
    lower   = random.choices(string.ascii_lowercase, k=6)
    digits  = random.choices(string.digits, k=3)
    special = random.choices("!@#$%&*", k=3)
    chars   = upper + lower + digits + special
    random.shuffle(chars)
    return "".join(chars)


def _get_domain() -> str:
    global _domain_cache, _domain_cache_ts
    with _domain_lock:
        if _domain_cache and (time.time() - _domain_cache_ts) < _domain_cache_ttl:
            return _domain_cache
        r = requests.get(f"{BASE}/domains", timeout=10)
        r.raise_for_status()
        domains = r.json().get("hydra:member", [])
        if not domains:
            raise RuntimeError("No domains available from mail.tm")
        _domain_cache = domains[0]["domain"]
        _domain_cache_ts = time.time()
        return _domain_cache


def create_mailbox(max_retries: int = 4) -> dict:
    """Create a new disposable mailbox. Retries with backoff on rate-limit or network errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            domain = _get_domain()
            address = f"{_random_string()}@{domain}"
            password = _random_string(16)
            r = requests.post(
                f"{BASE}/accounts",
                json={"address": address, "password": password},
                timeout=10,
            )
            r.raise_for_status()
            token = get_token(address, password)
            cg_password = _strong_password()
            return {"address": address, "password": password, "cg_password": cg_password, "token": token}
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            code = exc.response.status_code if exc.response is not None else 0
            if code in (429, 502, 503, 504):
                # Rate-limited or server overload — back off exponentially
                backoff = (2 ** attempt) * random.uniform(5, 12)
                time.sleep(backoff)
            else:
                raise
        except (_ConnErr, _Timeout) as exc:
            last_exc = exc
            time.sleep(random.uniform(3, 8) * (attempt + 1))
    raise last_exc or RuntimeError("Failed to create mailbox after retries")


def get_token(address, password):
    """Authenticate and return a Bearer token for inbox access."""
    r = requests.post(f"{BASE}/token", json={"address": address, "password": password}, timeout=10)
    r.raise_for_status()
    return r.json()["token"]


def poll_inbox(token, timeout=180, interval=5):
    """Poll until a CoinGecko email arrives. Returns the full message body (HTML).
    Retries with backoff on connection errors so concurrent workers don't cascade-fail."""
    headers = {"Authorization": f"Bearer {token}"}
    # Jitter: stagger workers so they don't all hit mail.tm at the same instant
    time.sleep(random.uniform(0, 3))
    deadline = time.time() + timeout
    backoff = interval
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE}/messages", headers=headers, timeout=15)
            r.raise_for_status()
            backoff = interval  # reset on success
            messages = r.json().get("hydra:member", [])
            for msg in messages:
                if "coingecko" in msg.get("from", {}).get("address", "").lower() or \
                   "coingecko" in msg.get("subject", "").lower():
                    msg_id = msg["id"]
                    detail = requests.get(f"{BASE}/messages/{msg_id}", headers=headers, timeout=15)
                    detail.raise_for_status()
                    return detail.json().get("html", detail.json().get("text", ""))
        except (_ConnErr, _Timeout):
            # mail.tm is overloaded — back off and retry rather than crashing
            backoff = min(backoff * 2, 30)
        time.sleep(backoff)
    raise TimeoutError(f"No CoinGecko email received within {timeout}s")


def extract_verification_link(body):
    """Extract the email confirmation URL from the email body."""
    import html as _html
    if isinstance(body, list):
        body = " ".join(body)
    pattern = r'https://[^\s"\'<>]*coingecko\.com[^\s"\'<>]*confirm[^\s"\'<>]*'
    matches = re.findall(pattern, body, re.IGNORECASE)
    if matches:
        return _html.unescape(matches[0])
    # Broader fallback
    pattern2 = r'https://[^\s"\'<>]*coingecko\.com/en/users/confirmation[^\s"\'<>]*'
    matches2 = re.findall(pattern2, body, re.IGNORECASE)
    if matches2:
        return _html.unescape(matches2[0])
    raise ValueError("Could not find verification link in email body")
