import json
import sys
import requests
from rich.console import Console
from rich.panel import Panel
from rich import print_json
import storage

console = Console()
BASE = "https://api.coingecko.com/api/v3"


def fetch(endpoint, api_key, params=None):
    headers = {"x-cg-demo-api-key": api_key}
    r = requests.get(f"{BASE}{endpoint}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def demo(api_key: str):
    console.rule("[bold cyan]CoinGecko API Demo")

    console.print("\n[bold yellow]1. Bitcoin & Ethereum price (USD)[/bold yellow]")
    data = fetch("/simple/price", api_key, {"ids": "bitcoin,ethereum", "vs_currencies": "usd"})
    print_json(json.dumps(data))

    console.print("\n[bold yellow]2. Top 10 coins by market cap[/bold yellow]")
    data = fetch(
        "/coins/markets",
        api_key,
        {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1},
    )
    for i, coin in enumerate(data, 1):
        console.print(f"  {i:>2}. [green]{coin['name']:20}[/green]  ${coin['current_price']:>12,.2f}  cap ${coin['market_cap']:>20,.0f}")

    console.print("\n[bold yellow]3. Trending coins[/bold yellow]")
    data = fetch("/search/trending", api_key)
    for item in data.get("coins", []):
        c = item["item"]
        console.print(f"  #{c['market_cap_rank']:>5}  [magenta]{c['name']}[/magenta]  ({c['symbol']})")

    console.print("\n[bold green]All demo calls succeeded.[/bold green]")


if __name__ == "__main__":
    accounts = storage.get_all_accounts()
    if not accounts:
        console.print("[red]No accounts in DB. Run main.py first.[/red]")
        sys.exit(1)

    key = accounts[0]["api_key"]
    console.print(Panel(f"Using key: [bold]{key[:12]}...[/bold]", title="API Key"))
    demo(key)
