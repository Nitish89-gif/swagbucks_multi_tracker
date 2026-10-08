"""
Web application backend for Swagbucks Multi-Account Tracker.
Built with FastAPI, Playwright Async, and SQLite.
100% Pure Automatic Email Alert System (SMTP).
"""

import asyncio
from datetime import datetime
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

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

# Global monitoring state
monitor_state: Dict[str, Any] = {
    "interval_seconds": Config.CHECK_INTERVAL_SECONDS,
    "countdown_seconds": Config.CHECK_INTERVAL_SECONDS,
    "is_checking": False,
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


class SettingsRequest(BaseModel):
    interval_seconds: int
    headless: bool


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
    """Background asynchronous task that manages concurrent polling and countdown."""
    while True:
        try:
            # 1. Fetch all accounts
            accounts = list_saved_accounts()
            if accounts:
                monitor_state["is_checking"] = True
                try:
                    await monitor_accounts_simultaneously(
                        accounts, headless=monitor_state["headless"]
                    )
                    monitor_state["last_check_time"] = datetime.now().strftime("%H:%M:%S")
                except Exception as e:
                    print(f"[!] Error in background check: {e}")
                finally:
                    monitor_state["is_checking"] = False

            # 2. Countdown loop
            monitor_state["countdown_seconds"] = monitor_state["interval_seconds"]
            for _ in range(monitor_state["interval_seconds"]):
                await asyncio.sleep(1)
                monitor_state["countdown_seconds"] = max(0, monitor_state["countdown_seconds"] - 1)
                # Check if manual check was triggered
                if monitor_state["trigger_event"].is_set():
                    monitor_state["trigger_event"].clear()
                    break

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[!] Unexpected error in monitor loop: {e}")
            await asyncio.sleep(5)


@app.on_event("startup")
async def on_startup():
    """Initialize DB and start background monitoring worker once."""
    if monitor_state.get("task") is None:
        init_db()
        monitor_state["trigger_event"] = asyncio.Event()
        monitor_state["task"] = asyncio.create_task(background_monitoring_worker())

        # Auto-login from .env on startup if credentials configured and not yet active
        if Config.SWAGBUCKS_EMAIL and Config.SWAGBUCKS_PASSWORD:
            env_acc_id = Config.SWAGBUCKS_EMAIL.split("@")[0].replace(".", "_")
            if env_acc_id not in list_saved_accounts():
                print(f"[*] Found credentials for {Config.SWAGBUCKS_EMAIL} in .env. Initiating auto-login...")
                asyncio.create_task(auto_login_from_env())


@app.on_event("shutdown")
async def on_shutdown():
    """Cancel background worker on shutdown."""
    if monitor_state.get("task"):
        monitor_state["task"].cancel()
        monitor_state["task"] = None


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

        status = stats["status"]
        current_sb = stats["current_sb"]
        latest_change = stats["latest_change"]
        today_change = stats["today_change"]

        total_sb += current_sb
        total_latest_change += latest_change
        total_today_change += today_change

        display_status = "Logged In"

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
        "accounts_above_530": accounts_above_530,
        "count_above_530": len(accounts_above_530),
        "threshold": Config.BALANCE_THRESHOLD,
        "tracker_status": "Running" if tracker_alive else "Error",
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
        "countdown_seconds": monitor_state["countdown_seconds"],
        "interval_seconds": monitor_state["interval_seconds"],
        "is_checking": monitor_state["is_checking"],
        "headless": monitor_state["headless"],
        "last_check_time": monitor_state["last_check_time"],
        "alerts": alerts,
        "active_login_account": active_login_account,
        "env_email_configured": bool(Config.SWAGBUCKS_EMAIL),
        "env_has_password": bool(Config.SWAGBUCKS_PASSWORD),
    }


@app.post("/api/check-now")
async def trigger_check_now():
    """Manually trigger immediate simultaneous check."""
    if monitor_state["is_checking"]:
        return {"status": "already_checking"}
    if monitor_state["trigger_event"]:
        monitor_state["trigger_event"].set()
    return {"status": "triggered"}


@app.post("/api/accounts/login-from-env")
async def login_from_env_endpoint():
    """Trigger auto-login using credentials stored in .env."""
    result = await auto_login_from_env()
    if result.get("success") and result.get("status") == "logged_in":
        if monitor_state["trigger_event"]:
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
    email = req.email.strip()
    password = req.password.strip()
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password cannot be empty.")

    acc_id = (
        req.account_id.strip()
        if (req.account_id and req.account_id.strip())
        else email.split("@")[0].replace(".", "_")
    )

    result = await login_with_credentials(acc_id, email, password)

    # If login succeeded, trigger immediate balance check
    if result.get("success") and result.get("status") == "logged_in":
        if monitor_state["trigger_event"]:
            monitor_state["trigger_event"].set()

    return result


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
    if monitor_state["trigger_event"]:
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


@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    """Update check interval and headless mode."""
    monitor_state["interval_seconds"] = max(10, req.interval_seconds)
    monitor_state["headless"] = req.headless
    return {
        "status": "updated",
        "interval_seconds": monitor_state["interval_seconds"],
        "headless": monitor_state["headless"],
    }


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the modern web dashboard interface."""
    html_path = Path(__file__).resolve().parent / "templates" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>Dashboard template not found</h1>"


from generate_cert import generate_ssl_certs


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
        key_path, cert_path = generate_ssl_certs()
        config_https = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=8000,
            ssl_keyfile=str(key_path),
            ssl_certfile=str(cert_path),
            log_level="info",
        )
        config_http = uvicorn.Config(
            app,
            host=Config.HOST,
            port=Config.PORT,
            log_level="info",
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
