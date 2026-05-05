"""
Full end-to-end run: creates ONE CoinGecko account and retrieves the API key.
Runs with a visible browser. Saves screenshots at every step.
"""
import re, time, os, sys
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth
import temp_email, storage

OUT = "run_screenshots"
os.makedirs(OUT, exist_ok=True)

HOMEPAGE  = "https://www.coingecko.com/"
SIGNIN_URL = "https://www.coingecko.com/en/users/sign_in"
API_DASH  = "https://www.coingecko.com/en/developers/dashboard"

step = 0
def snap(page, label):
    global step
    step += 1
    path = f"{OUT}/{step:02d}_{label}.png"
    page.screenshot(path=path, full_page=True)
    print(f"    [snap] {path}")

def log(msg): print(f"  >> {msg}")

# ── helpers ──────────────────────────────────────────────────────────────────

def dismiss_cookie(page):
    for txt in ["Accept all", "Accept All", "Accept"]:
        try:
            btn = page.get_by_role("button", name=re.compile(f"^{re.escape(txt)}$", re.I))
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                time.sleep(0.5)
                return
        except PWTimeout:
            pass

def wait_visible(page, selectors, timeout=20):
    """Return first visible element matching any selector, or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                pass
        time.sleep(0.4)
    return None

def click_visible_btn(page, pattern, timeout=15):
    """Click the first visible+enabled button whose label matches pattern."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for btn in page.get_by_role("button", name=re.compile(pattern, re.I)).all():
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=3000)
                    log(f"clicked '{pattern}'")
                    time.sleep(0.5)
                    return True
            except Exception:
                pass
        time.sleep(0.4)
    return False

def wait_turnstile(page, timeout=30):
    log(f"waiting up to {timeout}s for Cloudflare Turnstile …")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for el in page.locator("input[name='cf-turnstile-response']").all():
            try:
                if (el.get_attribute("value") or "").strip():
                    log("Turnstile auto-solved ✓")
                    return True
            except Exception:
                pass
        time.sleep(1)
    log("Turnstile did not solve in time — submitting anyway")
    return False

# ─────────────────────────────────────────────────────────────────────────────

print("\n=== STEP 1: create disposable mailbox ===")
mailbox  = temp_email.create_mailbox()
email    = mailbox["address"]
password = mailbox["password"]
token    = mailbox["token"]
log(f"email:    {email}")
log(f"password: {password}")

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=False,
        slow_mo=120,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    ctx = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
    )
    page = ctx.new_page()
    Stealth().apply_stealth_sync(page)
    page.set_default_timeout(20_000)

    # ── STEP 2: open signup modal ────────────────────────────────────────────
    print("\n=== STEP 2: open signup modal ===")
    page.goto(HOMEPAGE, wait_until="load", timeout=60_000)
    time.sleep(2)
    dismiss_cookie(page)
    snap(page, "homepage")

    # Click the "Sign up" button in the CoinGecko header
    if not click_visible_btn(page, r"^sign up$", timeout=10):
        snap(page, "ERROR_no_signup_btn")
        print("ERROR: could not find Sign up button")
        browser.close(); sys.exit(1)
    time.sleep(2)
    snap(page, "modal_opened")

    # ── STEP 3: click "Continue with email" inside the modal ─────────────────
    print("\n=== STEP 3: select email signup ===")
    if not click_visible_btn(page, r"continue with email", timeout=15):
        snap(page, "ERROR_no_continue_email")
        print("ERROR: 'Continue with email' button not visible")
        browser.close(); sys.exit(1)
    time.sleep(1.5)
    snap(page, "email_tab_selected")

    # ── STEP 4: fill email ───────────────────────────────────────────────────
    print("\n=== STEP 4: fill email ===")
    # Use name= attr to target the auth form field, not the newsletter field
    email_field = wait_visible(page, [
        "input[name='user[email]']",
        ".gecko-modal input[type='email']",
    ], timeout=15)

    if not email_field:
        snap(page, "ERROR_no_email_input")
        print("ERROR: email input not visible after clicking Continue with email")
        browser.close(); sys.exit(1)

    email_field.click()
    # press_sequentially fires native DOM input events so Stimulus enables the submit btn
    email_field.press_sequentially(email, delay=60)
    log(f"typed: {email}")
    # Manually dispatch input event as extra trigger for Stimulus
    email_field.dispatch_event("input")
    snap(page, "email_filled")

    # ── STEP 5: wait for Turnstile, then submit ──────────────────────────────
    print("\n=== STEP 5: Cloudflare Turnstile + submit ===")
    time.sleep(5)   # let Turnstile iframe load and (hopefully) auto-solve
    wait_turnstile(page, timeout=30)
    snap(page, "after_turnstile")

    # The submit button is data-auth-target="continueWithEmailButton"
    # Try clicking it — first normally, then with force if still disabled
    submitted = False
    for btn in page.locator("[data-auth-target='continueWithEmailButton']").all():
        try:
            if btn.is_visible():
                btn.click(force=True, timeout=5000)   # force bypasses disabled check
                submitted = True
                log("clicked submit (force)")
                break
        except Exception:
            pass
    if not submitted:
        # Last resort: press Enter in the email field
        email_field.press("Enter")
        log("submitted via Enter key")

    page.wait_for_load_state("load", timeout=60_000)
    time.sleep(3)
    snap(page, "after_register_submit")
    log(f"URL: {page.url}")

    # ── STEP 6: poll for verification email ──────────────────────────────────
    print("\n=== STEP 6: waiting for verification email (up to 3 min) ===")
    try:
        body = temp_email.poll_inbox(token, timeout=180)
        link = temp_email.extract_verification_link(body)
        log(f"link: {link[:80]}…")
    except Exception as e:
        snap(page, "ERROR_no_verify_email")
        print(f"ERROR: {e}")
        browser.close(); sys.exit(1)

    # ── STEP 7: confirm email ────────────────────────────────────────────────
    print("\n=== STEP 7: confirm email ===")
    page.goto(link, wait_until="load", timeout=60_000)
    time.sleep(3)
    snap(page, "after_email_confirm")
    log(f"URL: {page.url}")

    pw_fields = [f for f in page.locator("input[type='password']").all() if f.is_visible()]
    if pw_fields:
        log("password prompt found — filling")
        for f in pw_fields:
            f.fill(password)
        for pat in [r"confirm", r"save", r"set password", r"continue", r"submit"]:
            if click_visible_btn(page, pat, timeout=3):
                break
        page.wait_for_load_state("load", timeout=60_000)
        time.sleep(2)
        snap(page, "after_set_password")

    # ── STEP 8: login ────────────────────────────────────────────────────────
    print("\n=== STEP 8: login ===")
    page.goto(HOMEPAGE, wait_until="load", timeout=60_000)
    time.sleep(2)
    dismiss_cookie(page)

    # Click "Login" in header
    click_visible_btn(page, r"^login$", timeout=10)
    time.sleep(2)
    snap(page, "login_modal")

    # Click "Continue with email" to reveal email field
    click_visible_btn(page, r"continue with email", timeout=15)
    time.sleep(1.5)

    ef = wait_visible(page, [
        "input[name='user[email]']",
        ".gecko-modal input[type='email']",
    ], timeout=15)
    if ef:
        ef.click()
        ef.press_sequentially(email, delay=60)
        ef.dispatch_event("input")
        log("email typed")
    else:
        snap(page, "ERROR_login_no_email")
        print("ERROR: login email field not found")
        browser.close(); sys.exit(1)

    # Second "Continue with email" to reveal password
    click_visible_btn(page, r"continue with email", timeout=10)
    time.sleep(2)
    snap(page, "login_pw_step")

    pf = wait_visible(page, ["input[type='password']", "#user_password"], timeout=15)
    if pf:
        pf.fill(password)
        log("password filled")
    else:
        snap(page, "ERROR_no_pw_field")
        print("ERROR: password field not found")
        browser.close(); sys.exit(1)

    click_visible_btn(page, r"^login$", timeout=8)
    page.wait_for_load_state("load", timeout=60_000)
    time.sleep(3)
    snap(page, "after_login")
    log(f"URL: {page.url}")

    # ── STEP 9: API dashboard ────────────────────────────────────────────────
    print("\n=== STEP 9: API dashboard ===")
    page.goto(API_DASH, wait_until="load", timeout=60_000)
    time.sleep(4)
    snap(page, "api_dashboard")
    log(f"URL: {page.url}")

    # Print page text so we can see what form/state is shown
    body_text = page.locator("body").inner_text(timeout=5000)
    print("  Visible text:")
    for ln in body_text.split("\n"):
        ln = ln.strip()
        if ln: print(f"    {ln}")

    # Immediately scan for an API key
    api_key = None
    m = re.search(r"CG-[A-Za-z0-9_\-]{10,}", page.content())
    if m:
        api_key = m.group(0)
        log(f"API key found immediately: {api_key}")

    if not api_key:
        log("filling profile form to generate key …")

        # Company / project name
        for ph in [r"company", r"project", r"name", r"organisation"]:
            f = page.get_by_placeholder(re.compile(ph, re.I)).first
            if f.count() > 0 and f.is_visible():
                f.fill("Security Research")
                log("filled company/project field")
                break

        # Select dropdowns
        for sel_el in page.locator("select").all():
            if sel_el.is_visible():
                try: sel_el.select_option(index=1)
                except Exception: pass

        # Click first matching radio/chip option for team size, role, purpose
        for label in ["1-10", "Solo", "1", "Developer", "Engineer",
                      "Personal", "Research", "Other"]:
            for el in page.get_by_text(re.compile(f"^{re.escape(label)}$", re.I)).all():
                try:
                    if el.is_visible():
                        el.click(timeout=2000)
                        break
                except Exception:
                    pass

        time.sleep(1)
        snap(page, "api_form_filled")

        for pat in [r"generate", r"create.*key", r"add key", r"get.*key",
                    r"submit", r"confirm", r"continue"]:
            if click_visible_btn(page, pat, timeout=3):
                break

        page.wait_for_load_state("load", timeout=60_000)
        time.sleep(3)
        snap(page, "api_after_submit")

        for _ in range(3):
            m = re.search(r"CG-[A-Za-z0-9_\-]{10,}", page.content())
            if m:
                api_key = m.group(0)
                break
            for sel in ["input[readonly]", "input[type='text']", "code", "pre"]:
                for el in page.locator(sel).all():
                    try:
                        val = el.get_attribute("value") or el.inner_text(timeout=400)
                        mm = re.search(r"CG-[A-Za-z0-9_\-]{10,}", val)
                        if mm:
                            api_key = mm.group(0)
                            break
                    except Exception:
                        pass
                if api_key: break
            if api_key: break
            time.sleep(3)

    # ── Result ────────────────────────────────────────────────────────────────
    if api_key:
        print(f"\n{'='*50}")
        print(f"  SUCCESS")
        print(f"  email:   {email}")
        print(f"  api_key: {api_key}")
        print(f"{'='*50}")
        storage.save_account(email, password, api_key)
        log("saved to accounts.db ✓")
    else:
        snap(page, "ERROR_no_api_key")
        print("\n  FAILED — API key not found")
        print("  Share the 'Visible text' printed above and the screenshots.")

    time.sleep(3)
    browser.close()
