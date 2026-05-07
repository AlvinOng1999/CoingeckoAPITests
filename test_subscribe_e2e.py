"""
End-to-end test: subscribe one email via JS-fill (bypasses label interception).
Run: venv\Scripts\python.exe test_subscribe_e2e.py
"""
import os, time, sys
from camoufox.sync_api import Camoufox

sys.path.insert(0, os.path.dirname(__file__))
import coingecko

OUT = os.path.join(os.path.dirname(__file__), "logs", "subscribe_debug")
os.makedirs(OUT, exist_ok=True)

# Use a real created-account email from your bulk_accounts table, or any test email.
# For a quick pass/fail check the page just needs to show a success state.
EMAIL = "test_e2e_subscribe@wshu.net"

print(f"\n[e2e] Testing subscribe_newsletter with email={EMAIL}")

with Camoufox(headless=True, geoip=True) as browser:
    page = browser.new_page()
    result = coingecko.subscribe_newsletter(page, EMAIL, "")

print(f"\n[e2e] Result: subscribed={result}")
print(f"[e2e] Screenshots: {OUT}")
sys.exit(0 if result else 1)
