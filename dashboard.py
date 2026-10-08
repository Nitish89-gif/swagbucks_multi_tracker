"""
Terminal dashboard renderer for Swagbucks Multi-Account Tracker.
Uses Rich to provide live status tables, total metrics, alerts, and countdowns.
"""

from typing import Any, Dict, List
from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from database import get_account_stats

console = Console()


def format_change(change: int) -> Text:
    """Format change with color and explicit +/- sign."""
    if change > 0:
        return Text(f"+{change:,}", style="bold green")
    elif change < 0:
        return Text(f"{change:,}", style="bold red")
    else:
        return Text("0", style="dim")


def format_status(status: str) -> Text:
    """Format status with appropriate styling."""
    if status == "Logged In":
        return Text(status, style="bold green")
    elif status == "SESSION EXPIRED":
        return Text(status, style="bold red blink")
    elif status == "ERROR":
        return Text(status, style="bold red")
    elif status == "Checking...":
        return Text(status, style="bold yellow")
    else:
        return Text(status, style="dim")


def build_dashboard(
    account_results: List[Dict[str, Any]],
    countdown_seconds: int = 0,
    is_checking: bool = False,
    show_advanced_stats: bool = True,
) -> Panel:
    """
    Construct the terminal dashboard panel matching the specification:
    - Account, Status, Current SB, Change, Last Check
    - Summary of TOTAL SB and TOTAL CHANGE
    - Alerts for expired sessions or errors
    - Countdown until next check
    """
    # 1. Main monitoring table
    table = Table(
        title="SWAGBUCKS MULTI-ACCOUNT MONITOR",
        title_style="bold cyan",
        box=box.ASCII2,
        expand=True,
        header_style="bold white on navy_blue",
    )

    table.add_column("Account", justify="left", style="bold white")
    table.add_column("Status", justify="center")
    table.add_column("Current SB", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Last Check", justify="center")

    total_sb = 0
    total_change = 0
    alerts = []
    error_reasons = []

    for acc in account_results:
        acc_id = acc.get("account_id", "Unknown")
        status = acc.get("status", "Unknown")
        current_sb = acc.get("current_sb", 0)
        change = acc.get("change", 0)
        last_check = acc.get("last_check", "--:--:--")
        reason = acc.get("error_reason")

        total_sb += current_sb
        total_change += change

        if status == "SESSION EXPIRED":
            alerts.append(f"[bold red]Account '{acc_id}' requires manual login.[/bold red] Please re-authenticate via main menu.")
        elif status == "ERROR" and reason:
            error_reasons.append(f"[yellow]{acc_id}[/yellow] - [red]ERROR:[/red] {reason}")

        current_sb_str = f"{current_sb:,}" if current_sb is not None else "--"
        table.add_row(
            acc_id,
            format_status(status),
            current_sb_str,
            format_change(change),
            str(last_check),
        )

    # 2. Total summaries section
    summary_text = Text()
    summary_text.append(f"\nTOTAL SB: {total_sb:,}\n", style="bold green" if total_sb > 0 else "bold")
    summary_text.append("TOTAL CHANGE: ", style="bold")
    if total_change > 0:
        summary_text.append(f"+{total_change:,}\n", style="bold green")
    elif total_change < 0:
        summary_text.append(f"{total_change:,}\n", style="bold red")
    else:
        summary_text.append("0\n", style="dim")

    if is_checking:
        summary_text.append("\n[status] Checking all accounts simultaneously...", style="bold yellow")
    else:
        summary_text.append(f"\nNext check: {countdown_seconds} seconds", style="cyan")

    render_items = [table, summary_text]

    # 3. Add alerts / warnings if any accounts have issues
    if alerts or error_reasons:
        notice_lines = []
        for a in alerts:
            notice_lines.append(f"[!] {a}")
        for r in error_reasons:
            notice_lines.append(f"[!] {r}")
        render_items.append(
            Panel("\n".join(notice_lines), title="[bold red]ATTENTION REQUIRED[/bold red]", border_style="red")
        )

    # 4. Optional advanced statistics table
    if show_advanced_stats and account_results:
        stats_table = Table(
            title="Account Historical Stats",
            title_style="bold dim",
            box=box.SIMPLE_HEAD,
            expand=True,
            header_style="dim",
        )
        stats_table.add_column("Account", justify="left")
        stats_table.add_column("Today's Δ", justify="right")
        stats_table.add_column("High SB", justify="right")
        stats_table.add_column("Low SB", justify="right")
        stats_table.add_column("Checks (OK/Err)", justify="center")
        stats_table.add_column("Last Successful", justify="center")

        for acc in account_results:
            acc_id = acc.get("account_id", "")
            stats = get_account_stats(acc_id)
            stats_table.add_row(
                acc_id,
                format_change(stats["today_change"]),
                f"{stats['highest_balance']:,}",
                f"{stats['lowest_balance']:,}",
                f"{stats['successful_checks']}/{stats['failed_checks']}",
                str(stats["last_successful_check"]),
            )
        render_items.append(stats_table)

    return Panel(
        Group(*render_items),
        border_style="bright_blue",
        padding=(1, 2),
    )


def print_single_summary(account_id: str) -> None:
    """Print complete historical statistics for a single account."""
    stats = get_account_stats(account_id)
    table = Table(title=f"Statistics for {account_id}", box=box.ROUNDED)
    table.add_column("Metric", style="bold cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Current SB Balance", f"{stats['current_sb']:,}")
    table.add_row("Previous SB Balance", f"{stats['previous_sb']:,}")
    table.add_row("Latest Change", str(stats["latest_change"]))
    table.add_row("Today's Net Change", str(stats["today_change"]))
    table.add_row("Highest Recorded Balance", f"{stats['highest_balance']:,}")
    table.add_row("Lowest Recorded Balance", f"{stats['lowest_balance']:,}")
    table.add_row("Successful Checks", str(stats["successful_checks"]))
    table.add_row("Failed Checks", str(stats["failed_checks"]))
    table.add_row("Last Successful Check", str(stats["last_successful_check"]))

    console.print(table)
