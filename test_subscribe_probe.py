"""
Probe the CoinGecko newsletter page with a real Camoufox browser.
Dumps HTML, all input/button details, and screenshots to logs/subscribe_debug/.
Run: venv\Scripts\python.exe test_subscribe_probe.py
"""
import os, time, json
from camoufox.sync_api import Camoufox

OUT = os.path.join(os.path.dirname(__file__), "logs", "subscribe_debug")
os.makedirs(OUT, exist_ok=True)

URLS = [
    "https://newsletter.coingecko.com/landing/api_updates_subscribe",
    "https://landing.coingecko.com/daily-newsletter/",
    "https://www.coingecko.com/",
]

def probe(page, url, tag):
    print(f"\n{'='*60}")
    print(f"[probe] navigating to {url}")
    try:
        page.goto(url, wait_until="load", timeout=30_000)
    except Exception as e:
        print(f"[probe] goto failed: {e}")
        page.screenshot(path=os.path.join(OUT, f"{tag}_error.png"), full_page=True)
        return

    time.sleep(3)
    print(f"[probe] landed on: {page.url}")
    page.screenshot(path=os.path.join(OUT, f"{tag}_loaded.png"), full_page=True)

    # Dump all inputs
    inputs = page.evaluate("""() => {
        return [...document.querySelectorAll('input, textarea, select')].map(el => ({
            tag: el.tagName,
            type: el.type || '',
            name: el.name || '',
            id: el.id || '',
            placeholder: el.placeholder || '',
            value: el.value || '',
            visible: el.offsetParent !== null,
            classList: el.className || '',
        }));
    }""")
    print(f"[probe] inputs ({len(inputs)}):")
    for inp in inputs:
        print(f"  {json.dumps(inp)}")

    # Dump all buttons
    buttons = page.evaluate("""() => {
        return [...document.querySelectorAll('button, input[type="submit"], [role="button"]')]
            .map(el => ({
                tag: el.tagName,
                text: (el.textContent || el.value || '').trim().slice(0, 80),
                type: el.type || '',
                id: el.id || '',
                name: el.name || '',
                visible: el.offsetParent !== null,
                disabled: el.disabled || false,
            }));
    }""")
    print(f"[probe] buttons ({len(buttons)}):")
    for btn in buttons:
        print(f"  {json.dumps(btn)}")

    # Save full HTML
    html_path = os.path.join(OUT, f"{tag}_page.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"[probe] HTML saved to {html_path}")

    # Scroll to bottom and screenshot (footer newsletter form on homepage)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)
    page.screenshot(path=os.path.join(OUT, f"{tag}_bottom.png"), full_page=False)

    # Inputs again after scroll (lazy-loaded content)
    inputs2 = page.evaluate("""() => {
        return [...document.querySelectorAll('input[type="email"], input[placeholder*="email" i], input[name*="email" i]')]
            .map(el => ({
                type: el.type, name: el.name, id: el.id,
                placeholder: el.placeholder, visible: el.offsetParent !== null,
                rect: JSON.stringify(el.getBoundingClientRect()),
            }));
    }""")
    print(f"[probe] email-related inputs after scroll ({len(inputs2)}):")
    for inp in inputs2:
        print(f"  {json.dumps(inp)}")


with Camoufox(headless=True, geoip=True) as browser:
    page = browser.new_page()
    probe(page, URLS[0], "01_newsletter_coingecko")
    probe(page, URLS[1], "02_landing_coingecko")
    probe(page, URLS[2], "03_homepage")

print("\n[probe] Done — check logs/subscribe_debug/ for screenshots and HTML files.")
