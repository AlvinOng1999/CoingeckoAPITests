from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import re, time

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, slow_mo=80,
        args=["--disable-blink-features=AutomationControlled"])
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    Stealth().apply_stealth_sync(page)

    page.goto("https://www.coingecko.com/", wait_until="load", timeout=60000)
    time.sleep(2)

    for txt in ["Accept all", "Accept All", "Accept"]:
        try:
            b = page.get_by_role("button", name=re.compile(f"^{txt}$", re.I))
            if b.count() > 0:
                b.first.click(timeout=2000)
                break
        except Exception:
            pass

    page.get_by_role("button", name=re.compile(r"^sign up$", re.I)).first.click(timeout=10000)
    time.sleep(2)
    for btn in page.get_by_role("button", name=re.compile("continue with email", re.I)).all():
        if btn.is_visible():
            btn.click()
            break
    time.sleep(3)

    # Search HTML for sitekey
    html = page.content()
    for pat in [r'data-sitekey=["\']([^"\']+)', r'"sitekey"\s*:\s*"([^"]+)', r'sitekey=([0-9a-zA-Z_-]{20,})']:
        hits = re.findall(pat, html, re.I)
        if hits:
            print("sitekey:", hits)

    # Turnstile iframes
    for frame in page.frames:
        if "turnstile" in frame.url or "cloudflare" in frame.url:
            print("Turnstile frame:", frame.url[:300])
            # sitekey is in the iframe URL
            m = re.search(r"sitekey=([^&]+)", frame.url)
            if m:
                print("SITEKEY FROM URL:", m.group(1))

    # data-sitekey elements
    for el in page.locator("[data-sitekey]").all():
        try:
            print("data-sitekey element:", el.get_attribute("data-sitekey"))
        except Exception:
            pass

    browser.close()
