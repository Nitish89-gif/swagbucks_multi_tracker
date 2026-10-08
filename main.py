"""
Main orchestrator for Swagbucks Multi-Account Tracker.
Provides interactive startup menu and concurrent async monitoring loop.
"""

import asyncio
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt

from account_manager import (
    authenticate_account_interactive,
    list_saved_accounts,
    remove_account_session,
)
from dashboard import build_dashboard, print_single_summary
from database import (
    get_all_accounts,
    get_latest_record,
    init_db,
    register_account,
)
from monitor import monitor_accounts_simultaneously

console = Console()

DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_HEADLESS = True


async def monitoring_loop(
    account_ids: List[str],
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    headless: bool = DEFAULT_HEADLESS,
) -> None:
    """
    Main asynchronous loop:
    - Queries all accounts simultaneously
    - Updates terminal dashboard
    - Counts down to next check
    - Handles errors and expired sessions without terminating
    """
    console.print(f"[bold green]Starting concurrent monitoring for {len(account_ids)} accounts...[/bold green]")
    console.print(f"[dim]Interval: {interval_seconds}s | Headless: {headless} | Press Ctrl+C to stop[/dim]\n")

    # Initial state populated from database or defaults
    account_results: List[Dict[str, Any]] = []
    for acc_id in account_ids:
        latest = get_latest_record(acc_id)
        if latest:
            account_results.append({
                "account_id": acc_id,
                "status": latest["session_status"],
                "current_sb": latest["sb_balance"] if latest["sb_balance"] is not None else 0,
                "change": latest["balance_change"] if latest["balance_change"] is not None else 0,
                "last_check": latest["timestamp"].split()[1] if " " in str(latest["timestamp"]) else str(latest["timestamp"]),
                "error_reason": latest["error_message"],
            })
        else:
            account_results.append({
                "account_id": acc_id,
                "status": "Pending",
                "current_sb": 0,
                "change": 0,
                "last_check": "--:--:--",
                "error_reason": None,
            })

    try:
        with Live(
            build_dashboard(account_results, countdown_seconds=0, is_checking=True),
            console=console,
            refresh_per_second=4,
            screen=False,
        ) as live:
            while True:
                # 1. Update UI to indicate simultaneous check is starting
                live.update(build_dashboard(account_results, countdown_seconds=0, is_checking=True))

                # 2. Check all accounts simultaneously
                try:
                    new_results = await monitor_accounts_simultaneously(account_ids, headless=headless)
                    
                    # Update local state
                    res_map = {r["account_id"]: r for r in new_results}
                    updated_list = []
                    for existing in account_results:
                        acc_id = existing["account_id"]
                        if acc_id in res_map:
                            updated_list.append(res_map[acc_id])
                        else:
                            updated_list.append(existing)
                    account_results = updated_list

                except Exception as err:
                    # In case of overarching unexpected loop error, do not crash
                    for item in account_results:
                        item["status"] = "ERROR"
                        item["error_reason"] = f"Loop error: {str(err)}"

                # 3. Countdown timer until next simultaneous check
                for remaining in range(interval_seconds, 0, -1):
                    live.update(build_dashboard(account_results, countdown_seconds=remaining, is_checking=False))
                    await asyncio.sleep(1)

    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow][*] Monitoring stopped by user.[/yellow]")


def print_banner() -> None:
    """Print welcoming ASCII header."""
    banner_text = (
        "[bold cyan]=========================================================\n"
        "             SWAGBUCKS MULTI-ACCOUNT MONITOR\n"
        "=========================================================[/bold cyan]\n"
        "[dim]Isolated Sessions • Playwright Async • Live Dashboard[/dim]"
    )
    console.print(Panel(banner_text, border_style="cyan"))


def interactive_menu() -> None:
    """Interactive command-line interface for managing accounts and launching the monitor."""
    init_db()

    check_interval = DEFAULT_INTERVAL_SECONDS
    use_headless = DEFAULT_HEADLESS

    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print_banner()

        saved_accounts = list_saved_accounts()
        console.print("\n[bold white]Authenticated Account Sessions:[/bold white]")

        if saved_accounts:
            for idx, acc in enumerate(saved_accounts, 1):
                latest = get_latest_record(acc)
                status = latest["session_status"] if latest else "Registered"
                bal_str = f"{latest['sb_balance']:,} SB" if (latest and latest["sb_balance"] is not None) else "No balance recorded yet"
                console.print(f"  [cyan]{idx}.[/cyan] [bold]{acc}[/bold] - Status: [green]{status}[/green] ({bal_str})")
        else:
            console.print("  [yellow]No accounts authenticated yet. Add one below to begin.[/yellow]")

        console.print("\n[bold cyan]Options:[/bold cyan]")
        console.print("  [1] Start Simultaneous Monitoring")
        console.print("  [2] Authenticate / Add New Account")
        console.print("  [3] Re-authenticate an Existing Account")
        console.print("  [4] View Account Historical Statistics")
        console.print("  [5] Remove an Account Session")
        console.print(f"  [6] Settings (Interval: {check_interval}s, Headless: {use_headless})")
        console.print("  [0] Exit")

        choice = Prompt.ask("\nEnter choice", choices=["1", "2", "3", "4", "5", "6", "0"], default="1" if saved_accounts else "2")

        if choice == "1":
            if not saved_accounts:
                console.print("[red]No accounts found! Please authenticate at least one account first.[/red]")
                Prompt.ask("Press Enter to continue")
                continue

            console.print("\n[bold green]Launching monitor...[/bold green]")
            try:
                asyncio.run(
                    monitoring_loop(
                        account_ids=saved_accounts,
                        interval_seconds=check_interval,
                        headless=use_headless,
                    )
                )
            except KeyboardInterrupt:
                pass
            Prompt.ask("\nPress Enter to return to main menu")

        elif choice == "2":
            acc_name = Prompt.ask(
                "\nEnter a unique ID for this account (e.g. account_1, personal_acc, etc.)"
            ).strip()
            if not acc_name:
                continue

            asyncio.run(authenticate_account_interactive(acc_name))
            Prompt.ask("\nPress Enter to return to menu")

        elif choice == "3":
            if not saved_accounts:
                console.print("[yellow]No accounts to re-authenticate.[/yellow]")
                Prompt.ask("Press Enter to continue")
                continue

            acc_name = Prompt.ask(
                "\nEnter the Account ID to re-authenticate",
                choices=saved_accounts,
            )
            console.print(f"[bold]Re-authenticating session for {acc_name}...[/bold]")
            asyncio.run(authenticate_account_interactive(acc_name))
            Prompt.ask("\nPress Enter to return to menu")

        elif choice == "4":
            if not saved_accounts:
                console.print("[yellow]No accounts recorded.[/yellow]")
                Prompt.ask("Press Enter to continue")
                continue

            acc_name = Prompt.ask(
                "\nEnter the Account ID to view statistics",
                choices=saved_accounts,
            )
            print_single_summary(acc_name)
            Prompt.ask("\nPress Enter to return to menu")

        elif choice == "5":
            if not saved_accounts:
                console.print("[yellow]No accounts found.[/yellow]")
                Prompt.ask("Press Enter to continue")
                continue

            acc_name = Prompt.ask(
                "\nEnter Account ID to delete",
                choices=saved_accounts,
            )
            confirm = Prompt.ask(
                f"[bold red]Are you sure you want to delete session for '{acc_name}'? (y/n)[/bold red]",
                choices=["y", "n"],
                default="n",
            )
            if confirm == "y":
                remove_account_session(acc_name)
            Prompt.ask("\nPress Enter to continue")

        elif choice == "6":
            new_interval_str = Prompt.ask(
                f"Enter check interval in seconds (current: {check_interval})",
                default=str(check_interval),
            )
            try:
                new_interval = int(new_interval_str)
                if new_interval < 10:
                    console.print("[yellow]Warning: Intervals under 10 seconds may trigger aggressive rate limiting.[/yellow]")
                check_interval = max(5, new_interval)
            except ValueError:
                console.print("[red]Invalid number.[/red]")

            toggle_headless = Prompt.ask(
                f"Run in headless mode? (y/n, current: {'yes' if use_headless else 'no'})",
                choices=["y", "n"],
                default="y" if use_headless else "n",
            )
            use_headless = (toggle_headless == "y")
            Prompt.ask("\nSettings updated. Press Enter to continue")

        elif choice == "0":
            console.print("[cyan]Exiting Swagbucks Multi-Account Monitor. Goodbye![/cyan]")
            sys.exit(0)


if __name__ == "__main__":
    try:
        interactive_menu()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Program exited.[/yellow]")
