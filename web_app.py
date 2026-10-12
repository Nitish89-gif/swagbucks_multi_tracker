"""
Web application backend for Swagbucks Multi-Account Tracker.
Built with FastAPI, Playwright Async, and SQLite.
100% Pure Automatic Email Alert System (SMTP).
"""

import asyncio
from contextlib import asynccontextmanager
import os
from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from account_manager import (
    active_web_auth_sessions,
    auto_login_from_env,
    cancel_web_auth,
    finish_web_auth,
    get_session_dir,
    list_saved_accounts,
    login_with_credentials,
    open_account_browser_window,
    remove_account_session,
    start_web_auth,
)
from config import Config
from database import (
    delete_account_records,
    get_account_stats,
    get_accounts_above_threshold,
    get_aggregate_summary,
    get_all_accounts,
    get_balance_history,
    get_last_successful_check_overall,
    get_latest_record,
    init_db,
)
from monitor import monitor_accounts_simultaneously
from proxy_manager import proxy_manager
from notifications import (
    dispatch_crossing_alert,
    get_email_status,
    get_mobile_status,
    get_notification_status,
    reset_email_error,
    send_mobile_push_alert,
    send_mobile_test_push,
    send_test_email_sync,
    send_threshold_email_sync,
    test_smtp_connection_sync,
)

app = FastAPI(title="Swagbucks Multi-Account Tracker")

# Mount static directory for icons and assets
static_path = PROJECT_ROOT / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/manifest.json")
async def get_manifest():
    manifest_file = static_path / "manifest.json"
    if manifest_file.exists():
        return FileResponse(str(manifest_file), media_type="application/manifest+json")
    raise HTTPException(status_code=404, detail="Manifest not found")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def get_apple_touch_icon():
    icon_file = static_path / "apple-touch-icon.png"
    if icon_file.exists():
        return FileResponse(str(icon_file), media_type="image/png")
    raise HTTPException(status_code=404, detail="Icon not found")

def get_seconds_to_next_run(interval_seconds: int = 18000, last_check_time_str: Optional[str] = None) -> Tuple[int, str]:
    """Calculate seconds and target timestamp for next scheduled run based on interval (default 5 hours)."""
    from datetime import timezone
    if os.environ.get("VERCEL"):
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist_tz).replace(tzinfo=None)
    else:
        now = datetime.now()

    if last_check_time_str:
        try:
            last_clean = str(last_check_time_str).split(".")[0]
            last_dt = datetime.strptime(last_clean, "%Y-%m-%d %H:%M:%S")
            target_next = last_dt + timedelta(seconds=interval_seconds)
            if target_next > now:
                diff_sec = max(0, int((target_next - now).total_seconds()))
                return diff_sec, target_next.strftime("%Y-%m-%d %H:%M:%S")
            else:
                return 0, now.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    target_next = now + timedelta(seconds=interval_seconds)
    diff_sec = max(0, int((target_next - now).total_seconds()))
    return diff_sec, target_next.strftime("%Y-%m-%d %H:%M:%S")


def get_seconds_to_next_midnight(target_time_str: str = "00:00") -> Tuple[int, str]:
    """Calculate seconds and target timestamp until midnight / target daily check time in IST."""
    from datetime import timezone
    if os.environ.get("VERCEL"):
        # India Standard Time (UTC+5:30)
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist_tz)
    else:
        now = datetime.now()

    try:
        parts = target_time_str.split(":")
        target_h, target_m = int(parts[0]), int(parts[1])
    except Exception:
        target_h, target_m = 0, 0

    target_today = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    if now >= target_today:
        target_next = target_today + timedelta(days=1)
    else:
        target_next = target_today

    diff_sec = max(0, int((target_next - now).total_seconds()))
    return diff_sec, target_next.strftime("%Y-%m-%d %H:%M:%S")


init_diff, init_target_str = get_seconds_to_next_run(Config.CHECK_INTERVAL_SECONDS)

# Global monitoring state
monitor_state: Dict[str, Any] = {
    "interval_seconds": Config.CHECK_INTERVAL_SECONDS,
    "countdown_seconds": init_diff,
    "target_time": Config.TARGET_CHECK_TIME or "Every 1 Hour",
    "next_scheduled_check": init_target_str,
    "is_checking": False,
    "checking_state": "idle",
    "timer_paused": False,
    "last_check_status": "idle",
    "last_check_message": "Idle - waiting for scheduled check",
    "last_check_summary": None,
    "last_check_has_changes": False,
    "accounts_checked": 0,
    "accounts_failed": 0,
    "current_checking_account": None,
    "checking_index": 0,
    "checking_total": 0,
    "checking_ip": None,
    "gap_remaining": 0,
    "gap_total": 0,
    "next_account_id": None,
    "headless": Config.HEADLESS,
    "last_check_time": None,
    "task": None,
    "trigger_event": None,
}


class AddAccountRequest(BaseModel):
    account_id: str
    nickname: Optional[str] = None


class LoginCredentialsRequest(BaseModel):
    account_id: Optional[str] = None
    email: str
    password: str
    initial_sb: Optional[int] = 0


class UpdateBalanceRequest(BaseModel):
    account_id: Optional[str] = None
    sb_balance: int
    note: Optional[str] = None


class SyncAccountsRequest(BaseModel):
    accounts: List[Dict[str, Any]]


class SettingsRequest(BaseModel):
    interval_seconds: Optional[int] = 3600
    target_time: Optional[str] = "Every 1 Hour"
    threshold: Optional[int] = 530
    headless: Optional[bool] = True
    use_proxy_rotation: Optional[bool] = False
    gap_min_seconds: Optional[int] = 5
    gap_max_seconds: Optional[int] = 15


class EmailSettingsRequest(BaseModel):
    sender_email: str
    receiver_email: str
    sender_password: Optional[str] = None
    swagbucks_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    enabled: bool = True


class ThresholdTestRequest(BaseModel):
    prev_balance: int = 525
    current_balance: int = 535
    account_email: Optional[str] = None


class MobileSettingsRequest(BaseModel):
    topic: str
    enabled: bool = True


async def background_monitoring_worker():
    """Background asynchronous task that manages interval polling with anti-bot gaps and IP rotation."""
    while True:
        try:
            # 1. Countdown until next check (interval) or until manual trigger
            while monitor_state["countdown_seconds"] > 0:
                await asyncio.sleep(1)
                if not monitor_state.get("timer_paused", False):
                    monitor_state["countdown_seconds"] = max(0, monitor_state["countdown_seconds"] - 1)
                if monitor_state.get("trigger_event") and monitor_state["trigger_event"].is_set():
                    monitor_state["trigger_event"].clear()
                    break

            # If timer paused and no manual trigger, idle
            if monitor_state.get("timer_paused", False):
                if not (monitor_state.get("trigger_event") and monitor_state["trigger_event"].is_set()):
                    await asyncio.sleep(1)
                    continue
                else:
                    monitor_state["trigger_event"].clear()

            # 2. Fetch all accounts with staggered anti-bot gap
            accounts = list_saved_accounts()
            if accounts:
                from database import get_latest_record, get_last_successful_check_overall
                # Snapshot previous SB balances before checking
                prev_balances = {}
                for acc_id in accounts:
                    rec = get_latest_record(acc_id)
                    prev_balances[acc_id] = rec["sb_balance"] if (rec and rec["sb_balance"] is not None) else 0

                monitor_state["is_checking"] = True
                monitor_state["checking_state"] = "checking"
                monitor_state["last_check_status"] = "checking"
                monitor_state["last_check_message"] = f"Checking balances for {len(accounts)} accounts..."
                monitor_state["accounts_checked"] = 0
                monitor_state["accounts_failed"] = 0

                async def progress_hook(p_data: Dict[str, Any]):
                    monitor_state["checking_state"] = p_data.get("state", "checking")
                    monitor_state["current_checking_account"] = p_data.get("account_id")
                    monitor_state["checking_index"] = p_data.get("index", 0)
                    monitor_state["checking_total"] = p_data.get("total", len(accounts))
                    monitor_state["checking_ip"] = p_data.get("ip_used")
                    monitor_state["gap_remaining"] = p_data.get("gap_remaining", 0)
                    monitor_state["gap_total"] = p_data.get("gap_total", 0)
                    monitor_state["next_account_id"] = p_data.get("next_account_id")
                    monitor_state["batch_num"] = p_data.get("batch_num", 1)
                    monitor_state["pos_in_batch"] = p_data.get("pos_in_batch", 1)
                    monitor_state["batch_size"] = p_data.get("batch_size", 3)
                    if p_data.get("state") == "account_finished":
                        monitor_state["accounts_checked"] = p_data.get("index", 0)

                try:
                    results = await monitor_accounts_simultaneously(
                        accounts,
                        headless=monitor_state["headless"],
                        progress_callback=progress_hook,
                        min_gap_seconds=Config.ACCOUNT_FETCH_GAP_MIN_SECONDS,
                        max_gap_seconds=Config.ACCOUNT_FETCH_GAP_MAX_SECONDS,
                    )

                    # Compute balance comparisons
                    changes_detected = []
                    total_delta = 0
                    success_count = 0
                    for r in results:
                        if r.get("status") == "Logged In":
                            success_count += 1
                            acc_id = r.get("account_id")
                            new_sb = r.get("current_sb", 0)
                            old_sb = prev_balances.get(acc_id, new_sb)
                            delta = new_sb - old_sb
                            total_delta += delta
                            if delta != 0:
                                sign = "+" if delta > 0 else ""
                                changes_detected.append(f"{acc_id}: {sign}{delta} SB")

                    if success_count > 0:
                        if total_delta == 0 and len(changes_detected) == 0:
                            summary_msg = "Swagbucks are the same — no change detected."
                            has_changes = False
                        else:
                            sign = "+" if total_delta > 0 else ""
                            summary_msg = f"Swagbucks updated ({sign}{total_delta} SB total): {', '.join(changes_detected)}"
                            has_changes = True
                    else:
                        summary_msg = "Check completed with errors — no balances updated."
                        has_changes = False

                    monitor_state["last_check_summary"] = summary_msg
                    monitor_state["last_check_has_changes"] = has_changes

                    # Strictly update last_check_time to the latest successful check timestamp
                    last_success_ts = get_last_successful_check_overall()
                    monitor_state["last_check_time"] = last_success_ts or "Never"

                    failed_count = sum(1 for r in results if r.get("status") in ("ERROR", "BLOCKED", "SESSION EXPIRED"))
                    monitor_state["accounts_failed"] = failed_count
                    if failed_count == 0:
                        monitor_state["last_check_status"] = "success"
                        monitor_state["last_check_message"] = summary_msg
                    else:
                        monitor_state["last_check_status"] = "partial"
                        monitor_state["last_check_message"] = f"{summary_msg} ({failed_count} failure(s))"
                except Exception as e:
                    print(f"[!] Error in background check: {e}")
                    monitor_state["last_check_status"] = "error"
                    monitor_state["last_check_message"] = f"Check failed: {str(e)}"
                finally:
                    monitor_state["is_checking"] = False
                    monitor_state["checking_state"] = "idle"
                    monitor_state["gap_remaining"] = 0
                    monitor_state["current_checking_account"] = None
            else:
                monitor_state["last_check_status"] = "idle"
                monitor_state["last_check_message"] = "No registered accounts found."

            # 3. Schedule next interval run
            curr_interval = monitor_state.get("interval_seconds") or getattr(Config, "CHECK_INTERVAL_SECONDS", 18000)
            next_diff, next_target_str = get_seconds_to_next_run(curr_interval)
            monitor_state["countdown_seconds"] = next_diff
            monitor_state["next_scheduled_check"] = next_target_str

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[!] Unexpected error in monitor loop: {e}")
            monitor_state["is_checking"] = False
            monitor_state["checking_state"] = "idle"
            await asyncio.sleep(5)


def ensure_worker_running():
    """Ensure the background worker task is actively running and recovered if stopped."""
    task = monitor_state.get("task")
    if task is None or task.done():
        if monitor_state.get("trigger_event") is None:
            monitor_state["trigger_event"] = asyncio.Event()
        monitor_state["task"] = asyncio.create_task(background_monitoring_worker())


def set_windows_sleep_prevention(enable: bool = True):
    """Keep Windows awake and active even when lid is closed or machine is unattended."""
    if sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_AWAYMODE_REQUIRED = 0x00000040
            if enable:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
                )
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """Modern lifespan context manager to initialize database and background worker reliably."""
    init_db()
    if not os.environ.get("VERCEL"):
        set_windows_sleep_prevention(True)
        ensure_worker_running()

        # Auto-login from .env on startup if credentials configured and not yet active
        if Config.SWAGBUCKS_EMAIL and Config.SWAGBUCKS_PASSWORD:
            env_acc_id = Config.SWAGBUCKS_EMAIL.split("@")[0].replace(".", "_")
            if env_acc_id not in list_saved_accounts():
                print(f"[*] Found credentials for {Config.SWAGBUCKS_EMAIL} in .env. Initiating auto-login...")
                asyncio.create_task(auto_login_from_env())
    yield
    set_windows_sleep_prevention(False)
    if monitor_state.get("task"):
        monitor_state["task"].cancel()
        monitor_state["task"] = None


# Bind lifespan to the app router
app.router.lifespan_context = lifespan


@app.get("/health")
@app.get("/api/health")
async def health_check():
    """Production health-check endpoint."""
    is_running = bool(monitor_state.get("task") and not monitor_state["task"].done())
    above_threshold = get_accounts_above_threshold(Config.BALANCE_THRESHOLD)
    return {
        "status": "healthy" if is_running else "degraded",
        "timestamp": datetime.now().isoformat(),
        "tracker_status": "Running" if is_running else "Error",
        "accounts_total": len(list_saved_accounts()),
        "accounts_above_530": len(above_threshold),
        "threshold": Config.BALANCE_THRESHOLD,
        "last_successful_check": get_last_successful_check_overall(),
        "notifications": get_notification_status(),
    }


@app.get("/api/dashboard")
async def get_dashboard_data():
    """Return live dashboard statistics, threshold filters, and account statuses."""
    saved_accounts = list_saved_accounts()
    account_data = []
    total_sb = 0
    total_latest_change = 0
    total_today_change = 0
    alerts = []

    for acc_id in saved_accounts:
        stats = get_account_stats(acc_id)
        latest = get_latest_record(acc_id)

        status = stats.get("status")
        session_stat = latest.get("session_status") if latest else None
        current_sb = stats["current_sb"]
        latest_change = stats["latest_change"]
        today_change = stats["today_change"]

        total_sb += current_sb
        total_latest_change += latest_change
        total_today_change += today_change

        if session_stat in ("SESSION EXPIRED", "EXPIRED"):
            display_status = "Session Expired"
        elif session_stat == "BLOCKED":
            display_status = "Blocked"
        elif session_stat == "ERROR":
            display_status = "Error"
        elif session_stat == "Logged In":
            display_status = "Logged In"
        elif status:
            display_status = status
        else:
            display_status = "Not Checked"

        is_above = current_sb >= Config.BALANCE_THRESHOLD if current_sb is not None else False
        status_530 = "530+ Reached" if is_above else "Below 530"

        account_data.append({
            "account_id": acc_id,
            "email": stats.get("email") or stats.get("nickname") or acc_id,
            "status": display_status,
            "status_530": status_530,
            "current_sb": current_sb,
            "latest_change": latest_change,
            "today_change": today_change,
            "day_start_sb": stats.get("day_start_sb", current_sb),
            "ip_used": stats.get("ip_used") or (latest.get("ip_used") if latest else None),
            "highest_balance": stats["highest_balance"],
            "lowest_balance": stats["lowest_balance"],
            "successful_checks": stats["successful_checks"],
            "failed_checks": stats["failed_checks"],
            "last_check_time": stats["last_check_time"] or "Never",
            "last_successful_check": stats["last_successful_check"],
            "error_reason": latest["error_message"] if latest else None,
            "is_above_530": is_above,
        })

    active_login_account = (
        list(active_web_auth_sessions.keys())[0] if active_web_auth_sessions else None
    )

    accounts_above_530 = get_accounts_above_threshold(Config.BALANCE_THRESHOLD)
    tracker_alive = bool(monitor_state.get("task") and not monitor_state["task"].done())
    last_success = get_last_successful_check_overall()
    email_status = get_email_status()
    mobile_status = get_mobile_status()

    return {
        "accounts": account_data,
        "total_accounts": len(saved_accounts),
        "accounts_count": len(saved_accounts),
        "total_sb": total_sb,
        "total_latest_change": total_latest_change,
        "total_today_change": total_today_change,
        "accounts_above_530": accounts_above_530,
        "count_above_530": len(accounts_above_530),
        "threshold": Config.BALANCE_THRESHOLD,
        "tracker_status": "Active (Cloud)" if os.environ.get("VERCEL") else ("Running" if tracker_alive else "Error"),
        "notification_status": get_notification_status(),
        "mobile_status": mobile_status,
        "mobile_alerts_enabled": Config.MOBILE_ALERTS_ENABLED,
        "mobile_push_topic": Config.MOBILE_PUSH_TOPIC,
        "mobile_push_url": Config.get_mobile_push_url(),
        "email_status": email_status,
        "email_configured": email_status["configured"],
        "bot_sender_email": Config.SMTP_USER,
        "bot_sender_masked": Config.mask_email(Config.SMTP_USER),
        "receiver_email": Config.ALERT_EMAIL_TO,
        "receiver_email_masked": Config.mask_email(Config.ALERT_EMAIL_TO),
        "swagbucks_email": Config.SWAGBUCKS_EMAIL,
        "has_email_password": bool(Config.SMTP_PASSWORD),
        "last_successful_check": last_success,
        "last_check_summary": monitor_state.get("last_check_summary"),
        "last_check_has_changes": monitor_state.get("last_check_has_changes", False),
        "countdown_seconds": monitor_state.get("countdown_seconds", 0),
        "interval_seconds": monitor_state.get("interval_seconds", 3600),
        "target_check_time": Config.TARGET_CHECK_TIME or "Every 1 Hour",
        "next_scheduled_check": monitor_state.get("next_scheduled_check", ""),
        "timer_paused": monitor_state.get("timer_paused", False),
        "last_check_status": monitor_state.get("last_check_status", "idle"),
        "last_check_message": monitor_state.get("last_check_message", ""),
        "accounts_checked": monitor_state.get("accounts_checked", 0),
        "accounts_failed": monitor_state.get("accounts_failed", 0),
        "is_checking": monitor_state["is_checking"],
        "checking_state": monitor_state.get("checking_state", "idle"),
        "current_checking_account": monitor_state.get("current_checking_account"),
        "checking_index": monitor_state.get("checking_index", 0),
        "checking_total": monitor_state.get("checking_total", 0),
        "checking_ip": monitor_state.get("checking_ip"),
        "batch_num": monitor_state.get("batch_num", 1),
        "pos_in_batch": monitor_state.get("pos_in_batch", 1),
        "batch_size": getattr(Config, "BATCH_ACCOUNTS_PER_IP", 3),
        "gap_remaining": monitor_state.get("gap_remaining", 0),
        "gap_total": monitor_state.get("gap_total", 0),
        "gap_min_seconds": Config.ACCOUNT_FETCH_GAP_MIN_SECONDS,
        "gap_max_seconds": Config.ACCOUNT_FETCH_GAP_MAX_SECONDS,
        "next_account_id": monitor_state.get("next_account_id"),
        "proxy_rotation_enabled": getattr(Config, "USE_PROXY_ROTATION", True),
        "proxy_status": proxy_manager.get_status(),
        "headless": monitor_state["headless"],
        "last_check_time": last_success or "Never",
        "alerts": alerts,
        "active_login_account": active_login_account,
        "env_email_configured": bool(Config.SWAGBUCKS_EMAIL),
        "env_has_password": bool(Config.SWAGBUCKS_PASSWORD),
    }


@app.post("/api/check-now")
async def trigger_check_now():
    """Manually trigger immediate check with anti-bot staggering and IP rotation."""
    if os.environ.get("VERCEL"):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        monitor_state["last_check_time"] = now_str
        monitor_state["countdown_seconds"] = monitor_state["interval_seconds"]
        from database import record_balance, get_latest_record
        accounts = list_saved_accounts()
        for acc_id in accounts:
            latest = get_latest_record(acc_id)
            curr = latest["sb_balance"] if latest and latest["sb_balance"] is not None else 0
            record_balance(
                account_id=acc_id,
                sb_balance=curr,
                prev_balance=curr,
                balance_change=0,
                session_status="Logged In",
                error_message=None,
            )
        return {"status": "checked", "accounts_refreshed": len(accounts), "last_check": now_str}

    if monitor_state.get("is_checking"):
        return JSONResponse(
            status_code=409,
            content={
                "status": "already_checking",
                "message": "A balance check is already running. Please wait for it to complete.",
                "current_account": monitor_state.get("current_checking_account"),
                "index": monitor_state.get("checking_index", 0),
                "total": monitor_state.get("checking_total", 0),
            }
        )

    # Immediately mark as checking to block duplicate concurrent triggers
    monitor_state["is_checking"] = True
    monitor_state["checking_state"] = "starting"
    monitor_state["last_check_status"] = "checking"
    monitor_state["last_check_message"] = "Starting account balance check..."
    monitor_state["countdown_seconds"] = 0

    # Ensure background worker is alive
    ensure_worker_running()

    if monitor_state.get("trigger_event"):
        monitor_state["trigger_event"].set()

    return {
        "status": "started",
        "message": "Check Now triggered. Checking all account balances...",
    }


@app.post("/api/timer/pause")
async def pause_timer_endpoint():
    """Pause automatic checking countdown timer."""
    monitor_state["timer_paused"] = True
    return {
        "status": "paused",
        "timer_paused": True,
        "countdown_seconds": monitor_state.get("countdown_seconds", 0),
        "message": "Automatic countdown timer paused.",
    }


@app.post("/api/timer/resume")
async def resume_timer_endpoint():
    """Resume automatic checking countdown timer."""
    monitor_state["timer_paused"] = False
    return {
        "status": "resumed",
        "timer_paused": False,
        "countdown_seconds": monitor_state.get("countdown_seconds", 0),
        "message": "Automatic countdown timer resumed.",
    }


@app.post("/api/timer/reset")
async def reset_timer_endpoint():
    """Reset countdown timer to configured interval."""
    interval = monitor_state.get("interval_seconds", Config.CHECK_INTERVAL_SECONDS)
    diff, next_str = get_seconds_to_next_run(interval)
    monitor_state["countdown_seconds"] = diff
    monitor_state["next_scheduled_check"] = next_str
    return {
        "status": "reset",
        "countdown_seconds": diff,
        "next_scheduled_check": next_str,
        "message": f"Countdown timer reset to {diff} seconds.",
    }


@app.post("/api/accounts/login-from-env")
async def login_from_env_endpoint():
    """Trigger auto-login using credentials stored in .env."""
    result = await auto_login_from_env()
    if result.get("success") and result.get("status") == "logged_in":
        if monitor_state.get("trigger_event"):
            monitor_state["trigger_event"].set()
    return result


@app.post("/api/open-account-browser/{account_id}")
@app.get("/api/open-account-browser/{account_id}")
async def open_account_browser_endpoint(account_id: str):
    """
    Launch a visible browser window for the account and auto-fill saved credentials.
    Password is never exposed in the response or URL.
    """
    return await open_account_browser_window(account_id)


@app.post("/api/settings/email")
async def update_email_settings_endpoint(req: EmailSettingsRequest):
    """
    Save and update Email configuration live in memory and into .env securely.
    Password is saved but never exposed back in the API response.
    """
    sender = req.sender_email.strip()
    receiver = req.receiver_email.strip()
    if not sender:
        raise HTTPException(status_code=400, detail="Bot/Sender Email cannot be empty.")
    if not receiver:
        raise HTTPException(status_code=400, detail="Personal/Receiver Email cannot be empty.")

    Config.update_email(
        sender_email=sender,
        receiver_email=receiver,
        sender_password=req.sender_password,
        swagbucks_email=req.swagbucks_email,
        smtp_host=req.smtp_host,
        smtp_port=req.smtp_port,
        enabled=req.enabled,
    )
    reset_email_error()

    return {
        "status": "saved",
        "message": "Email settings saved successfully.",
        "email": get_email_status(),
        "notification_status": get_notification_status(),
    }


@app.get("/api/settings/email")
async def get_email_settings_endpoint():
    """Retrieve safe email settings without exposing password."""
    return get_email_status()


@app.post("/api/email/test-connection")
async def test_smtp_connection_endpoint():
    """
    Test and diagnose SMTP host, port, TLS/SSL, and authentication.
    Does NOT send an email. Verifies credentials and connectivity safely.
    """
    success, detail, diag = await asyncio.to_thread(test_smtp_connection_sync)
    return {
        "status": "connected" if success else "failed",
        "success": success,
        "detail": detail,
        "diagnostics": diag,
        "email": get_email_status(),
    }


@app.post("/api/email/test")
async def send_test_email_endpoint():
    """
    Send a real test email from Bot/Sender Email to Personal/Receiver Email.
    Returns real confirmation or failure based on actual SMTP transmission.
    """
    success, detail = await asyncio.to_thread(send_test_email_sync)
    return {
        "status": "sent" if success else "failed",
        "success": success,
        "detail": detail,
        "email": get_email_status(),
    }


@app.post("/api/mobile/test")
async def send_mobile_test_endpoint():
    """Send an instant test notification to user's mobile phone."""
    success, detail = await asyncio.to_thread(send_mobile_test_push)
    return {
        "status": "sent" if success else "failed",
        "success": success,
        "detail": detail,
        "mobile": get_mobile_status(),
    }


@app.get("/api/settings/mobile")
async def get_mobile_settings_endpoint():
    """Retrieve current mobile notification settings."""
    return get_mobile_status()


@app.post("/api/settings/mobile")
async def update_mobile_settings_endpoint(req: MobileSettingsRequest):
    """Save and update mobile push notification settings."""
    Config.update_mobile(topic=req.topic, enabled=req.enabled)
    return {
        "status": "saved",
        "message": f"Mobile alerts configured for topic '{Config.MOBILE_PUSH_TOPIC}'.",
        "mobile": get_mobile_status(),
        "notification_status": get_notification_status(),
    }


@app.post("/api/email/test-threshold")
async def email_test_threshold_endpoint(req: ThresholdTestRequest):
    """
    Test the 530 SB threshold logic and alert email formatting.
    Verifies previous <= 530 and current > 530 rule.
    """
    threshold = Config.BALANCE_THRESHOLD
    acc_email = req.account_email.strip() if req.account_email else (Config.SWAGBUCKS_EMAIL or "example@gmail.com")

    # Evaluate exact threshold crossing rule: prev <= threshold and current > threshold
    if req.prev_balance <= threshold and req.current_balance > threshold:
        timestamp = datetime.now().strftime("%d-%b-%Y %I:%M:%S %p")
        mobile_task = asyncio.to_thread(
            send_mobile_push_alert,
            account_email=acc_email,
            prev_balance=req.prev_balance,
            current_balance=req.current_balance,
            timestamp=timestamp,
            threshold=threshold,
        )
        email_task = asyncio.to_thread(
            send_threshold_email_sync,
            account_email=acc_email,
            prev_balance=req.prev_balance,
            current_balance=req.current_balance,
            timestamp=timestamp,
            threshold=threshold,
        )
        m_res, e_res = await asyncio.gather(mobile_task, email_task, return_exceptions=True)
        m_ok = isinstance(m_res, tuple) and m_res[0] is True
        m_msg = m_res[1] if isinstance(m_res, tuple) else str(m_res)
        e_ok = isinstance(e_res, tuple) and e_res[0] is True
        e_msg = e_res[1] if isinstance(e_res, tuple) else str(e_res)

        return {
            "triggered": True,
            "condition_met": True,
            "success": m_ok or e_ok,
            "mobile_success": m_ok,
            "mobile_detail": m_msg,
            "email_success": e_ok,
            "email_detail": e_msg,
            "rule": f"prev ({req.prev_balance}) <= {threshold} and current ({req.current_balance}) > {threshold}",
            "message": "Threshold condition MET! Alert triggered for mobile phone and email.",
            "detail": f"Mobile: {m_msg} • Email: {e_msg}",
            "email": get_email_status(),
            "mobile": get_mobile_status(),
        }
    elif req.prev_balance > threshold and req.current_balance > threshold:
        return {
            "triggered": False,
            "condition_met": False,
            "success": True,
            "rule": f"prev ({req.prev_balance}) > {threshold} and current ({req.current_balance}) > {threshold}",
            "message": f"Alert SUPPRESSED: Both previous ({req.prev_balance} SB) and current ({req.current_balance} SB) are above {threshold} SB. Duplicate alert prevented.",
            "detail": "Duplicate suppression verified",
            "email": get_email_status(),
        }
    else:
        return {
            "triggered": False,
            "condition_met": False,
            "success": True,
            "rule": f"current ({req.current_balance}) <= {threshold}",
            "message": f"No alert: Current balance ({req.current_balance} SB) has not crossed above {threshold} SB.",
            "detail": "Threshold not reached",
            "email": get_email_status(),
        }


@app.post("/api/accounts/login-credentials")
async def login_credentials_endpoint(req: LoginCredentialsRequest):
    """Authenticate account using Swagbucks email and password."""
    try:
        email = req.email.strip()
        password = req.password.strip()
        if not email or not password:
            return JSONResponse(status_code=400, content={"success": False, "error": "Email and password cannot be empty."})

        acc_id = (
            req.account_id.strip()
            if (req.account_id and req.account_id.strip())
            else email.split("@")[0].replace(".", "_")
        )

        result = await login_with_credentials(acc_id, email, password, initial_sb=req.initial_sb)

        # If login succeeded, trigger immediate balance check if active
        if result.get("success") and result.get("status") == "logged_in":
            if monitor_state.get("trigger_event"):
                monitor_state["trigger_event"].set()

        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Login failed: {str(e)}"})


@app.post("/api/accounts/update-balance")
@app.post("/api/accounts/{account_id}/update-balance")
async def update_account_balance_endpoint(req: UpdateBalanceRequest, account_id: Optional[str] = None):
    """Directly update or override an account's SB balance."""
    try:
        acc_id = (account_id or req.account_id or "").strip()
        if not acc_id:
            return JSONResponse(status_code=400, content={"success": False, "error": "Account ID cannot be empty."})

        from database import (
            update_account_balance,
            can_send_daily_threshold_alert,
            get_account_email,
            record_threshold_alert,
            record_daily_threshold_alert,
        )

        res = update_account_balance(acc_id, req.sb_balance, req.note)

        # Check 530+ threshold alert
        threshold = Config.BALANCE_THRESHOLD
        if req.sb_balance >= threshold and can_send_daily_threshold_alert(acc_id):
            acc_email = get_account_email(acc_id)
            alert_res = await dispatch_crossing_alert(
                account_id=acc_id,
                account_email=acc_email,
                prev_balance=res["prev_balance"],
                current_balance=req.sb_balance,
                threshold=threshold,
            )
            record_threshold_alert(
                account_id=acc_id,
                email=acc_email,
                prev_balance=res["prev_balance"],
                current_balance=req.sb_balance,
                channels_sent=alert_res.get("channels", "None"),
                threshold=threshold,
            )
            record_daily_threshold_alert(acc_id)

        return JSONResponse(content={"success": True, "data": res})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Update failed: {str(e)}"})


@app.post("/api/sync-accounts")
async def sync_accounts_endpoint(req: SyncAccountsRequest):
    """Bulk synchronize accounts and their latest SB balances from external tracker."""
    try:
        from database import batch_sync_accounts
        count = batch_sync_accounts(req.accounts)
        return JSONResponse(content={"success": True, "status": "synced", "count": count})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Sync failed: {str(e)}"})


@app.post("/api/accounts/start-auth")
async def start_auth_endpoint(req: AddAccountRequest):
    """Launch visible browser for manual login."""
    acc_id = req.account_id.strip()
    if not acc_id:
        raise HTTPException(status_code=400, detail="Account ID cannot be empty.")

    success = await start_web_auth(acc_id, req.nickname)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to launch browser window.")
    return {"status": "browser_opened", "account_id": acc_id}


@app.post("/api/accounts/finish-auth")
async def finish_auth_endpoint(req: AddAccountRequest):
    """Complete and persist manual login session."""
    acc_id = req.account_id.strip()
    result = await finish_web_auth(acc_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Login failed."))
    
    # Trigger an immediate check to fetch balance
    if monitor_state.get("trigger_event"):
        monitor_state["trigger_event"].set()

    return result


@app.post("/api/accounts/cancel-auth")
async def cancel_auth_endpoint(req: AddAccountRequest):
    """Cancel pending login session."""
    acc_id = req.account_id.strip()
    await cancel_web_auth(acc_id)
    return {"status": "cancelled"}


@app.post("/api/accounts/{account_id}/delete")
async def delete_account_endpoint(account_id: str):
    """Delete account session and historical data."""
    remove_account_session(account_id)
    delete_account_records(account_id)
    return {"status": "deleted", "account_id": account_id}


@app.get("/api/history/{account_id}")
async def get_history_endpoint(account_id: str):
    """Return historical balance records for charts."""
    history = get_balance_history(account_id, limit=60)
    return {"account_id": account_id, "data": history}


class SaveProxiesRequest(BaseModel):
    proxies: List[str]


class TestProxyRequest(BaseModel):
    proxy: str


@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    """Update check interval, target time, threshold, anti-bot gaps, and proxy rotation."""
    Config.update_general_settings(
        interval_seconds=req.interval_seconds,
        target_time=req.target_time,
        threshold=req.threshold,
        gap_min_seconds=req.gap_min_seconds,
        gap_max_seconds=req.gap_max_seconds,
        use_proxy_rotation=req.use_proxy_rotation,
        headless=req.headless,
    )
    if req.interval_seconds is not None:
        monitor_state["interval_seconds"] = Config.CHECK_INTERVAL_SECONDS
        diff, next_str = get_seconds_to_next_run(monitor_state["interval_seconds"], monitor_state.get("last_check_time"))
        monitor_state["countdown_seconds"] = diff
        monitor_state["next_scheduled_check"] = next_str
    if req.target_time is not None:
        monitor_state["target_time"] = Config.TARGET_CHECK_TIME
    if req.headless is not None:
        monitor_state["headless"] = Config.HEADLESS

    return {
        "status": "updated",
        "message": "Settings updated and saved successfully.",
        "interval_seconds": Config.CHECK_INTERVAL_SECONDS,
        "target_time": Config.TARGET_CHECK_TIME,
        "threshold": Config.BALANCE_THRESHOLD,
        "next_scheduled_check": monitor_state.get("next_scheduled_check"),
        "headless": Config.HEADLESS,
        "use_proxy_rotation": Config.USE_PROXY_ROTATION,
        "gap_min_seconds": Config.ACCOUNT_FETCH_GAP_MIN_SECONDS,
        "gap_max_seconds": Config.ACCOUNT_FETCH_GAP_MAX_SECONDS,
    }


@app.get("/api/settings")
async def get_settings_endpoint():
    """Retrieve full safe settings for UI population."""
    return {
        "interval_seconds": Config.CHECK_INTERVAL_SECONDS,
        "target_time": Config.TARGET_CHECK_TIME,
        "threshold": Config.BALANCE_THRESHOLD,
        "gap_min_seconds": Config.ACCOUNT_FETCH_GAP_MIN_SECONDS,
        "gap_max_seconds": Config.ACCOUNT_FETCH_GAP_MAX_SECONDS,
        "use_proxy_rotation": Config.USE_PROXY_ROTATION,
        "headless": Config.HEADLESS,
        "timer_paused": monitor_state.get("timer_paused", False),
        "email": get_email_status(),
        "mobile": get_mobile_status(),
    }


@app.get("/api/proxies")
async def get_proxies_endpoint():
    """Retrieve active proxy rotation status and list."""
    return proxy_manager.get_status()


@app.post("/api/proxies")
async def save_proxies_endpoint(req: SaveProxiesRequest):
    """Save user-supplied proxy list for rotation across accounts."""
    proxy_manager.save_proxies(req.proxies)
    return {
        "status": "saved",
        "total_proxies": len(proxy_manager.proxies),
        "proxies": proxy_manager.proxies,
    }


@app.post("/api/proxies/test")
async def test_proxy_endpoint(req: TestProxyRequest):
    """Test connectivity for a single proxy."""
    working = await asyncio.to_thread(proxy_manager.test_proxy, req.proxy.strip())
    return {
        "proxy": req.proxy,
        "working": working,
        "status": "connected" if working else "failed",
    }


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the modern web dashboard interface."""
    html_path = Path(__file__).resolve().parent / "templates" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Dashboard template not found</h1>"


try:
    from generate_cert import generate_ssl_certs
except Exception:
    generate_ssl_certs = None


async def start_dual_servers():
    port = int(os.environ.get("PORT", Config.PORT))
    is_cloud = os.environ.get("PORT") is not None and str(port) != "8080"

    if is_cloud:
        print(f"[*] Cloud hosting environment detected. Binding HTTP on port {port}...")
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
        return

    try:
        if generate_ssl_certs is None:
            raise RuntimeError("generate_ssl_certs not available")
        key_path, cert_path = generate_ssl_certs()
        config_https = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=8000,
            ssl_keyfile=str(key_path),
            ssl_certfile=str(cert_path),
            log_level="info",
            lifespan="off",
        )
        config_http = uvicorn.Config(
            app,
            host=Config.HOST,
            port=Config.PORT,
            log_level="info",
            lifespan="on",
        )

        server_https = uvicorn.Server(config_https)
        server_http = uvicorn.Server(config_http)

        print("\n" + "=" * 60)
        print("[*] SWAGBUCKS MULTI-ACCOUNT WEB TRACKER IS RUNNING")
        print("=" * 60)
        print("    HTTPS (Secure): https://localhost:8000 or https://127.0.0.1:8000")
        print(f"    HTTP  (Normal): http://localhost:{Config.PORT}  or http://127.0.0.1:{Config.PORT}")
        print("=" * 60 + "\n")

        await asyncio.gather(server_https.serve(), server_http.serve())
    except Exception as e:
        print(f"[*] Starting standard HTTP server on port {port}: {e}")
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()


if __name__ == "__main__":
    asyncio.run(start_dual_servers())
