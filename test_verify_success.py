"""
Verify real success state: what changes in DOM after form submission.
Saves HTML before+after to logs/subscribe_debug/ for comparison.
"""
import time, os
from camoufox.sync_api import Camoufox

URL = "https://newsletter.coingecko.com/landing/api_updates_subscribe"
OUT = os.path.join(os.path.dirname(__file__), "logs", "subscribe_debug")
os.makedirs(OUT, exist_ok=True)

def js_fill(page, sel, val):
    return page.evaluate("""([s, v]) => {
        const inp = document.querySelector(s);
        if (!inp) return false;
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, v);
        inp.dispatchEvent(new Event('input', {bubbles: true}));
        return true;
    }""", [sel, val])

def page_state(page):
    """Snapshot key state: form visibility, visible text, page title."""
    return page.evaluate("""() => {
        const emailInp = document.querySelector("input[name='email']");
        const nameInp  = document.querySelector("input[name='name']");
        const btn      = document.querySelector("button[type='submit']");
        const bodyText = document.body.innerText.replace(/[^\\x00-\\x7F]/g, '?').slice(0, 600);
        return {
            emailVisible: !!(emailInp && emailInp.offsetParent !== null),
            nameVisible:  !!(nameInp  && nameInp.offsetParent  !== null),
            btnVisible:   !!(btn      && btn.offsetParent      !== null),
            btnDisabled:  !!(btn      && btn.disabled),
            bodyText:     bodyText,
            title:        document.title,
        };
    }""")

with Camoufox(headless=True, geoip=True) as browser:
    page = browser.new_page()
    page.goto(URL, wait_until="load", timeout=30000)
    time.sleep(2)

    pre = page_state(page)
    print("=== PRE-SUBMIT STATE ===")
    for k, v in pre.items():
        print(f"  {k}: {v!r}")

    page.screenshot(path=os.path.join(OUT, "verify_01_pre.png"), full_page=True)
    with open(os.path.join(OUT, "verify_01_pre.html"), "w", encoding="utf-8", errors="replace") as f:
        f.write(page.content())

    # Fill and submit
    js_fill(page, "input[name='name']", "Crypto User")
    js_fill(page, "input[name='email']", "verify_real_check@wshu.net")
    page.evaluate("document.querySelector(\"button[type='submit']\").click()")
    print("\nSubmit clicked. Polling every 2s for up to 40s...")

    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(2)
        post = page_state(page)
        changed = {k: post[k] for k in post if post[k] != pre[k]}
        print(f"  t={round(40-(deadline-time.time()),1)}s | changed: {list(changed.keys())} | emailVisible={post['emailVisible']} | btnVisible={post['btnVisible']}")
        if not post['emailVisible']:
            print("  => Form HIDDEN after submit = REAL SUCCESS!")
            break
    else:
        print("  => Timeout — form still visible after 40s")

    page.screenshot(path=os.path.join(OUT, "verify_02_post.png"), full_page=True)
    with open(os.path.join(OUT, "verify_02_post.html"), "w", encoding="utf-8", errors="replace") as f:
        f.write(page.content())

    print("\n=== POST-SUBMIT STATE ===")
    for k, v in post.items():
        print(f"  {k}: {v!r}")

print("\nCheck logs/subscribe_debug/verify_*.png + verify_*.html for comparison")
