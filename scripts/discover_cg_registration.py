"""
Run once to discover CoinGecko's registration HTTP endpoints.
Output: prints POST request details to stdout.

Usage:
    python scripts/discover_cg_registration.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import temp_email
import coingecko
from camoufox.sync_api import Camoufox

captured = []

mailbox = temp_email.create_mailbox()
email = mailbox["address"]
password = mailbox["cg_password"]
print(f"Registering with: {email}")

with Camoufox(headless=False, geoip=True) as browser:
    page = browser.new_page()

    def on_request(request):
        if "coingecko.com" in request.url and request.method in ("POST", "PUT", "PATCH"):
            captured.append({
                "url": request.url,
                "method": request.method,
                "content_type": request.headers.get("content-type", ""),
                "post_data": request.post_data,
            })

    page.on("request", on_request)
    coingecko.register(page, email, password)

print("\n=== Captured Registration Requests ===")
for i, r in enumerate(captured, 1):
    print(f"\n[{i}] {r['method']} {r['url']}")
    print(f"    Content-Type: {r['content_type']}")
    if r["post_data"]:
        print(f"    Body: {r['post_data'][:500]}")

with open("registration_requests.json", "w") as f:
    json.dump(captured, f, indent=2)
print("\nFull details saved to registration_requests.json")
