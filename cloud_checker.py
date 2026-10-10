"""
Cloud Balance Checker for Swagbucks Multi-Account Tracker.
Runs 100% in the cloud (GitHub Actions, Docker, or Cloud Server) completely
independent of the local laptop.

Features:
- Uses exported session states (data/sessions/*.json) with Playwright.
- Rotating batch IP proxies (3 accounts per 1 IP address).
- Random anti-bot delays (120s - 180s) between account checks.
- Instant mobile phone push notification & email alert when any account crosses 530 SB.
- Automatic persistence to SQLite database and syncs to Vercel.
"""

import asyncio
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright

from account_manager import DEFAULT_USER_AGENT, DEFAULT_VIEWPORT
from config import Config
from database import (
    can_send_daily_threshold_alert,
    get_account_email,
    get_all_accounts,
    get_latest_record,
    init_db,
    record_balance,
    record_daily_threshold_alert,
    record_threshold_alert,
    set_account_status,
)
from monitor import extract_sb_balance
from notifications import dispatch_crossing_alert
from proxy_manager import proxy_manager

SESSIONS_DIR = PROJECT_ROOT / "data" / "sessions"


def get_target_accounts() -> List[str]:
    """Get list of account IDs to check from session files or database."""
    accounts = []
    if SESSIONS_DIR.exists():
        for p in sorted(SESSIONS_DIR.glob("*.json")):
            accounts.append(p.stem)
    if not accounts:
        db_accs = get_all_accounts()
        accounts = [a["account_id"] for a in db_accs if a and "account_id" in a]
    return sorted(list(dict.fromkeys(accounts)))


async def check_account_in_cloud(
    playwright_obj,
    account_id: str,
    account_index: int,
    total_accounts: int,
    batch_size: int = 3,
) -> Dict[str, Any]:
    """Check a single account's balance using its cloud session state."""
    b_info = proxy_manager.get_batch_info_for_account(
        account_id, account_index, total_accounts, batch_size=batch_size
    )
    proxy_dict = b_info["proxy_dict"] if Config.USE_PROXY_ROTATION else None
    ip_display = b_info["display"]
    batch_num = b_info["batch_num"]
    pos_in_batch = b_info["pos_in_batch"]

    session_file = SESSIONS_DIR / f"{account_id}.json"
    storage_state_data = None
    if session_file.exists():
        try:
            storage_state_data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] Warning reading session for {account_id}: {e}")

    prev_record = get_latest_record(account_id)
    prev_balance = prev_record["sb_balance"] if prev_record and prev_record["sb_balance"] is not None else 0

    print(f"[*] [{account_index+1}/{total_accounts}] Checking {account_id} | Batch {batch_num} [{pos_in_batch}/{batch_size}] | IP: {ip_display}...")

    # Determine if proxy is live and working before passing to Chromium
    use_pw_proxy = None
    if proxy_dict:
        srv = proxy_dict.get("server", "")
        if proxy_manager.test_proxy(srv, timeout=2):
            use_pw_proxy = proxy_dict

    browser = None
    context = None
    try:
        browser = await playwright_obj.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            proxy=use_pw_proxy,
        )

        context_kwargs = {
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": DEFAULT_VIEWPORT,
        }
        if storage_state_data:
            context_kwargs["storage_state"] = storage_state_data

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        await page.goto("https://www.swagbucks.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        current_sb = await extract_sb_balance(page)

        # Update session cookies back to file if fresh cookies were set
        try:
            new_state = await context.storage_state()
            if session_file.parent.exists():
                session_file.write_text(json.dumps(new_state, indent=2), encoding="utf-8")
        except Exception:
            pass

        change = current_sb - prev_balance if prev_balance is not None else 0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        record_balance(
            account_id=account_id,
            sb_balance=current_sb,
            prev_balance=prev_balance,
            balance_change=change,
            session_status="Logged In",
            ip_used=ip_display,
        )
        set_account_status(account_id, "ACTIVE")

        # Threshold check: >= 530 SB
        threshold = Config.BALANCE_THRESHOLD
        if current_sb is not None and current_sb >= threshold:
            if can_send_daily_threshold_alert(account_id):
                account_email = get_account_email(account_id)
                alert_res = await dispatch_crossing_alert(
                    account_id=account_id,
                    account_email=account_email,
                    prev_balance=prev_balance,
                    current_balance=current_sb,
                    threshold=threshold,
                )
                record_threshold_alert(
                    account_id=account_id,
                    email=account_email,
                    prev_balance=prev_balance,
                    current_balance=current_sb,
                    channels_sent=alert_res.get("channels", "None"),
                    threshold=threshold,
                )
                record_daily_threshold_alert(account_id)
                print(f"[!] THRESHOLD ALERT: {account_id} reached {current_sb} SB (>= {threshold})! Sent mobile push alert.")

        print(f"   [+] Result for {account_id}: {current_sb} SB (Change: {change:+d})")
        return {
            "account_id": account_id,
            "status": "Logged In",
            "current_sb": current_sb,
            "change": change,
            "last_check": now_str,
            "ip_used": ip_display,
        }

    except Exception as e:
        print(f"   [-] Error checking {account_id}: {e}")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record_balance(
            account_id=account_id,
            sb_balance=prev_balance,
            prev_balance=prev_balance,
            balance_change=0,
            session_status="Logged In",
            error_message=str(e),
            ip_used=ip_display,
        )
        return {
            "account_id": account_id,
            "status": "Error",
            "current_sb": prev_balance,
            "change": 0,
            "last_check": now_str,
            "ip_used": ip_display,
            "error": str(e),
        }

    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def run_cloud_check(min_gap: int = 5, max_gap: int = 10):
    """
    Execute check on all accounts.
    When running in CI/CD without human supervision, default anti-bot gap can be configured.
    """
    init_db()
    accounts = get_target_accounts()
    print(f"=== CLOUD SWAGBUCKS BALANCE CHECKER ===")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Total Accounts: {len(accounts)}")
    print(f"Batch IP Size: {Config.BATCH_ACCOUNTS_PER_IP} accounts per IP")
    print(f"=======================================")

    results = []
    total = len(accounts)
    batch_size = Config.BATCH_ACCOUNTS_PER_IP

    async with async_playwright() as p:
        for idx, acc_id in enumerate(accounts):
            res = await check_account_in_cloud(p, acc_id, idx, total, batch_size=batch_size)
            results.append(res)

            # Delay before next account
            if idx < total - 1:
                gap = random.randint(min_gap, max_gap)
                print(f"[*] Sleeping {gap}s anti-bot interval before checking next account...")
                await asyncio.sleep(gap)

    # Copy updated db to seed db
    db_file = PROJECT_ROOT / "data" / "sb_history.db"
    seed_file = PROJECT_ROOT / "data" / "sb_history_seed.db"
    if db_file.exists():
        import shutil
        shutil.copyfile(str(db_file), str(seed_file))
        print("[+] Synced database to sb_history_seed.db")

    # Sync live results directly to Vercel production dashboard
    try:
        import urllib.request
        vercel_url = os.getenv("VERCEL_DASHBOARD_URL", "https://swagbucksmultitracker.vercel.app")
        sync_payload = {
            "accounts": [
                {
                    "account_id": r["account_id"],
                    "email": get_account_email(r["account_id"]),
                    "current_sb": r["current_sb"],
                    "ip_used": r["ip_used"],
                }
                for r in results if r.get("status") == "Logged In" or r.get("current_sb") is not None
            ]
        }
        req_data = json.dumps(sync_payload).encode("utf-8")
        req = urllib.request.Request(
            f"{vercel_url.rstrip('/')}/api/sync-accounts",
            data=req_data,
            headers={"Content-Type": "application/json", "User-Agent": "SwagbucksCloudRunner/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(f"[+] Successfully pushed cloud check results to Vercel ({resp.status})")
    except Exception as e:
        print(f"[!] Warning syncing results to Vercel: {e}")

    print("\n=== SUMMARY OF CLOUD CHECK ===")
    for r in results:
        print(f"  * {r['account_id']:20} -> {r['current_sb']:>5} SB | IP: {r['ip_used']}")
    print("===============================\n")


if __name__ == "__main__":
    # If environment provides GAP settings, use them
    min_g = int(os.getenv("CLOUD_MIN_GAP", "5"))
    max_g = int(os.getenv("CLOUD_MAX_GAP", "10"))
    asyncio.run(run_cloud_check(min_gap=min_g, max_gap=max_g))
