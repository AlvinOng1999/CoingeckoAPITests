"""
Debug the API key creation form on the developer dashboard.
Usage: python debug_api_dash.py <email> <password>
"""
import sys, re, time, os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

EMAIL    = sys.argv[1] if len(sys.argv) > 1 else input("Email: ")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else input("Password: ")

SIGNIN_URL = "https://www.coingecko.com/en/users/sign_in"
API_DASH   = "https://www.coingecko.com/en/developers/dashboard"
OUT = "debug_screenshots"
os.makedirs(OUT, exist_ok=True)


def snap(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
    with open(f"{OUT}/{name}.html", "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"  [snap] {name}")


def dump_inputs(page):
    print("  --- visible interactive elements ---")
    for el in page.locator("input, button, select, textarea").all():
        try:
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName")
            typ = el.get_attribute("type") or ""
            name = el.get_attribute("name") or ""
            id_ = el.get_attribute("id") or ""
            ph = el.get_attribute("placeholder") or ""
            val = el.get_attribute("value") or ""
            txt = el.inner_text(timeout=300).strip()[:80]
            print(f"    <{tag} type={typ!r} name={name!r} id={id_!r} placeholder={ph!r} value={val[:30]!r}> text={txt!r}")
        except Exception:
            pass
    print("  --- end ---")


def first_visible(page, selectors, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                pass
        time.sleep(0.5)
    return None


def click_visible_btn(page, pattern, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for btn in page.get_by_role("button", name=re.compile(pattern, re.I)).all():
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=3000)
                    print(f"  Clicked: {pattern!r}")
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    print(f"  WARNING: button '{pattern}' not found")
    return False


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, slow_mo=200)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900},
    )
    page = ctx.new_page()
    page.set_default_timeout(20_000)

    # ── Step 1: Navigate to sign-in URL (auto-opens modal) ───────────────────
    print(f"\n[1] Navigating to {SIGNIN_URL} ...")
    page.goto(SIGNIN_URL, wait_until="load", timeout=60_000)
    time.sleep(2)
    snap(page, "s01_page_loaded")

    # Dismiss cookie banner
    for txt in ["Accept all", "Accept All", "Accept"]:
        try:
            page.get_by_role("button", name=re.compile(f"^{txt}$", re.I)).first.click(timeout=2000)
            print(f"  Dismissed cookie banner")
            time.sleep(1)
            break
        except PWTimeout:
            pass

    snap(page, "s02_after_cookie_dismiss")
    dump_inputs(page)

    # ── Step 2: Fill email ───────────────────────────────────────────────────
    print(f"\n[2] Looking for visible email field ...")
    email_field = first_visible(page, [
        "input[type='email'][placeholder*='email' i]",
        "input[type='email']",
        "#user_email",
    ], timeout=20)

    if email_field:
        email_field.click()
        email_field.fill(EMAIL)
        print(f"  Filled email: {EMAIL}")
        snap(page, "s03_email_filled")
    else:
        print("  ERROR: email field not found. Dumping visible buttons:")
        dump_inputs(page)
        snap(page, "s03_email_not_found")
        print("  Waiting 30s for manual inspection...")
        time.sleep(30)
        browser.close()
        sys.exit(1)

    # ── Step 3: Click "Continue with email" ──────────────────────────────────
    print("\n[3] Clicking 'Continue with email' ...")
    click_visible_btn(page, "continue with email", timeout=10)
    time.sleep(2)
    snap(page, "s04_after_continue_email")
    dump_inputs(page)

    # ── Step 4: Fill password ────────────────────────────────────────────────
    print("\n[4] Looking for password field ...")
    pw_field = first_visible(page, ["input[type='password']", "#user_password"], timeout=15)
    if pw_field:
        pw_field.fill(PASSWORD)
        print("  Filled password")
        snap(page, "s05_password_filled")
    else:
        print("  ERROR: password field not found")
        dump_inputs(page)
        snap(page, "s05_pw_not_found")

    # ── Step 5: Submit login ─────────────────────────────────────────────────
    print("\n[5] Submitting login ...")
    click_visible_btn(page, r"^login$", timeout=8)
    page.wait_for_load_state("load", timeout=60_000)
    time.sleep(2)
    snap(page, "s06_after_login")
    print(f"  URL after login: {page.url}")

    # ── Step 6: Developer dashboard ──────────────────────────────────────────
    print(f"\n[6] Navigating to API dashboard ...")
    page.goto(API_DASH, wait_until="load", timeout=60_000)
    time.sleep(3)
    snap(page, "s07_api_dashboard")
    print(f"  URL: {page.url}")

    print("\n  Visible text on page:")
    for line in page.locator("body").inner_text(timeout=5000).split("\n"):
        line = line.strip()
        if line:
            print(f"    {line}")

    dump_inputs(page)

    print("\n[done] Keeping browser open 60s — inspect the API key form manually.")
    time.sleep(60)
    browser.close()
    print("Screenshots in debug_screenshots/s01_* through s07_*")
