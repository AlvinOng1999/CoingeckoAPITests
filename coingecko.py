import re
import time
from playwright.sync_api import Page, TimeoutError as PWTimeout
import captcha_solver

HOMEPAGE       = "https://www.coingecko.com/"
SIGNIN_URL     = "https://www.coingecko.com/en/users/sign_in"
API_DASH       = "https://www.coingecko.com/en/developers/dashboard"
API_PRICING    = "https://www.coingecko.com/en/api/pricing"
NEWSLETTER_URL = "https://newsletter.coingecko.com/landing/api_updates_subscribe"

_NEWSLETTER_SUCCESS_KWS = [
    "success", "thank you", "thanks", "check your email",
    "subscribed", "you're in", "you are in", "welcome", "confirmed",
]

NAV_TIMEOUT  = 60_000
ELEM_TIMEOUT = 20_000


def _wait_cloudflare(page: Page, timeout: int = 30):
    """Block until the Cloudflare 'Verifying you are human' challenge clears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            content = page.content()
            url = page.url
        except Exception:
            time.sleep(1)
            continue
        cf_challenge = (
            "Verifying you are human" in content
            or "challenge" in url
            or "cf_chl" in url
        )
        if not cf_challenge:
            return
        print("  [cloudflare] challenge detected — waiting …")
        time.sleep(2)
    print("  [cloudflare] challenge did not clear in time — proceeding anyway")


def _goto(page: Page, url: str):
    page.goto(url, wait_until="load", timeout=NAV_TIMEOUT)
    _wait_cloudflare(page)
    time.sleep(0.5)


def _dismiss_cookie_banner(page: Page):
    for text in ["Accept all", "Accept All", "Accept Cookies", "Accept"]:
        try:
            btn = page.get_by_role("button", name=re.compile(f"^{text}$", re.I))
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                time.sleep(0.5)
                return
        except PWTimeout:
            pass


def _solve_and_inject_turnstile(page: Page, page_url: str):
    """
    Solve Cloudflare Turnstile and inject the token into the page.

    Strategy (in order):
      1. Use CAPTCHA solving service if CAPTCHA_API_KEY is set.
      2. Wait up to 20 s for Turnstile to auto-solve (works when fingerprint is clean).
      3. Proceed anyway (submit with force-click).
    """
    # ── Try service solver first ──────────────────────────────────────────────
    try:
        token = captcha_solver.solve_turnstile(page_url)
    except Exception as e:
        print(f"  [captcha] solver error: {e} — falling back to auto-solve wait")
        token = None

    if token:
        _inject_token(page, token)
        return

    # ── Fall back: wait for browser auto-solve (up to 20 s) ──────────────────
    print("  [captcha] no API key — waiting up to 20 s for Turnstile auto-solve …")
    deadline = time.time() + 20
    while time.time() < deadline:
        for el in page.locator("input[name='cf-turnstile-response']").all():
            try:
                if (el.get_attribute("value") or "").strip():
                    print("  [captcha] auto-solved ✓")
                    return
            except Exception:
                pass
        time.sleep(1)
    print("  [captcha] Turnstile not solved — submitting anyway")


def _inject_token(page: Page, token: str):
    """Write token into the hidden Turnstile input and trigger the site callback."""
    page.evaluate(
        """token => {
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            if (inp) { inp.value = token; }
            if (typeof turnstileCallback === 'function') { turnstileCallback(token); }
        }""",
        token,
    )


def _inject_hcaptcha_token(page: Page, token: str):
    """Replicate captcha controller's _enableFormSubmitButton to set captchaVerified."""
    page.evaluate(
        """token => {
            const captchaDiv = document.querySelector('#sign-up-captcha');
            if (captchaDiv) {
                const old = captchaDiv.querySelector('input[name="response_token"]');
                if (old) old.remove();
                const inp = document.createElement('input');
                inp.type = 'hidden';
                inp.name = 'response_token';
                inp.value = token;
                captchaDiv.appendChild(inp);
            }
            window.captchaVerified = true;
            const pwField = document.querySelector('[data-auth-target="signUpPassword"]');
            if (pwField) pwField.dispatchEvent(new Event('input', {bubbles: true}));
        }""",
        token,
    )


def _click_hcaptcha_checkbox(page: Page) -> bool:
    """Click the hCaptcha checkbox iframe and wait for captchaVerified — same idea as Turnstile auto-solve."""
    print("  [hcaptcha] waiting for checkbox iframe …")
    deadline = time.time() + 20
    checkbox_frame = None
    while time.time() < deadline:
        for frame in page.frames:
            url = frame.url or ""
            if "hcaptcha.com" in url and "checkbox" in url:
                checkbox_frame = frame
                break
        if checkbox_frame:
            break
        time.sleep(0.5)

    if not checkbox_frame:
        frame_urls = [f.url for f in page.frames if f.url and f.url != "about:blank"]
        print(f"  [hcaptcha] checkbox iframe not found. Active frames: {frame_urls}")
        return False

    try:
        cb = checkbox_frame.locator("#checkbox")
        cb.wait_for(state="visible", timeout=5000)
        cb.click(timeout=5000)
        print("  [hcaptcha] checkbox clicked — waiting for auto-verify …")
    except Exception as e:
        print(f"  [hcaptcha] checkbox click failed: {e}")
        return False

    # Poll for captchaVerified (set by CoinGecko's captcha controller callback).
    # If a challenge appeared, the user can solve it manually in the visible browser.
    print("  [hcaptcha] waiting up to 30 s for captchaVerified (solve challenge in browser if needed) …")
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if page.evaluate("() => window.captchaVerified === true"):
                print("  [hcaptcha] captchaVerified ✓")
                return True
        except Exception:
            pass
        time.sleep(1)

    print("  [hcaptcha] captchaVerified not set — submitting anyway")
    return False


def _scroll_and_click_button(page: Page, pattern: str, timeout: int = 15) -> bool:
    """
    Scroll the page until a button/link matching `pattern` is in view, then click it.
    Returns True if clicked, False if not found within timeout.
    Only clicks interactive elements (<button>, <a>, role="button") to avoid
    accidentally clicking decorative text nodes that match the pattern.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        rx = re.compile(pattern, re.I)
        for role in ("button", "link"):
            for el in page.get_by_role(role, name=rx).all():
                try:
                    el.scroll_into_view_if_needed(timeout=2000)
                    time.sleep(0.3)
                    if el.is_visible() and el.is_enabled():
                        el.click(timeout=3000)
                        return True
                except Exception:
                    pass
        # Text fallback — only <a> tags or elements with an explicit role="button"
        # (never span/div which are often decorative section headers)
        for el in page.get_by_text(rx).all():
            try:
                tag = el.evaluate("e => e.tagName").lower()
                role_attr = (el.get_attribute("role") or "").lower()
                if tag == "a" or role_attr == "button":
                    el.scroll_into_view_if_needed(timeout=2000)
                    time.sleep(0.3)
                    if el.is_visible():
                        el.click(timeout=3000)
                        return True
            except Exception:
                pass
        # Progressive scroll down to reveal more content
        page.evaluate("window.scrollBy(0, 500)")
        time.sleep(0.5)
    return False


def _click_pricing_free_cta(page: Page, timeout: int = 20) -> bool:
    """Pure JS click — no Playwright role lookup, single DOM pass per scroll tick."""
    KWS = ['create free account', 'get started for free', 'get started free',
           'create demo account', 'get started', 'sign up']

    page.evaluate("window.scrollTo(0, 0)")
    deadline = time.time() + timeout

    while time.time() < deadline:
        clicked = page.evaluate("""
            (kws) => {
                const els = [...document.querySelectorAll('button, a[href], [role="button"]')];
                for (const kw of kws) {
                    const el = els.find(e => {
                        const t = e.textContent.trim().toLowerCase();
                        const r = e.getBoundingClientRect();
                        return t.includes(kw) && r.height > 10 && e.offsetParent !== null;
                    });
                    if (el) {
                        el.scrollIntoView({behavior: 'instant', block: 'center'});
                        el.click();
                        return el.textContent.trim();
                    }
                }
                return null;
            }
        """, KWS)

        if clicked:
            print(f"  [pricing] clicked: {clicked!r}")
            return True

        page.evaluate("window.scrollBy(0, 400)")
        time.sleep(0.2)

    return False


def _first_visible(page: Page, selectors: list[str], timeout=ELEM_TIMEOUT):
    """Return the first visible locator from a list of CSS selectors."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                pass
        time.sleep(0.5)
    raise TimeoutError(f"None of {selectors} became visible within {timeout}ms")


def _click_visible_button(page: Page, pattern: str, timeout=8000):
    """Click the first visible+enabled button whose text matches `pattern`."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for btn in page.get_by_role("button", name=re.compile(pattern, re.I)).all():
            try:
                if btn.is_visible() and btn.is_enabled():
                    btn.click(timeout=3000)
                    return
            except Exception:
                pass
        time.sleep(0.5)
    raise TimeoutError(f"Button matching '{pattern}' not found or not clickable")


# ─────────────────────────────────────────────────────────────────────────────

def _click_signup_entry(page: Page):
    """Click whichever Sign Up / Get Started button is visible in the header."""
    patterns = [r"^sign up$", r"^get started$", r"^create account$",
                r"sign up", r"get started for free", r"create free account"]
    deadline = time.time() + ELEM_TIMEOUT / 1000
    while time.time() < deadline:
        for pat in patterns:
            rx = re.compile(pat, re.I)
            for role in ("button", "link"):
                for el in page.get_by_role(role, name=rx).all():
                    try:
                        if el.is_visible() and el.is_enabled():
                            el.click(timeout=3000)
                            return
                    except Exception:
                        pass
        time.sleep(0.5)
    raise TimeoutError("Could not find a Sign Up entry point on the page")


def register(page: Page, email: str, password: str):
    """Open the homepage, click Sign up, fill email, solve Turnstile, submit."""
    _goto(page, HOMEPAGE)
    _dismiss_cookie_banner(page)

    # Open auth modal — try multiple button/link text variants
    _click_signup_entry(page)
    time.sleep(0.8)

    # Switch to email form
    _click_visible_button(page, "continue with email", timeout=ELEM_TIMEOUT)
    time.sleep(0.8)

    # Fill email field
    email_field = _first_visible(page, [
        "input[name='user[email]']",
        "input[placeholder*='email' i]",
    ])
    email_field.click()
    email_field.press_sequentially(email, delay=60)
    email_field.dispatch_event("input")

    time.sleep(1)  # let Turnstile iframe initialize
    _solve_and_inject_turnstile(page, HOMEPAGE)

    # Submit — enabled after token injection
    try:
        _click_visible_button(page, "continue with email", timeout=10_000)
    except TimeoutError:
        # Last resort: force-click the data-auth-target submit button
        for btn in page.locator("[data-auth-target='continueWithEmailButton']").all():
            try:
                if btn.is_visible():
                    btn.click(force=True, timeout=5000)
                    break
            except Exception:
                pass

    time.sleep(2)

    # New flow: modal shows a password-creation form after email submission.
    # The Sign up button is disabled until:
    #   1. The password field triggers a second Turnstile via focus->captcha#loadCaptcha
    #   2. The input event fires (input->auth#validate) enabling the button
    try:
        pw_field = _first_visible(page, [
            "input[data-auth-target='signUpPassword']",
            "input[autocomplete='new-password']",
        ], timeout=8_000)
    except TimeoutError:
        pw_field = None

    if pw_field:
        # Focus triggers hCaptcha load (captcha#loadCaptcha action)
        pw_field.click()
        time.sleep(1.5)  # let captcha#loadCaptcha fire before typing

        pw_field.press_sequentially(password, delay=40)
        pw_field.dispatch_event("input")

        time.sleep(2.5)   # let hCaptcha iframes finish rendering

        # Click hCaptcha checkbox — Camoufox clean fingerprint may auto-verify;
        # if a challenge appears, press Escape to dismiss it and proceed
        verified = _click_hcaptcha_checkbox(page)
        if not verified:
            # Challenge may be overlaying the page — dismiss it so submit is reachable
            try:
                page.keyboard.press("Escape")
                time.sleep(0.5)
            except Exception:
                pass

        # Wait up to 15 s for Sign up button to become enabled
        signup_btn = None
        deadline = time.time() + 15
        while time.time() < deadline:
            for btn in page.locator("[data-auth-target='signUpSubmit']").all():
                try:
                    if btn.is_visible() and btn.is_enabled():
                        signup_btn = btn
                        break
                except Exception:
                    pass
            if signup_btn:
                break
            time.sleep(0.5)

        if signup_btn:
            try:
                signup_btn.click(timeout=15_000)
            except PWTimeout:
                # Click registered but server returned inline error (no navigation) —
                # force-click to re-submit in case captchaVerified toggled since the wait
                print("  [register] click timed out waiting for navigation — force-clicking")
                for btn in page.locator("[data-auth-target='signUpSubmit']").all():
                    if btn.is_visible():
                        btn.click(force=True)
                        break
        else:
            # Force-click as last resort (button stays disabled when captchaVerified=false)
            for btn in page.locator("[data-auth-target='signUpSubmit']").all():
                if btn.is_visible():
                    btn.click(force=True)
                    break

        try:
            page.wait_for_load_state("load", timeout=NAV_TIMEOUT)
        except PWTimeout:
            pass  # no full navigation is fine — form may have submitted via AJAX
        time.sleep(2)


def confirm_email(page: Page, link: str, password: str):
    """Navigate to the email verification link; set password if prompted."""
    _goto(page, link)  # handles Cloudflare challenge
    time.sleep(3)

    pw_fields = [f for f in page.locator("input[type='password']").all()
                 if f.is_visible()]
    if pw_fields:
        for f in pw_fields:
            f.fill(password)
        for pattern in [r"confirm", r"set password", r"continue", r"save", r"submit"]:
            try:
                _click_visible_button(page, pattern, timeout=3000)
                break
            except TimeoutError:
                pass
        page.wait_for_load_state("load", timeout=NAV_TIMEOUT)
        time.sleep(2)


def login(page: Page, email: str, password: str):
    """Open the homepage, click Login, fill email → Continue → password → Login."""
    _goto(page, HOMEPAGE)
    _dismiss_cookie_banner(page)

    # Open auth modal via the header "Login" button
    _click_visible_button(page, r"^login$", timeout=ELEM_TIMEOUT)
    time.sleep(1.5)

    # The login modal may show email directly or require "Continue with email" first
    try:
        email_field = _first_visible(page, [
            "input[name='user[email]']",
            "input[placeholder*='email' i]",
        ], timeout=5_000)
    except TimeoutError:
        _click_visible_button(page, "continue with email", timeout=ELEM_TIMEOUT)
        time.sleep(1.5)
        email_field = _first_visible(page, [
            "input[name='user[email]']",
            "input[placeholder*='email' i]",
        ])

    email_field.click()
    email_field.fill(email)
    time.sleep(0.5)

    # "Continue with email" reveals the password field
    _click_visible_button(page, "continue with email", timeout=10_000)
    time.sleep(2)

    pw_field = _first_visible(page, [
        "input[type='password']",
        "#user_password",
    ])
    pw_field.fill(password)

    _click_visible_button(page, r"^login$", timeout=8000)
    page.wait_for_load_state("load", timeout=NAV_TIMEOUT)
    time.sleep(2)


def _subscribe_snap(page: Page, name: str):
    """Save a full-page debug screenshot to logs/subscribe_debug/."""
    try:
        import os as _os
        d = _os.path.join(_os.path.dirname(__file__), "logs", "subscribe_debug")
        _os.makedirs(d, exist_ok=True)
        page.screenshot(path=_os.path.join(d, f"{name}.png"), full_page=True)
    except Exception:
        pass


def _js_fill(page: Page, selector: str, value: str) -> bool:
    """Fill an input via JS native setter — bypasses label/overlay click interception."""
    return page.evaluate("""([sel, val]) => {
        const inp = document.querySelector(sel);
        if (!inp) return false;
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles: true}));
        inp.dispatchEvent(new Event('change', {bubbles: true}));
        inp.dispatchEvent(new Event('blur',   {bubbles: true}));
        return true;
    }""", [selector, value])


def subscribe_newsletter(page: Page, email: str, password: str) -> bool:
    """
    Subscribe email to CoinGecko newsletter via newsletter.coingecko.com.
    No login required.
    Confirmed form: input[name='name'], input[name='email'], button[type='submit'].
    Label divs intercept clicks — fields are filled via JS native setter.
    """
    print(f"  [subscribe] loading {NEWSLETTER_URL}", flush=True)
    page.goto(NEWSLETTER_URL, wait_until="load", timeout=30_000)
    _wait_cloudflare(page)
    _dismiss_cookie_banner(page)
    _subscribe_snap(page, "01_loaded")
    time.sleep(2)

    # ── Fill name via JS (label overlay intercepts normal clicks) ─────────────
    filled_name = _js_fill(page, "input[name='name']", "Crypto User")
    print(f"  [subscribe] name filled={filled_name}", flush=True)

    # ── Fill email via JS ─────────────────────────────────────────────────────
    filled_email = _js_fill(page, "input[name='email']", email)
    print(f"  [subscribe] email filled={filled_email}", flush=True)
    if not filled_email:
        print("  [subscribe] ERROR: email field not found", flush=True)
        _subscribe_snap(page, "02_no_email_field")
        return False

    _subscribe_snap(page, "02_form_filled")

    # ── Click Submit — button[type='submit'] confirmed present ────────────────
    submitted = page.evaluate("""() => {
        const btn = document.querySelector("button[type='submit']");
        if (btn && btn.offsetParent !== null) {
            btn.scrollIntoView({behavior:'instant', block:'center'});
            btn.click();
            return btn.textContent.trim() || 'submit';
        }
        return null;
    }""")
    print(f"  [subscribe] submit clicked: {submitted!r}", flush=True)
    if not submitted:
        print("  [subscribe] no submit button found", flush=True)
        _subscribe_snap(page, "03_no_button")
        return False

    _subscribe_snap(page, "03_after_submit")

    # ── Poll for real success: email input disappears from DOM ────────────────
    # (keywords in initial HTML are false positives — form hiding is unambiguous)
    print("  [subscribe] polling for form to disappear (up to 30 s)...", flush=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        email_visible = page.evaluate("""() => {
            const inp = document.querySelector("input[name='email']");
            return !!(inp && inp.offsetParent !== null);
        }""")
        if not email_visible:
            _subscribe_snap(page, "04_success")
            print("  [subscribe] form hidden — subscription confirmed!", flush=True)
            return True
        # If a Turnstile appeared after submit, wait for auto-solve then re-click
        for el in page.locator("input[name='cf-turnstile-response']").all():
            try:
                if (el.get_attribute("value") or "").strip():
                    print("  [subscribe] Turnstile solved — re-submitting", flush=True)
                    page.evaluate("""() => {
                        const btn = document.querySelector("button[type='submit']");
                        if (btn) btn.click();
                    }""")
                    time.sleep(2)
                    break
            except Exception:
                pass
        time.sleep(2)

    _subscribe_snap(page, "04_timeout")
    visible = page.evaluate("() => document.body.innerText.replace(/[^\\x00-\\x7F]/g,'?').slice(0,200)")
    print(f"  [subscribe] timeout — visible text: {visible!r}", flush=True)
    return False


def _random_company_name() -> str:
    import random, string
    words = ["Alpha", "Beta", "Nexus", "Apex", "Orbit", "Nova", "Flux", "Zinc",
             "Vega", "Axon", "Echo", "Grid", "Kova", "Luma", "Myra", "Nion"]
    suffix = ["Labs", "Analytics", "Research", "Group", "Works", "Studio", "Tech"]
    return f"{random.choice(words)} {random.choice(suffix)}"




def _is_logged_in(page: Page) -> bool:
    """Return True if the current page shows a logged-in state (no Login button visible)."""
    try:
        btn = page.get_by_role("button", name=re.compile(r"^login$", re.I))
        return btn.count() == 0 or not btn.first.is_visible()
    except Exception:
        return True


def _scan_for_api_key(page: Page) -> str | None:
    """Scan the current page for a CoinGecko API key pattern. Returns key or None."""
    # Check input field values first (most reliable)
    for sel in ["input[readonly]", "input[type='text']", "input"]:
        for el in page.locator(sel).all():
            try:
                val = el.get_attribute("value") or ""
                if re.match(r"CG-[A-Za-z0-9]{15,}", val):
                    return val.strip()
            except Exception:
                continue

    # Check visible text elements
    for sel in ["code", "pre", "span", "p", "td", "li", "div"]:
        for el in page.locator(sel).all():
            try:
                txt = el.inner_text(timeout=300)
                m = re.search(r"CG-[A-Za-z0-9]{15,}", txt)
                if m:
                    return m.group(0)
            except Exception:
                continue

    # Last resort: raw HTML
    m = re.search(r"CG-[A-Za-z0-9]{15,}", page.content())
    return m.group(0) if m else None


def _fill_input_after_label(page, label_text: str, value: str) -> bool:
    """Find an input immediately following a label with the given text, then fill it."""
    # Try label element association
    for label in page.get_by_text(re.compile(label_text, re.I)).all():
        try:
            # Get the associated input via for= attribute
            for_ = label.get_attribute("for")
            if for_:
                inp = page.locator(f"#{for_}")
                if inp.count() > 0:
                    inp.scroll_into_view_if_needed(timeout=2000)
                    inp.fill(value)
                    return True
            # Try sibling/child input
            parent = label.locator("xpath=..")
            inp = parent.locator("input, textarea").first
            if inp.count() > 0:
                inp.scroll_into_view_if_needed(timeout=2000)
                inp.fill(value)
                return True
            # Try next sibling via evaluate
            inp_handle = label.evaluate_handle(
                "el => el.nextElementSibling && el.nextElementSibling.matches('input,textarea') "
                "? el.nextElementSibling : el.parentElement.querySelector('input,textarea')"
            )
            if inp_handle:
                inp_loc = page.locator("input, textarea").filter(has=page.locator(":scope")).first
                # Fallback: just use evaluate to fill
                label.evaluate(
                    """(el, val) => {
                        const inp = el.nextElementSibling || el.parentElement.querySelector('input,textarea');
                        if (inp) inp.value = val;
                    }""",
                    value,
                )
                return True
        except Exception:
            pass
    return False


def _wait_for_modal(page: Page, timeout: int = 25) -> bool:
    """Wait for the Demo Account modal/form to appear. Uses one JS sweep per tick."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = page.evaluate("""
                () => {
                    // Dialog/modal by role or class
                    const dlg = document.querySelector(
                        "[role='dialog'],[role='modal'],.modal,[class*='modal'],[class*='dialog']"
                    );
                    if (dlg && dlg.offsetParent !== null) {
                        const r = dlg.getBoundingClientRect();
                        if (r.height > 50) return 'dialog';
                    }
                    // Form heading keywords
                    const hEls = [...document.querySelectorAll('h1,h2,h3,h4,h5')];
                    const hKws = ['demo account', 'api key', 'coingecko api'];
                    if (hEls.some(el => hKws.some(k => el.textContent.toLowerCase().includes(k))
                                     && el.offsetParent !== null)) return 'heading';
                    // ≥2 visible text inputs = form rendered
                    const inputs = [...document.querySelectorAll(
                        "input[type='text'],input[type='number'],input:not([type='checkbox'])"
                        + ":not([type='radio']):not([type='hidden'])"
                    )].filter(el => el.offsetParent !== null);
                    if (inputs.length >= 2) return 'form';
                    return null;
                }
            """)
            if result:
                print(f"  [modal] detected via {result}")
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _modal_scroll(page: Page, amount: int = 300):
    """Scroll inside the modal/dialog container by `amount` pixels."""
    for sel in ["[role='dialog']", ".modal-body", ".modal-content", "[class*='modal']"]:
        try:
            container = page.locator(sel).first
            if container.count() > 0 and container.is_visible():
                container.evaluate(f"el => el.scrollTop += {amount}")
                time.sleep(0.2)
                return
        except Exception:
            pass
    page.evaluate(f"window.scrollBy(0, {amount})")
    time.sleep(0.2)


def _fill_demo_account_modal(page: Page):
    print("  [modal] filling Demo Account form …")
    company = _random_company_name()

    time.sleep(0.3)

    # Scroll modal to top
    page.evaluate("""() => {
        for (const sel of ["[role='dialog'] .modal-body","[role='dialog']",".modal-body","[class*='modal-body']"]) {
            const el = document.querySelector(sel);
            if (el) { el.scrollTop = 0; return; }
        }
    }""")

    # Fill text inputs via JS with React-compatible native setter
    page.evaluate("""(vals) => {
        const inputs = [...document.querySelectorAll(
            "input[type='text'],input[type='number']," +
            "input:not([type='checkbox']):not([type='radio']):not([type='hidden'])"
        )].filter(el => el.offsetParent !== null);
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        vals.forEach((v,i) => {
            if (i >= inputs.length) return;
            inputs[i].scrollIntoView({behavior:'instant',block:'center'});
            setter.call(inputs[i], v);
            inputs[i].dispatchEvent(new Event('input',{bubbles:true}));
            inputs[i].dispatchEvent(new Event('change',{bubbles:true}));
        });
    }""", [company, "5", "Developer"])
    print(f"  [modal] company: {company!r}")
    print("  [modal] team size: 5")
    print("  [modal] role: Developer")

    def _click_radio_js(index: int, group_name: str):
        page.evaluate("""(idx) => {
            const radios = [...document.querySelectorAll("input[type='radio']")]
                .filter(el => el.offsetParent !== null);
            if (idx < radios.length) {
                radios[idx].scrollIntoView({behavior:'instant',block:'center'});
                radios[idx].click();
                radios[idx].dispatchEvent(new Event('change',{bubbles:true}));
            }
        }""", index)
        print(f"  [modal] {group_name}: radio[{index}] clicked ✓")

    def _scroll_modal_js(amount: int):
        page.evaluate("""(amt) => {
            const modal = document.querySelector(
                "[role='dialog'] .modal-body,[role='dialog'],.modal-body,[class*='modal-body']"
            );
            if (modal) modal.scrollTop += amt; else window.scrollBy(0, amt);
        }""", amount)

    _scroll_modal_js(200)
    time.sleep(0.15)
    _click_radio_js(2, "use-case (Research)")
    time.sleep(0.15)

    _scroll_modal_js(300)
    time.sleep(0.2)
    _click_radio_js(6, "referral (Word of mouth)")
    time.sleep(0.15)

    # Fill textarea
    _scroll_modal_js(200)
    page.evaluate("""(text) => {
        const ta = [...document.querySelectorAll("textarea")].find(el => el.offsetParent !== null);
        if (!ta) return;
        ta.scrollIntoView({behavior:'instant',block:'center'});
        const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
        setter.call(ta, text);
        ta.dispatchEvent(new Event('input',{bubbles:true}));
        ta.dispatchEvent(new Event('change',{bubbles:true}));
    }""", "Using CoinGecko API for QA testing and research to validate cryptocurrency data accuracy across different endpoints.")
    print("  [modal] elaboration filled")

    # Tick all unchecked checkboxes
    _scroll_modal_js(150)
    time.sleep(0.15)
    n_checked = page.evaluate("""() => {
        const cbs = [...document.querySelectorAll("input[type='checkbox']")]
            .filter(el => el.offsetParent !== null && !el.checked);
        cbs.forEach(cb => { cb.scrollIntoView({behavior:'instant',block:'center'}); cb.click(); });
        return cbs.length;
    }""")
    for _ in range(n_checked):
        print("  [modal] checkbox ticked")

    time.sleep(0.2)
    print("  [modal] form fully filled — clicking Create Demo Account …")

    # ── Step 8: Click "Create Demo Account" via JS ────────────────────────────
    submitted = page.evaluate("""() => {
        const pats = [/create demo account/i,/create.*demo/i,/create.*account/i,/proceed/i,/submit/i,/confirm/i];
        const btns = [...document.querySelectorAll("button,[role='button']")].filter(el => el.offsetParent !== null);
        for (const pat of pats) {
            const btn = btns.find(b => pat.test(b.textContent.trim()));
            if (btn) {
                btn.scrollIntoView({behavior:'instant',block:'center'});
                const txt = btn.textContent.trim();
                btn.click();
                return txt;
            }
        }
        return null;
    }""")
    if submitted:
        print(f"  [modal] clicked: {submitted!r}")
    else:
        for btn in page.locator("[role='dialog'] button, form button").all():
            try:
                txt = (btn.inner_text(timeout=500) or "").strip()
                if btn.is_visible() and btn.is_enabled() and txt and "×" not in txt:
                    btn.click(timeout=3000)
                    print(f"  [modal] fallback submit: {txt!r}")
                    break
            except Exception:
                pass

    # Poll for navigation away from pricing (up to 4s), then look for Create API Key
    print(f"  [modal] after Create Demo Account, url={page.url}")
    deadline = time.time() + 4
    while time.time() < deadline:
        if "developers/dashboard" in page.url:
            break
        time.sleep(0.3)

    # ── Step 9: Click "Create API Key" — poll every 0.3s, up to 8s ──────────
    print("  [modal] waiting for Create API Key button …")
    deadline = time.time() + 8
    while time.time() < deadline:
        clicked = page.evaluate("""() => {
            const pats = [/create.*api.*key/i,/add.*api.*key/i,/generate.*key/i,/get.*api.*key/i,/create.*key/i];
            const els = [...document.querySelectorAll("button,[role='button'],a")]
                .filter(el => el.offsetParent !== null);
            for (const pat of pats) {
                const el = els.find(e => pat.test(e.textContent.trim()));
                if (el) {
                    el.scrollIntoView({behavior:'instant',block:'center'});
                    const txt = el.textContent.trim();
                    el.click();
                    return txt;
                }
            }
            return null;
        }""")
        if clicked:
            print(f"  [modal] clicked: {clicked!r}")
            time.sleep(0.5)
            return
        time.sleep(0.3)


def _ensure_api_key_form_submitted(page: Page, timeout: int = 30):
    """
    Wait for and fill the API key application form (modal or inline).
    The CoinGecko Demo Account modal has labeled inputs with NO placeholder text.
    """
    print("  [api-form] waiting for form/modal …")

    if _wait_for_modal(page, timeout=timeout):
        _fill_demo_account_modal(page)
        return True
    print("  [api-form] no modal/form detected — skipping fill")
    return False


def get_api_key(page: Page, email: str, password: str) -> str:
    """Retrieve the CoinGecko Demo API key for the logged-in account."""

    # ── 1. Go straight to developer dashboard ───────────────────────────────
    print("  [api-key] navigating to developer dashboard …")
    page.goto(API_DASH, wait_until="load", timeout=NAV_TIMEOUT)
    _wait_cloudflare(page)
    time.sleep(0.5)
    print(f"  [api-key] landed on: {page.url}")

    # Re-login if redirected to auth
    if "sign_in" in page.url or "sign_up" in page.url:
        print("  [api-key] redirected to auth — logging in …")
        login(page, email, password)
        page.goto(API_DASH, wait_until="load", timeout=NAV_TIMEOUT)
        _wait_cloudflare(page)
        time.sleep(1.0)
        print(f"  [api-key] after login, landed on: {page.url}")

    # ── 2. If dashboard redirected to pricing, skip straight to CTA ─────────
    if "pricing" in page.url:
        print("  [api-key] dashboard → pricing redirect detected — skipping to CTA …")
    else:
        # Actually on the dashboard — check for existing key or onboarding modal
        print("  [api-key] on dashboard — scanning for existing key …")
        key = _scan_for_api_key(page)
        if key:
            return key

        print("  [api-key] no key yet — checking for onboarding modal …")
        if _wait_for_modal(page, timeout=2):
            _fill_demo_account_modal(page)

        for _ in range(3):
            key = _scan_for_api_key(page)
            if key:
                return key
            time.sleep(1)

        print("  [api-key] no key on dashboard — navigating to pricing CTA …")
        page.goto(API_PRICING, wait_until="load", timeout=NAV_TIMEOUT)
        _wait_cloudflare(page)
        time.sleep(0.5)
        print(f"  [api-key] pricing page loaded: {page.url}")

    cta_clicked = _click_pricing_free_cta(page, timeout=20)
    print(f"  [api-key] CTA {'clicked' if cta_clicked else 'not found'}, url={page.url}")

    _wait_cloudflare(page)
    if "sign_in" in page.url or "sign_up" in page.url:
        print("  [api-key] CTA navigated to auth page — logging in again …")
        login(page, email, password)
        page.goto(API_DASH, wait_until="load", timeout=NAV_TIMEOUT)
        _wait_cloudflare(page)

    # Poll for modal or key — exit as soon as either appears (up to 6s)
    print("  [api-key] waiting for modal or key after CTA …")
    deadline = time.time() + 6
    while time.time() < deadline:
        key = _scan_for_api_key(page)
        if key:
            return key
        if _wait_for_modal(page, timeout=0.3):
            _fill_demo_account_modal(page)
            break
        time.sleep(0.3)

    _wait_cloudflare(page)

    # Poll for key on current page (up to 5s)
    print("  [api-key] scanning for key after modal …")
    deadline = time.time() + 5
    while time.time() < deadline:
        key = _scan_for_api_key(page)
        if key:
            return key
        time.sleep(0.3)

    # Navigate to dashboard as final fallback
    page.goto(API_DASH, wait_until="load", timeout=NAV_TIMEOUT)
    _wait_cloudflare(page)
    print(f"  [api-key] final dashboard url={page.url}")

    # Poll for key + click any "Create API Key" button (up to 10s)
    _CREATE_KEY_JS = """() => {
        const pats = [/create.*api.*key/i, /add.*api.*key/i, /generate.*key/i, /create.*key/i];
        const els = [...document.querySelectorAll("button,a,[role='button']")]
            .filter(el => el.offsetParent !== null);
        for (const pat of pats) {
            const el = els.find(e => pat.test(e.textContent.trim()));
            if (el) {
                el.scrollIntoView({behavior:'instant',block:'center'});
                const txt = el.textContent.trim();
                el.click();
                return txt;
            }
        }
        return null;
    }"""
    deadline = time.time() + 10
    last_click = 0
    while time.time() < deadline:
        key = _scan_for_api_key(page)
        if key:
            return key
        if time.time() - last_click > 1.5:
            clicked = page.evaluate(_CREATE_KEY_JS)
            if clicked:
                print(f"  [api-key] clicked dashboard button: {clicked!r}")
                last_click = time.time()
        time.sleep(0.3)

    page.screenshot(path="debug_api_dash.png", full_page=True)
    raise RuntimeError("API key not found — see debug_api_dash.png")
