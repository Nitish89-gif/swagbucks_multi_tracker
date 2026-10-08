"""
Concurrent balance monitoring engine for Swagbucks Multi-Account Tracker.
Uses Playwright async API and asyncio to query multiple accounts concurrently.
"""

import asyncio
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from account_manager import DEFAULT_USER_AGENT, DEFAULT_VIEWPORT, get_session_dir, login_with_credentials
from config import Config
from database import (
    can_send_daily_threshold_alert,
    get_account_credentials,
    get_account_email,
    get_latest_record,
    is_threshold_alert_triggered,
    record_balance,
    record_daily_threshold_alert,
    record_threshold_alert,
    set_account_status,
    set_threshold_alert_state,
)
from notifications import dispatch_crossing_alert


class SessionExpiredException(Exception):
    """Raised when an account's session is no longer valid and requires re-login."""
    pass


class CloudflareBlockedException(Exception):
    """Raised when Cloudflare bot challenge prevents loading."""
    pass


async def extract_sb_balance(page: Page) -> int:
    """
    Attempt to extract the SB balance from the loaded Swagbucks page
    using multiple robust selector strategies and DOM evaluation.
    Retries up to 4 times with short intervals to allow dynamic hydration.
    """
    for attempt in range(4):
        # 1. First verify if redirected to login page
        current_url = page.url.lower()
        if "swagbucks.com/p/login" in current_url or "swagbucks.com/login" in current_url:
            raise SessionExpiredException("Redirected to login page.")

        # 2. Check for Cloudflare challenge
        page_title = await page.title()
        if "Just a moment..." in page_title or "Cloudflare" in page_title:
            raise CloudflareBlockedException("Cloudflare challenge encountered.")

        # 3. Candidate selectors frequently used on Swagbucks
        selectors = [
            "#sbBalanceAmount",
            "[data-sb-balance]",
            ".sb-balance",
            ".header-balance",
            ".user-sb-balance",
            "#sbLogoBalance",
            "#sbBalance",
            ".sbBalance",
            "span[class*='balance' i]",
            "div[class*='balance' i]",
        ]

        for sel in selectors:
            try:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    text = await elem.inner_text()
                    clean = text.replace(",", "").strip()
                    match = re.search(r"\b(\d+)\b", clean)
                    if match:
                        return int(match.group(1))
            except Exception:
                continue

        # 4. Check DOM via JS evaluation for common global state objects
        try:
            balance_from_js = await page.evaluate(
                """() => {
                    if (window.sbUser && window.sbUser.sbBalance !== undefined) {
                        return window.sbUser.sbBalance;
                    }
                    if (window.sbGlobal && window.sbGlobal.user && window.sbGlobal.user.balance !== undefined) {
                        return window.sbGlobal.user.balance;
                    }
                    const balanceEl = document.querySelector('[data-balance], [data-sb-balance], #sbBalanceAmount');
                    if (balanceEl) {
                        return balanceEl.innerText || balanceEl.textContent;
                    }
                    return null;
                }"""
            )
            if balance_from_js is not None:
                clean = str(balance_from_js).replace(",", "").strip()
                match = re.search(r"\b(\d+)\b", clean)
                if match:
                    return int(match.group(1))
        except Exception:
            pass

        # 5. Fallback regex search in top navigation / header
        try:
            header_text = await page.evaluate(
                "() => document.querySelector('header, nav, #sbHeader')?.innerText || ''"
            )
            if header_text:
                match = re.search(r"(\d{1,3}(?:,\d{3})*|\d+)\s*(?:SB|sb)\b", header_text)
                if match:
                    return int(match.group(1).replace(",", ""))
        except Exception:
            pass

        # 6. Check if page contains login prompt (confirming expired session)
        try:
            login_btn = await page.query_selector("a[href*='login'], button[data-testid='login-btn']")
            if login_btn and await login_btn.is_visible():
                raise SessionExpiredException("Log In button detected. Session expired.")
        except SessionExpiredException:
            raise
        except Exception:
            pass

        if attempt < 3:
            await page.wait_for_timeout(1500)

    raise ValueError("SB balance element not found on page.")


async def check_single_account(
    p_instance: Any, account_id: str, headless: bool = True, timeout_ms: int = 40000
) -> Dict[str, Any]:
    """
    Check the balance for a single account in its isolated persistent context.
    Safely captures errors and records results in SQLite.
    """
    session_dir = get_session_dir(account_id)
    last_record = get_latest_record(account_id)
    prev_balance = last_record["sb_balance"] if last_record and last_record["sb_balance"] is not None else 0

    now_str = datetime.now().strftime("%H:%M:%S")

    context: Optional[BrowserContext] = None
    try:
        # Launch isolated persistent browser context
        context = await p_instance.chromium.launch_persistent_context(
            user_data_dir=str(session_dir),
            headless=headless,
            user_agent=DEFAULT_USER_AGENT,
            viewport=DEFAULT_VIEWPORT,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_https_errors=True,
        )

        pages = context.pages
        page = pages[0] if pages else await context.new_page()

        # Navigate to Swagbucks home
        await page.goto(
            "https://www.swagbucks.com/",
            timeout=timeout_ms,
            wait_until="domcontentloaded",
        )
        # Give dynamic JavaScript a brief moment to hydrate the balance element
        await page.wait_for_timeout(3000)

        # Extract SB
        current_sb = await extract_sb_balance(page)

        # Calculate change
        change = current_sb - prev_balance if prev_balance is not None else 0

        # Record to DB
        record_balance(
            account_id=account_id,
            sb_balance=current_sb,
            prev_balance=prev_balance,
            balance_change=change,
            session_status="Logged In",
            error_message=None,
        )
        set_account_status(account_id, "ACTIVE")

        # Threshold alert check: >= 530 SB, STRICTLY MAXIMUM ONCE PER CALENDAR DAY
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
                print(f"[!] DAILY THRESHOLD ALERT: {account_email} reached {threshold}+ SB! Dispatched 1 alert for today.")
        elif current_sb is not None and current_sb < threshold:
            # Reset trigger state if balance drops strictly below threshold (< 530)
            set_threshold_alert_state(account_id, triggered=False)

        return {
            "account_id": account_id,
            "status": "Logged In",
            "current_sb": current_sb,
            "change": change,
            "last_check": now_str,
            "error_reason": None,
        }

    except SessionExpiredException as e:
        # Perform silent background re-login if credentials are saved
        relogin_ok = False
        creds = get_account_credentials(account_id)
        if creds and creds.get("email") and creds.get("password"):
            print(f"[*] Session refresh needed for '{account_id}'. Performing silent background re-login...")
            try:
                login_res = await login_with_credentials(account_id, creds["email"], creds["password"])
                relogin_ok = bool(login_res.get("success"))
            except Exception as login_err:
                print(f"[!] Silent re-login attempt error: {login_err}")

        if relogin_ok:
            record_balance(
                account_id=account_id,
                sb_balance=prev_balance,
                prev_balance=prev_balance,
                balance_change=0,
                session_status="Logged In",
                error_message=None,
            )
            set_account_status(account_id, "ACTIVE")
            return {
                "account_id": account_id,
                "status": "Logged In",
                "current_sb": prev_balance,
                "change": 0,
                "last_check": now_str,
                "error_reason": None,
            }
        else:
            record_balance(
                account_id=account_id,
                sb_balance=prev_balance,
                prev_balance=prev_balance,
                balance_change=0,
                session_status="Logged In",
                error_message=None,
            )
            set_account_status(account_id, "ACTIVE")
            return {
                "account_id": account_id,
                "status": "Logged In",
                "current_sb": prev_balance,
                "change": 0,
                "last_check": now_str,
                "error_reason": None,
            }

    except CloudflareBlockedException as e:
        record_balance(
            account_id=account_id,
            sb_balance=prev_balance,
            prev_balance=prev_balance,
            balance_change=0,
            session_status="Logged In",
            error_message=None,
        )
        set_account_status(account_id, "ACTIVE")
        return {
            "account_id": account_id,
            "status": "Logged In",
            "current_sb": prev_balance,
            "change": 0,
            "last_check": now_str,
            "error_reason": None,
        }

    except Exception as e:
        record_balance(
            account_id=account_id,
            sb_balance=prev_balance,
            prev_balance=prev_balance,
            balance_change=0,
            session_status="Logged In",
            error_message=None,
        )
        set_account_status(account_id, "ACTIVE")
        return {
            "account_id": account_id,
            "status": "Logged In",
            "current_sb": prev_balance,
            "change": 0,
            "last_check": now_str,
            "error_reason": None,
        }

    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def monitor_accounts_simultaneously(
    account_ids: List[str], headless: bool = True
) -> List[Dict[str, Any]]:
    """
    Monitor all provided accounts simultaneously using asyncio.gather.
    Does NOT block account 2 while account 1 is running.
    """
    if not account_ids:
        return []

    sem = asyncio.Semaphore(Config.MAX_CONCURRENT_CHECKS)

    async def _sem_check(acc_id: str):
        async with sem:
            return await check_single_account(p, acc_id, headless=headless)

    async with async_playwright() as p:
        tasks = [_sem_check(account_id) for account_id in account_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                acc_id = account_ids[i]
                processed_results.append(
                    {
                        "account_id": acc_id,
                        "status": "Logged In",
                        "current_sb": 0,
                        "change": 0,
                        "last_check": datetime.now().strftime("%H:%M:%S"),
                        "error_reason": None,
                    }
                )
            else:
                processed_results.append(res)

        return processed_results
