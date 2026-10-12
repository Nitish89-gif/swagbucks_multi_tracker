"""
Concurrent balance monitoring engine for Swagbucks Multi-Account Tracker.
Uses Playwright async API and asyncio to query multiple accounts concurrently.
"""

import asyncio
import random
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

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
from proxy_manager import proxy_manager


class SessionExpiredException(Exception):
    """Raised when an account's session is no longer valid and requires re-login."""
    pass


class CloudflareBlockedException(Exception):
    """Raised when Cloudflare bot challenge prevents loading."""
    pass


async def extract_sb_balance(page: Page) -> int:
    """
    Attempt to extract the SB balance from the loaded Swagbucks page
    using multiple robust selector strategies, raw attribute inspection,
    and DOM evaluation.
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

        # 3. Direct DOM Evaluation: data-sb-raw attribute & modern React/Vue balance nodes
        try:
            raw_val = await page.evaluate(
                """() => {
                    // Check exact raw attribute on any element
                    const rawEl = document.querySelector('[data-sb-raw]');
                    if (rawEl && rawEl.getAttribute('data-sb-raw')) {
                        return rawEl.getAttribute('data-sb-raw');
                    }
                    // Check var tags (Swagbucks uses <var class="toggler_balanceNumber..." data-sb-raw="392">)
                    const varEl = document.querySelector('var[class*="balance" i], [class*="balanceNumber" i]');
                    if (varEl) {
                        return varEl.innerText || varEl.textContent;
                    }
                    // Check account menu toggler
                    const menuAcc = document.querySelector('#main-menu-item-account, [data-testid="main-menu-item-account"], .toggler_userBalanceAndImage__B0JNA');
                    if (menuAcc) {
                        return menuAcc.innerText || menuAcc.textContent;
                    }
                    // Check global JS objects
                    if (window.sbUser && window.sbUser.sbBalance !== undefined) {
                        return window.sbUser.sbBalance;
                    }
                    if (window.sbGlobal && window.sbGlobal.user && window.sbGlobal.user.balance !== undefined) {
                        return window.sbGlobal.user.balance;
                    }
                    return null;
                }"""
            )
            if raw_val is not None:
                clean = str(raw_val).replace(",", "").strip()
                match = re.search(r"(\d+)", clean)
                if match:
                    return int(match.group(1))
        except Exception:
            pass

        # 4. Candidate selectors frequently used on Swagbucks
        selectors = [
            "[data-sb-raw]",
            "var[class*='balanceNumber' i]",
            "var[class*='balance' i]",
            "[class*='balanceNumber' i]",
            "[class*='balanceContainer' i]",
            "#main-menu-item-account",
            "[data-testid='main-menu-item-account']",
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
                    raw_attr = await elem.get_attribute("data-sb-raw")
                    if raw_attr:
                        clean_attr = raw_attr.replace(",", "").strip()
                        m_attr = re.search(r"\b(\d+)\b", clean_attr)
                        if m_attr:
                            return int(m_attr.group(1))
                    text = await elem.inner_text()
                    clean = text.replace(",", "").strip()
                    match = re.search(r"\b(\d+)\b", clean)
                    if match:
                        return int(match.group(1))
            except Exception:
                continue

        # 5. Fallback regex search in top navigation / header
        try:
            header_text = await page.evaluate(
                "() => document.querySelector('header, nav, #sbHeader, #header-container')?.innerText || ''"
            )
            if header_text:
                match = re.search(r"(\d{1,3}(?:,\d{3})*|\d+)\s*(?:SB|sb)\b", header_text)
                if match:
                    return int(match.group(1).replace(",", ""))
        except Exception:
            pass

        # 6. Check if page contains login prompt (confirming expired session)
        if attempt >= 2:
            try:
                login_btn = await page.query_selector("header a[href*='login'], nav a[href*='login'], button[data-testid='login-btn']")
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
    p_instance: Any,
    account_id: str,
    headless: bool = True,
    timeout_ms: int = 40000,
    proxy: Optional[Dict[str, str]] = None,
    ip_display: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check the balance for a single account in its isolated persistent context.
    Supports proxy/IP rotation and records results in SQLite with full date & time.
    """
    session_dir = get_session_dir(account_id)
    last_record = get_latest_record(account_id)
    prev_balance = last_record["sb_balance"] if last_record and last_record["sb_balance"] is not None else 0
    prev_successful_ts = (
        last_record["timestamp"]
        if (last_record and last_record.get("session_status") == "Logged In")
        else None
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    context: Optional[BrowserContext] = None
    try:
        launch_kwargs = {
            "user_data_dir": str(session_dir),
            "headless": headless,
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": DEFAULT_VIEWPORT,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            "ignore_https_errors": True,
        }
        if proxy and Config.USE_PROXY_ROTATION:
            launch_kwargs["proxy"] = proxy

        # Launch isolated persistent browser context
        context = await p_instance.chromium.launch_persistent_context(**launch_kwargs)

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

        # Record to DB with exact timestamp, ip_used, and day_start_sb
        record_balance(
            account_id=account_id,
            sb_balance=current_sb,
            prev_balance=prev_balance,
            balance_change=change,
            session_status="Logged In",
            error_message=None,
            ip_used=ip_display,
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
            "ip_used": ip_display,
        }

    except SessionExpiredException as e:
        relogin_ok = False
        creds = get_account_credentials(account_id)
        if creds and creds.get("email") and creds.get("password"):
            print(f"[*] Session refresh needed for '{account_id}'. Performing silent background re-login...")
            try:
                login_res = await login_with_credentials(account_id, creds["email"], creds["password"])
                relogin_ok = bool(login_res.get("success"))
                if relogin_ok and page:
                    await page.goto("https://www.swagbucks.com/", timeout=timeout_ms, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                    current_sb = await extract_sb_balance(page)
                    change = current_sb - prev_balance if prev_balance is not None else 0
                    record_balance(
                        account_id=account_id,
                        sb_balance=current_sb,
                        prev_balance=prev_balance,
                        balance_change=change,
                        session_status="Logged In",
                        error_message=None,
                        ip_used=ip_display,
                    )
                    set_account_status(account_id, "ACTIVE")
                    return {
                        "account_id": account_id,
                        "status": "Logged In",
                        "current_sb": current_sb,
                        "change": change,
                        "last_check": now_str,
                        "error_reason": None,
                        "ip_used": ip_display,
                    }
            except Exception as login_err:
                print(f"[!] Silent re-login attempt error: {login_err}")

        err_msg = "Session expired. Manual login required."
        record_balance(
            account_id=account_id,
            sb_balance=prev_balance,
            prev_balance=prev_balance,
            balance_change=0,
            session_status="SESSION EXPIRED",
            error_message=err_msg,
            ip_used=ip_display,
        )
        set_account_status(account_id, "EXPIRED")
        return {
            "account_id": account_id,
            "status": "SESSION EXPIRED",
            "current_sb": prev_balance,
            "change": 0,
            "last_check": prev_successful_ts,
            "error_reason": err_msg,
            "ip_used": ip_display,
        }

    except CloudflareBlockedException as e:
        err_msg = "Cloudflare verification challenge encountered."
        record_balance(
            account_id=account_id,
            sb_balance=prev_balance,
            prev_balance=prev_balance,
            balance_change=0,
            session_status="BLOCKED",
            error_message=err_msg,
            ip_used=ip_display,
        )
        set_account_status(account_id, "BLOCKED")
        return {
            "account_id": account_id,
            "status": "BLOCKED",
            "current_sb": prev_balance,
            "change": 0,
            "last_check": prev_successful_ts,
            "error_reason": err_msg,
            "ip_used": ip_display,
        }

    except Exception as e:
        err_msg = str(e)
        record_balance(
            account_id=account_id,
            sb_balance=prev_balance,
            prev_balance=prev_balance,
            balance_change=0,
            session_status="ERROR",
            error_message=err_msg,
            ip_used=ip_display,
        )
        set_account_status(account_id, "ERROR")
        return {
            "account_id": account_id,
            "status": "ERROR",
            "current_sb": prev_balance,
            "change": 0,
            "last_check": prev_successful_ts,
            "error_reason": err_msg,
            "ip_used": ip_display,
        }

    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def monitor_accounts_sequentially(
    account_ids: List[str],
    headless: bool = True,
    progress_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
    min_gap_seconds: Optional[int] = None,
    max_gap_seconds: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Monitor accounts sequentially with:
    1. Rotating IP addresses per account (when proxies configured).
    2. Time gap between accounts to prevent anti-bot detection.
    3. Live progress reporting for dashboard UI.
    """
    if not account_ids:
        return []

    min_gap = min_gap_seconds if min_gap_seconds is not None else Config.ACCOUNT_FETCH_GAP_MIN_SECONDS
    max_gap = max_gap_seconds if max_gap_seconds is not None else Config.ACCOUNT_FETCH_GAP_MAX_SECONDS

    processed_results = []
    total = len(account_ids)

    async with async_playwright() as p:
        for idx, acc_id in enumerate(account_ids):
            # 1. Determine batch proxy and display IP for this account
            batch_size = getattr(Config, "BATCH_ACCOUNTS_PER_IP", 3)
            b_info = proxy_manager.get_batch_info_for_account(acc_id, idx, total, batch_size=batch_size)
            proxy_dict = b_info["proxy_dict"] if Config.USE_PROXY_ROTATION else None
            batch_num = b_info["batch_num"]
            pos_in_batch = b_info["pos_in_batch"]

            use_pw_proxy = None
            if proxy_dict:
                srv = proxy_dict.get("server", "")
                if proxy_manager.test_proxy(srv, timeout=2):
                    use_pw_proxy = proxy_dict

            if use_pw_proxy:
                ip_display = b_info["display"]
            else:
                ip_display = proxy_manager.get_current_public_ip() or "Direct IP"

            if progress_callback:
                try:
                    cb_res = progress_callback({
                        "state": "checking",
                        "account_id": acc_id,
                        "index": idx + 1,
                        "total": total,
                        "ip_used": ip_display,
                        "batch_num": batch_num,
                        "pos_in_batch": pos_in_batch,
                        "batch_size": batch_size,
                    })
                    if asyncio.iscoroutine(cb_res):
                        await cb_res
                except Exception:
                    pass

            print(f"[*] [{idx+1}/{total}] Checking account '{acc_id}' via IP: {ip_display}...")

            try:
                res = await check_single_account(
                    p,
                    acc_id,
                    headless=headless,
                    proxy=use_pw_proxy,
                    ip_display=ip_display,
                )
                processed_results.append(res)
            except Exception as e:
                print(f"[!] Error checking {acc_id}: {e}")
                err_res = {
                    "account_id": acc_id,
                    "status": "ERROR",
                    "current_sb": 0,
                    "change": 0,
                    "last_check": None,
                    "error_reason": str(e),
                    "ip_used": ip_display,
                }
                processed_results.append(err_res)

            # Report completion of this account
            if progress_callback:
                try:
                    cb_res = progress_callback({
                        "state": "account_finished",
                        "account_id": acc_id,
                        "index": idx + 1,
                        "total": total,
                        "ip_used": ip_display,
                    })
                    if asyncio.iscoroutine(cb_res):
                        await cb_res
                except Exception:
                    pass

            # 2. Configured time gap before checking the next account
            if idx < total - 1 and max_gap > 0:
                gap = random.randint(min_gap, max_gap) if max_gap >= min_gap else min_gap
                gap_min = gap // 60
                gap_sec = gap % 60
                next_acc = account_ids[idx + 1]
                print(f"[*] [Gap Protection] Sleeping {gap}s ({gap_min}m {gap_sec}s) before checking next account '{next_acc}'...")

                for remaining in range(gap, 0, -1):
                    if progress_callback:
                        try:
                            cb_res = progress_callback({
                                "state": "gap",
                                "account_id": acc_id,
                                "next_account_id": next_acc,
                                "index": idx + 1,
                                "total": total,
                                "gap_remaining": remaining,
                                "gap_total": gap,
                            })
                            if asyncio.iscoroutine(cb_res):
                                await cb_res
                        except Exception:
                            pass
                    await asyncio.sleep(1)

        if progress_callback:
            try:
                cb_res = progress_callback({
                    "state": "completed",
                    "total": total,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                if asyncio.iscoroutine(cb_res):
                    await cb_res
            except Exception:
                pass

    return processed_results


# Maintain alias for compatibility
monitor_accounts_simultaneously = monitor_accounts_sequentially
