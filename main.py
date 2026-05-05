import argparse
import sys
import traceback
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from camoufox.sync_api import Camoufox
import temp_email
import coingecko
import storage

# Force UTF-8 so Rich spinner/box characters don't crash on Windows CP1252 terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

console = Console()


def create_one_account(idx: int) -> dict:
    console.rule(f"[bold cyan]Account #{idx}")

    with Progress(SpinnerColumn("line"), TextColumn("{task.description}"), console=console) as prog:
        t = prog.add_task("Creating disposable mailbox...", total=None)
        mailbox = temp_email.create_mailbox()
        email = mailbox["address"]
        prog.update(t, description=f"[green]Mailbox ready:[/green] {email}")

        prog.update(t, description="Launching browser & registering on CoinGecko...")
        with Camoufox(headless=False, geoip=True) as browser:
            page = browser.new_page()
            try:
                cg_pw = mailbox["cg_password"]
                coingecko.register(page, email, cg_pw)
                prog.update(t, description="Waiting for verification email (up to 3 min)...")

                body = temp_email.poll_inbox(mailbox["token"])
                link = temp_email.extract_verification_link(body)
                prog.update(t, description="Confirming email via verification link...")

                coingecko.confirm_email(page, link, cg_pw)
                prog.update(t, description="Fetching API key from developer dashboard...")

                api_key = coingecko.get_api_key(page, email, cg_pw)
            finally:
                browser.close()

        prog.update(t, description=f"[bold green]Done![/bold green] API key: {api_key[:16]}...")

    storage.save_account(email, cg_pw, api_key)
    return {"email": email, "api_key": api_key}


def main():
    parser = argparse.ArgumentParser(description="CoinGecko account farming PoC")
    parser.add_argument("--count", type=int, default=1, help="Number of accounts to create")
    args = parser.parse_args()

    results = []
    for i in range(1, args.count + 1):
        try:
            result = create_one_account(i)
            results.append(result)
        except Exception as e:
            console.print(f"[red]Account #{i} failed:[/red] {e}")
            console.print(f"[dim]{traceback.format_exc()}[/dim]")

    if results:
        table = Table(title="Created Accounts", show_lines=True)
        table.add_column("#", style="dim")
        table.add_column("Email", style="cyan")
        table.add_column("API Key", style="green")
        for i, r in enumerate(results, 1):
            table.add_row(str(i), r["email"], r["api_key"])
        console.print(table)
        console.print(f"\n[bold green]{len(results)}/{args.count} accounts created.[/bold green]")
        console.print("Run [bold]python dashboard/app.py[/bold] to view all accounts.")
    else:
        console.print("[red]No accounts were created successfully.[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
