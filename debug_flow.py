"""
Step-by-step debug script — saves a screenshot + HTML at every stage so we
can see the exact DOM structure and fix selectors in coingecko.py.
"""
import time, os, re
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

OUT = "debug_screenshots"
os.makedirs(OUT, exist_ok=True)

SIGNUP_URL = "https://www.coingecko.com/en/users/sign_up"
API_DASH   = "https://www.coingecko.com/en/developers/dashboard"

EMAIL    = "PUT_YOUR_TEMP_EMAIL_HERE@mail.tm"   # fill in a real temp email
PASSWORD = "TestPassword123!"


def snap(page, name):
    path = f"{OUT}/{name}.png"
    html = f"{OUT}/{name}.html"
    page.screenshot(path=path, full_page=True)
    with open(html, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"  [snap] {name}")


def dump_inputs(page):
    """Print every input/button/select visible on the page."""
    print("  --- inputs ---")
    for el in page.locator("input, button, select, textarea, [role='button']").all():
        try:
            tag  = el.evaluate("e => e.tagName")
            typ  = el.get_attribute("type") or ""
            name = el.get_attribute("name") or ""
            id_  = el.get_attribute("id") or ""
            cls  = (el.get_attribute("class") or "")[:60]
            txt  = (el.inner_text() or "")[:60].strip()
            ph   = el.get_attribute("placeholder") or ""
            print(f"    <{tag} type={typ!r} name={name!r} id={id_!r} placeholder={ph!r}> text={txt!r}")
        except Exception:
            pass
    print("  --- end inputs ---")


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=500)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(15_000)

        # ── STEP 1: load signup page ─────────────────────────────────────────
        print("\n[1] Loading signup page...")
        page.goto(SIGNUP_URL, wait_until="load", timeout=60_000)
        time.sleep(2)
        snap(page, "01_signup_loaded")
        dump_inputs(page)

        # dismiss cookie / consent banner
        for txt in ["Accept all", "Accept All", "Accept", "Agree", "Got it"]:
            try:
                page.get_by_role("button", name=re.compile(txt, re.I)).first.click(timeout=2000)
                print(f"  Dismissed cookie banner ({txt!r})")
                time.sleep(1)
                break
            except PWTimeout:
                pass

        # ── STEP 2: click the main "Sign up" / "Continue with Email" button ─
        print("\n[2] Looking for 'Continue with Email' or 'Sign up' ...")
        snap(page, "02_before_email_btn")
        dump_inputs(page)

        clicked = False
        for pattern in [
            r"continue with email",
            r"sign up with email",
            r"use email",
            r"email",
        ]:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if btn.count() > 0:
                    btn.click(timeout=5000)
                    clicked = True
                    print(f"  Clicked button matching {pattern!r}")
                    time.sleep(1.5)
                    break
            except PWTimeout:
                pass

        if not clicked:
            # maybe we're already on an email form — try filling it directly
            print("  No 'continue with email' button found — trying email field directly")

        snap(page, "03_after_email_btn")
        dump_inputs(page)

        # ── STEP 3: fill email ───────────────────────────────────────────────
        print("\n[3] Filling email field...")
        email_locators = [
            page.locator('input[type="email"]'),
            page.locator('input[name="user[email]"]'),
            page.locator('input[placeholder*="email" i]'),
            page.locator('input[id*="email" i]'),
        ]
        filled = False
        for loc in email_locators:
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.fill(EMAIL)
                    filled = True
                    print(f"  Filled email via {loc}")
                    break
            except Exception:
                pass

        if not filled:
            print("  ERROR: could not find email input!")

        snap(page, "04_email_filled")

        # ── STEP 4: click Continue / Next ───────────────────────────────────
        print("\n[4] Clicking Continue / Next...")
        for pattern in [r"continue", r"next", r"sign up", r"register"]:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=5000)
                    print(f"  Clicked {pattern!r}")
                    time.sleep(2)
                    break
            except PWTimeout:
                pass

        # also try submit input
        try:
            page.locator('input[type="submit"]').first.click(timeout=3000)
        except PWTimeout:
            pass

        page.wait_for_load_state("load", timeout=30_000)
        time.sleep(2)
        snap(page, "05_after_continue")
        dump_inputs(page)

        # ── STEP 5: password fields ──────────────────────────────────────────
        print("\n[5] Filling password fields (if any)...")
        for pw_field in page.locator('input[type="password"]').all():
            try:
                pw_field.fill(PASSWORD)
                print("  Filled password field")
            except Exception:
                pass

        snap(page, "06_password_filled")
        dump_inputs(page)

        # ── STEP 6: ToS / checkbox ───────────────────────────────────────────
        for chk in page.locator('input[type="checkbox"]').all():
            try:
                if not chk.is_checked():
                    chk.check()
                    print("  Checked a checkbox")
            except Exception:
                pass

        # ── STEP 7: final submit ─────────────────────────────────────────────
        print("\n[7] Final submit...")
        for pattern in [r"sign up", r"register", r"create account", r"continue", r"submit"]:
            try:
                btn = page.get_by_role("button", name=re.compile(pattern, re.I)).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=5000)
                    print(f"  Clicked submit: {pattern!r}")
                    time.sleep(3)
                    break
            except PWTimeout:
                pass

        page.wait_for_load_state("load", timeout=30_000)
        snap(page, "07_after_submit")
        print("\n[done] Screenshots saved to debug_screenshots/")
        print("  Check 01_ through 07_ images to see the actual flow.")
        print("  Press Ctrl+C or close the browser when done.")

        # keep browser open for manual inspection
        time.sleep(30)
        browser.close()


if __name__ == "__main__":
    main()
