"""
CAPTCHA solver for CoinGecko automation.

Two CAPTCHAs in the signup flow:
  1. Cloudflare Turnstile (email step)  — sitekey 0x4AAAAAABkuQIhLBgY-YxvO
  2. hCaptcha (password step)           — sitekey d7b4358f-5390-46d4-a479-eb9a1fa28033

Configure via environment variables:
    CAPTCHA_SERVICE   = "capsolver" (default) | "2captcha"
    CAPTCHA_API_KEY   = your API key

CapSolver:  https://capsolver.com  (~$0.80/1000 Turnstile, ~$1/1000 hCaptcha)
2captcha:   https://2captcha.com   (~$2.99/1000 both)
"""

import os

TURNSTILE_SITEKEY = "0x4AAAAAABkuQIhLBgY-YxvO"
HCAPTCHA_SITEKEY  = "d7b4358f-5390-46d4-a479-eb9a1fa28033"


def _api_key() -> str:
    return os.environ.get("CAPTCHA_API_KEY", "").strip()


def _service() -> str:
    return os.environ.get("CAPTCHA_SERVICE", "capsolver").lower()


def solve_turnstile(page_url: str, sitekey: str = TURNSTILE_SITEKEY) -> str | None:
    """Return a Cloudflare Turnstile token, or None if no API key is configured."""
    key = _api_key()
    if not key:
        return None
    svc = _service()
    print(f"  [captcha] solving Turnstile via {svc} …")
    if svc == "capsolver":
        return _capsolver_turnstile(sitekey, page_url, key)
    elif svc == "2captcha":
        return _2captcha_turnstile(sitekey, page_url, key)
    raise ValueError(f"Unknown CAPTCHA_SERVICE: {svc!r}")


def solve_hcaptcha(page_url: str, sitekey: str = HCAPTCHA_SITEKEY) -> str | None:
    """Return an hCaptcha token, or None if no API key is configured."""
    key = _api_key()
    if not key:
        return None
    svc = _service()
    print(f"  [captcha] solving hCaptcha via {svc} …")
    if svc == "capsolver":
        return _capsolver_hcaptcha(sitekey, page_url, key)
    elif svc == "2captcha":
        return _2captcha_hcaptcha(sitekey, page_url, key)
    raise ValueError(f"Unknown CAPTCHA_SERVICE: {svc!r}")


# ── CapSolver ─────────────────────────────────────────────────────────────────

def _capsolver_turnstile(sitekey: str, page_url: str, key: str) -> str:
    import capsolver
    capsolver.api_key = key
    sol = capsolver.solve({"type": "AntiTurnstileTaskProxyLess",
                           "websiteURL": page_url, "websiteKey": sitekey})
    token = sol.get("token") or sol.get("code") or sol.get("value", "")
    if not token:
        raise RuntimeError(f"CapSolver Turnstile: no token in {sol}")
    print(f"  [captcha] Turnstile token ({token[:20]}…)")
    return token


def _capsolver_hcaptcha(sitekey: str, page_url: str, key: str) -> str:
    import capsolver
    capsolver.api_key = key
    sol = capsolver.solve({"type": "HCaptchaTaskProxyLess",
                           "websiteURL": page_url, "websiteKey": sitekey})
    token = sol.get("gRecaptchaResponse") or sol.get("token") or sol.get("code", "")
    if not token:
        raise RuntimeError(f"CapSolver hCaptcha: no token in {sol}")
    print(f"  [captcha] hCaptcha token ({token[:20]}…)")
    return token


# ── 2captcha ──────────────────────────────────────────────────────────────────

def _2captcha_turnstile(sitekey: str, page_url: str, key: str) -> str:
    from twocaptcha import TwoCaptcha
    sol = TwoCaptcha(key).turnstile(sitekey=sitekey, url=page_url)
    token = sol.get("code", "")
    if not token:
        raise RuntimeError(f"2captcha Turnstile: no code in {sol}")
    print(f"  [captcha] Turnstile token ({token[:20]}…)")
    return token


def _2captcha_hcaptcha(sitekey: str, page_url: str, key: str) -> str:
    from twocaptcha import TwoCaptcha
    sol = TwoCaptcha(key).hcaptcha(sitekey=sitekey, url=page_url)
    token = sol.get("code", "")
    if not token:
        raise RuntimeError(f"2captcha hCaptcha: no code in {sol}")
    print(f"  [captcha] hCaptcha token ({token[:20]}…)")
    return token
