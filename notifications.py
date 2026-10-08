"""
Notification service for Swagbucks Multi-Account Tracker.
100% Pure Automatic Email Alert System (SMTP).
Delivers threshold crossing alerts, connection testing, and test emails
with precise error diagnosis and safe password redaction.
"""

import asyncio
import base64
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import re
import smtplib
import socket
import ssl
from typing import Any, Dict, Optional, Tuple
import urllib.error
import urllib.request

from config import Config

# Module-level health and delivery tracking state
_last_email_error: Optional[str] = None
_last_email_success_time: Optional[str] = None


def sanitize_secret(text: str) -> str:
    """Remove potential passwords or authentication tokens from error messages."""
    if not text:
        return ""
    cleaned = re.sub(r"(password|pass|secret|token)[:=\s]+[^\s,;&]+", r"\1: [REDACTED]", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1[REDACTED]", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"AUTH\s+[A-Za-z0-9+/=]+", "AUTH [REDACTED]", cleaned, flags=re.IGNORECASE)
    return cleaned


def is_email_configured() -> bool:
    """Check if all required SMTP settings are present."""
    return Config.is_email_configured()


def reset_email_error() -> None:
    """Reset last email error when new settings are saved."""
    global _last_email_error
    _last_email_error = None


def safe_smtp_connect_and_auth(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    use_tls: Optional[bool] = None,
    timeout: int = 15,
) -> Tuple[bool, str, Optional[smtplib.SMTP]]:
    """
    Establish an SMTP session with comprehensive error classification.
    Catches exact provider responses (e.g. BadCredentials / Invalid App Password)
    instead of letting standard smtplib mask them as 'Connection unexpectedly closed'.
    """
    target_host = (host or Config.SMTP_HOST or "smtp.gmail.com").strip()
    target_port = int(port or Config.SMTP_PORT or 587)
    target_user = (user or Config.SMTP_USER or Config.ALERT_EMAIL_FROM or "").strip()
    raw_password = (password or Config.SMTP_PASSWORD or "")
    # Clean app password: remove accidental spaces common in Google 16-char App Passwords
    clean_pwd = raw_password.strip().replace(" ", "")
    tls_enabled = use_tls if use_tls is not None else Config.SMTP_USE_TLS

    if not target_user:
        return False, "Bot/Sender Email is not configured. Please enter your Bot Email in settings.", None
    if not clean_pwd:
        return False, "Bot Email Password / App Password is not configured. Please enter your App Password in settings.", None
    if not target_host:
        return False, "SMTP Host is not configured.", None

    # Step 1: DNS Resolution Check
    try:
        socket.getaddrinfo(target_host, target_port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        return False, f"SMTP host resolution failed: Could not resolve hostname '{target_host}'. Check SMTP Host setting.", None
    except Exception as e:
        return False, f"DNS error resolving SMTP host '{target_host}': {sanitize_secret(str(e))}", None

    context = ssl.create_default_context()

    # Step 2: Establish TCP & TLS/SSL Connection
    server: Optional[smtplib.SMTP] = None
    try:
        if target_port == 465:
            server = smtplib.SMTP_SSL(target_host, target_port, context=context, timeout=timeout)
            server.ehlo()
        else:
            server = smtplib.SMTP(target_host, target_port, timeout=timeout)
            server.ehlo()
            if tls_enabled:
                server.starttls(context=context)
                server.ehlo()
    except (socket.timeout, TimeoutError):
        return False, f"SMTP connection timed out after {timeout}s: Could not connect to {target_host}:{target_port}. The port might be blocked by your network or firewall.", None
    except ConnectionRefusedError:
        return False, f"SMTP connection refused: Server at {target_host}:{target_port} refused connection. Verify port (e.g. 587 for TLS, 465 for SSL).", None
    except ssl.SSLError as e:
        return False, f"TLS/SSL handshake mismatch on {target_host}:{target_port}: {sanitize_secret(str(e))}. Try switching between port 587 (STARTTLS) and 465 (SSL).", None
    except smtplib.SMTPServerDisconnected as e:
        return False, f"SMTP server disconnected during handshake on {target_host}:{target_port}. Verify that your network allows outbound SMTP traffic.", None
    except Exception as e:
        return False, f"SMTP connection failed on {target_host}:{target_port}: {sanitize_secret(str(e))}", None

    # Step 3: Precise Authentication Handshake
    try:
        # Use AUTH LOGIN challenge-response to intercept provider status code (e.g., 535 BadCredentials)
        user_b64 = base64.b64encode(target_user.encode("utf-8")).decode("ascii")
        pass_b64 = base64.b64encode(clean_pwd.encode("utf-8")).decode("ascii")

        code, resp = server.docmd("AUTH LOGIN", user_b64)
        resp_str = resp.decode("utf-8", errors="ignore") if isinstance(resp, bytes) else str(resp)

        if code == 334:
            # Server prompted for password
            code2, resp2 = server.docmd(pass_b64)
            resp2_str = resp2.decode("utf-8", errors="ignore") if isinstance(resp2, bytes) else str(resp2)

            if code2 == 235:
                # Successfully authenticated
                return True, "SMTP connection and authentication successful!", server
            elif code2 == 535 or "badcredentials" in resp2_str.lower() or "not accepted" in resp2_str.lower():
                try:
                    server.close()
                except Exception:
                    pass
                if "gmail" in target_host.lower() or "google" in target_host.lower():
                    return (
                        False,
                        "Authentication failed: Invalid Bot Email or App Password. "
                        "Gmail rejected the login (535 BadCredentials). "
                        "Google requires a 16-character Google App Password (not your regular account password). "
                        "To generate one: Google Account -> Security -> 2-Step Verification -> App Passwords.",
                        None,
                    )
                return False, f"Authentication failed: SMTP provider rejected credentials (code {code2}): {sanitize_secret(resp2_str)}", None
            else:
                try:
                    server.close()
                except Exception:
                    pass
                return False, f"Authentication failed (code {code2}): {sanitize_secret(resp2_str)}", None
        elif code == 535:
            try:
                server.close()
            except Exception:
                pass
            return False, f"Authentication failed: Username not accepted by SMTP server (code 535): {sanitize_secret(resp_str)}", None
        else:
            # Fallback to standard login method
            server.login(target_user, clean_pwd)
            return True, "SMTP connection and authentication successful!", server

    except smtplib.SMTPAuthenticationError as e:
        if server:
            try:
                server.close()
            except Exception:
                pass
        err_msg = str(e)
        if "badcredentials" in err_msg.lower() or "535" in err_msg:
            return (
                False,
                "Authentication failed: Invalid Bot Email or App Password. "
                "Gmail rejected credentials (535 BadCredentials). "
                "You must use a 16-character Google App Password (not your normal Google account password).",
                None,
            )
        return False, f"Authentication failed: {sanitize_secret(err_msg)}", None

    except smtplib.SMTPServerDisconnected as e:
        if server:
            try:
                server.close()
            except Exception:
                pass
        if "gmail" in target_host.lower():
            return (
                False,
                "Authentication failed (Gmail connection closed): Google closed the connection because the password was rejected. "
                "Google does not allow standard account passwords for SMTP. "
                "You MUST generate a 16-character Google App Password in Google Account -> Security -> App Passwords.",
                None,
            )
        return False, f"SMTP server disconnected during authentication: {sanitize_secret(str(e))}", None

    except Exception as e:
        if server:
            try:
                server.close()
            except Exception:
                pass
        return False, f"SMTP authentication error: {sanitize_secret(str(e))}", None


def test_smtp_connection_sync() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Test and diagnose SMTP connection & authentication without sending an email.
    Returns (success, message, diagnostics).
    """
    global _last_email_error
    host = Config.SMTP_HOST
    port = Config.SMTP_PORT
    user = Config.SMTP_USER
    masked_user = Config.mask_email(user)
    has_pwd = bool(Config.SMTP_PASSWORD)

    start_time = datetime.now()
    success, detail, server = safe_smtp_connect_and_auth()
    duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

    if server:
        try:
            server.quit()
        except Exception:
            pass

    if success:
        _last_email_error = None
    else:
        _last_email_error = detail

    diag = {
        "host": host,
        "port": port,
        "user_masked": masked_user,
        "has_password": has_pwd,
        "latency_ms": duration_ms,
        "protocol": "SSL" if port == 465 else "STARTTLS",
    }
    return success, detail, diag


def get_email_status() -> Dict[str, Any]:
    """Return sanitized email alert system status without exposing passwords."""
    global _last_email_error, _last_email_success_time
    configured = is_email_configured()

    if not configured:
        status = "Not Configured"
    elif _last_email_error:
        status = "Error"
    else:
        status = "Configured"

    return {
        "status": status,
        "configured": configured,
        "enabled": Config.EMAIL_ALERTS_ENABLED,
        "sender_email": Config.SMTP_USER,
        "sender_email_masked": Config.mask_email(Config.SMTP_USER),
        "receiver_email": Config.ALERT_EMAIL_TO,
        "receiver_email_masked": Config.mask_email(Config.ALERT_EMAIL_TO),
        "has_password": bool(Config.SMTP_PASSWORD),
        "smtp_host": Config.SMTP_HOST,
        "smtp_port": Config.SMTP_PORT,
        "smtp_use_tls": Config.SMTP_USE_TLS,
        "threshold": Config.BALANCE_THRESHOLD,
        "last_error": _last_email_error,
        "last_success": _last_email_success_time,
    }


def get_mobile_status() -> Dict[str, Any]:
    """Return status of instant mobile push alert system."""
    topic = Config.MOBILE_PUSH_TOPIC or "sb_alerts_9676061877"
    return {
        "enabled": Config.MOBILE_ALERTS_ENABLED,
        "configured": True,
        "topic": topic,
        "push_url": f"https://ntfy.sh/{topic}",
        "subscribe_url": f"https://ntfy.sh/{topic}",
        "threshold": Config.BALANCE_THRESHOLD,
    }


def send_mobile_push_alert(
    account_email: str,
    prev_balance: int,
    current_balance: int,
    timestamp: str,
    threshold: int = 530,
) -> Tuple[bool, str]:
    """
    Send instant mobile push notification directly to user's phone lock screen.
    Zero password, zero API key, guaranteed real-time push with sound and vibration.
    """
    if not Config.MOBILE_ALERTS_ENABLED:
        return False, "Mobile push notifications disabled in settings."

    topic = Config.MOBILE_PUSH_TOPIC or "sb_alerts_9676061877"
    url = f"https://ntfy.sh/{topic}"

    from database import get_account_email
    email_display = account_email if (account_email and "@" in account_email) else get_account_email(account_email or "")
    if not email_display or "@" not in email_display:
        email_display = Config.SWAGBUCKS_EMAIL or account_email or "swagbucks_user@domain.com"

    title = f"SB 530+ Reached: {email_display}"
    body = (
        f"🎯 SB Balance Reached {threshold} SB!\n\n"
        f"• Account Email: {email_display}\n"
        f"• Current Balance: {current_balance} SB\n"
        f"• Previous Balance: {prev_balance} SB\n"
        f"• Target Threshold: {threshold} SB\n"
        f"• Status: Reached or exceeded {threshold} SB\n"
        f"• Detected At: {timestamp}"
    )

    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "urgent",
            "Tags": "moneybag,bell,tada",
            "Click": "https://palm-metres-basename-guards.trycloudflare.com",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                print(f"[+] Instant Mobile Alert sent to phone for {account_email} (Topic: {topic})")
                return True, f"Mobile push alert delivered to topic '{topic}'"
            return False, f"Mobile push returned status {resp.status}"
    except Exception as e:
        err = f"Failed to send mobile alert: {sanitize_secret(str(e))}"
        print(f"[!] {err}")
        return False, err


def send_mobile_test_push() -> Tuple[bool, str]:
    """Send a real instant test alert to user's mobile phone."""
    timestamp = datetime.now().strftime("%d-%b-%Y %I:%M:%S %p")
    topic = Config.MOBILE_PUSH_TOPIC or "sb_alerts_9676061877"
    url = f"https://ntfy.sh/{topic}"

    title = "Swagbucks Tracker - Mobile Alert Test"
    body = (
        "✅ Mobile alerts are working perfectly!\n\n"
        f"• Device Topic: {topic}\n"
        f"• Alert Threshold: {Config.BALANCE_THRESHOLD} SB\n"
        f"• Last Checked Time: {timestamp}\n\n"
        "Your phone will instantly ring & vibrate whenever any account crosses 530 SB!"
    )

    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "white_check_mark,iphone,bell",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                return True, f"Mobile test alert sent to topic '{topic}'! Check your phone."
            return False, f"Mobile test push returned status {resp.status}"
    except Exception as e:
        return False, f"Failed to send mobile test alert: {sanitize_secret(str(e))}"


def get_notification_status() -> Dict[str, Any]:
    """Inspect alert system configuration status for dashboard overview."""
    email_info = get_email_status()
    mobile_info = get_mobile_status()

    parts = []
    if mobile_info["enabled"]:
        parts.append(f"Mobile Push Active ({mobile_info['topic']})")
    if email_info["configured"] and email_info["status"] != "Error":
        parts.append(f"Email Active ({email_info['receiver_email_masked']})")

    summary = " • ".join(parts) if parts else "Alerts Configured"

    return {
        "mobile": mobile_info,
        "email": email_info,
        "email_enabled": Config.EMAIL_ALERTS_ENABLED,
        "email_configured": email_info["configured"],
        "mobile_enabled": mobile_info["enabled"],
        "status": "Active" if (mobile_info["enabled"] or email_info["configured"]) else "Not Configured",
        "summary": summary,
        "threshold": Config.BALANCE_THRESHOLD,
    }


def send_test_email_sync() -> Tuple[bool, str]:
    """
    Send a real test email from Bot/Sender Email to Personal/Receiver Email.
    Uses robust SMTP connection with detailed error handling.
    """
    global _last_email_error, _last_email_success_time

    if not Config.ALERT_EMAIL_TO:
        err = "Personal/Receiver Email is not configured. Please enter your Personal Email in settings."
        _last_email_error = err
        return False, f"Test email failed: {err}"

    # 1. Connect and Authenticate
    success, detail, server = safe_smtp_connect_and_auth()
    if not success or not server:
        _last_email_error = detail
        return False, f"Test email failed: {detail}"

    sender_email = Config.ALERT_EMAIL_FROM or Config.SMTP_USER
    receiver_email = Config.ALERT_EMAIL_TO
    timestamp = datetime.now().strftime("%d-%b-%Y %I:%M:%S %p")

    subject = "Swagbucks Tracker - Test Email"

    body_text = (
        "This is a test email from the Swagbucks Multi Tracker.\n\n"
        "Email alerts are configured correctly.\n\n"
        "Sender:\n"
        f"{sender_email}\n\n"
        "Receiver:\n"
        f"{receiver_email}\n\n"
        "Time:\n"
        f"{timestamp}\n"
    )

    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin: 0; padding: 24px; background-color: #0b0f19; font-family: 'Segoe UI', Arial, sans-serif; color: #f3f4f6;">
  <div style="max-width: 560px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 28px; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
    <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
      <div style="background: linear-gradient(135deg, #2563eb, #10b981); color: #fff; width: 38px; height: 38px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; font-weight: bold; font-size: 16px;">SB</div>
      <h2 style="margin: 0; color: #ffffff; font-size: 20px;">Swagbucks Multi Tracker</h2>
    </div>
    
    <div style="background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 8px; padding: 14px 16px; margin-bottom: 22px;">
      <p style="margin: 0; font-size: 15px; font-weight: 600; color: #34d399;">
        This is a test email from the Swagbucks Multi Tracker.
      </p>
      <p style="margin: 6px 0 0 0; font-size: 13px; color: #a7f3d0;">
        Email alerts are configured correctly.
      </p>
    </div>

    <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
      <tr style="border-bottom: 1px solid #1f2937;">
        <td style="padding: 10px 0; color: #9ca3af; font-weight: 600;">Sender:</td>
        <td style="padding: 10px 0; color: #60a5fa; text-align: right; font-family: monospace;">{sender_email}</td>
      </tr>
      <tr style="border-bottom: 1px solid #1f2937;">
        <td style="padding: 10px 0; color: #9ca3af; font-weight: 600;">Receiver:</td>
        <td style="padding: 10px 0; color: #34d399; text-align: right; font-family: monospace;">{receiver_email}</td>
      </tr>
      <tr>
        <td style="padding: 10px 0; color: #9ca3af; font-weight: 600;">Time:</td>
        <td style="padding: 10px 0; color: #f3f4f6; text-align: right;">{timestamp}</td>
      </tr>
    </table>

    <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid #1f2937; text-align: center; font-size: 11px; color: #6b7280;">
      Automated verification dispatched by Swagbucks Multi Tracker
    </div>
  </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        server.sendmail(sender_email, [receiver_email], msg.as_string())
        server.quit()
        _last_email_error = None
        _last_email_success_time = timestamp
        print(f"[+] Test email sent successfully to {receiver_email} via {Config.SMTP_HOST}")
        return True, "Test email sent successfully"
    except Exception as e:
        err = sanitize_secret(str(e))
        _last_email_error = err
        try:
            server.close()
        except Exception:
            pass
        return False, f"Test email delivery failed: {err}"


def send_threshold_email_sync(
    account_email: str,
    prev_balance: int,
    current_balance: int,
    timestamp: str,
    threshold: int = 530,
) -> Tuple[bool, str]:
    """
    Send automatic threshold crossing alert email when an account crosses 530 SB.
    Matches exact requested Subject and Body specifications:
      Subject: Swagbucks Alert – Balance Crossed 530 SB
    """
    global _last_email_error, _last_email_success_time

    if not Config.EMAIL_ALERTS_ENABLED:
        return False, "Email alerts are disabled in configuration."

    if not Config.ALERT_EMAIL_TO:
        err = "Personal Receiver Email is not configured. Missing ALERT_EMAIL_TO."
        _last_email_error = err
        return False, err

    # 1. Connect and Authenticate
    success, detail, server = safe_smtp_connect_and_auth()
    if not success or not server:
        _last_email_error = detail
        return False, f"Email delivery failed: {detail}"

    sender_email = Config.ALERT_EMAIL_FROM or Config.SMTP_USER
    receiver_email = Config.ALERT_EMAIL_TO

    subject = f"Swagbucks Alert – Balance Crossed {threshold} SB"

    body_text = (
        "Swagbucks Balance Alert\n\n"
        "Account Email:\n"
        f"{account_email}\n\n"
        "Previous Balance:\n"
        f"{prev_balance} SB\n\n"
        "Current Balance:\n"
        f"{current_balance} SB\n\n"
        "Threshold:\n"
        f"{threshold} SB\n\n"
        "Status:\n"
        f"Balance crossed above {threshold} SB\n\n"
        "Detected At:\n"
        f"{timestamp}\n"
    )

    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin: 0; padding: 24px; background-color: #0b0f19; font-family: 'Segoe UI', Arial, sans-serif; color: #f3f4f6;">
  <div style="max-width: 580px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 28px; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
    <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; border-bottom: 1px solid #1f2937; padding-bottom: 16px;">
      <h2 style="margin: 0; color: #34d399; font-size: 22px;">🎯 Swagbucks Balance Alert</h2>
      <span style="background: rgba(16, 185, 129, 0.2); color: #34d399; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: bold; border: 1px solid rgba(16, 185, 129, 0.4);">&gt; {threshold} SB Crossed</span>
    </div>

    <div style="background: #0d1322; border: 1px solid #1f2937; border-radius: 10px; padding: 18px; margin-bottom: 20px;">
      <div style="font-size: 12px; color: #9ca3af; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">Account Email</div>
      <div style="font-size: 18px; font-weight: 700; color: #ffffff; margin-top: 4px; word-break: break-all;">{account_email}</div>
    </div>

    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 14px;">
      <tr style="border-bottom: 1px solid #1f2937;">
        <td style="padding: 12px 6px; color: #9ca3af; font-weight: 600;">Previous Balance:</td>
        <td style="padding: 12px 6px; font-weight: 700; color: #f87171; text-align: right; font-size: 16px;">{prev_balance} SB</td>
      </tr>
      <tr style="border-bottom: 1px solid #1f2937;">
        <td style="padding: 12px 6px; color: #9ca3af; font-weight: 600;">Current Balance:</td>
        <td style="padding: 12px 6px; font-weight: 800; color: #34d399; text-align: right; font-size: 20px;">{current_balance} SB</td>
      </tr>
      <tr style="border-bottom: 1px solid #1f2937;">
        <td style="padding: 12px 6px; color: #9ca3af; font-weight: 600;">Threshold:</td>
        <td style="padding: 12px 6px; font-weight: 700; color: #93c5fd; text-align: right;">{threshold} SB</td>
      </tr>
      <tr style="border-bottom: 1px solid #1f2937;">
        <td style="padding: 12px 6px; color: #9ca3af; font-weight: 600;">Status:</td>
        <td style="padding: 12px 6px; font-weight: 700; color: #34d399; text-align: right;">Balance crossed above {threshold} SB</td>
      </tr>
      <tr>
        <td style="padding: 12px 6px; color: #9ca3af; font-weight: 600;">Detected At:</td>
        <td style="padding: 12px 6px; color: #cbd5e1; text-align: right; font-size: 13px;">{timestamp}</td>
      </tr>
    </table>

    <div style="background: rgba(37, 99, 235, 0.08); border-left: 3px solid #3b82f6; padding: 12px 14px; border-radius: 4px; font-size: 12px; color: #93c5fd; margin-bottom: 20px;">
      ⚡ Automated alert sent immediately upon threshold crossing. Zero human intervention required.
    </div>

    <div style="border-top: 1px solid #1f2937; padding-top: 14px; font-size: 11px; color: #6b7280; text-align: center;">
      Sender: {sender_email} &bull; Receiver: {receiver_email}
    </div>
  </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        server.sendmail(sender_email, [receiver_email], msg.as_string())
        server.quit()

        _last_email_error = None
        _last_email_success_time = timestamp
        print(f"[+] Threshold alert email sent successfully to {receiver_email} for {account_email} ({prev_balance} -> {current_balance} SB)")
        return True, "Email alert sent successfully"

    except Exception as e:
        err = sanitize_secret(str(e))
        _last_email_error = err
        try:
            server.close()
        except Exception:
            pass
        return False, f"Email delivery failed: {err}"


async def dispatch_crossing_alert(
    account_id: str,
    account_email: str,
    prev_balance: int,
    current_balance: int,
    threshold: int = 530,
) -> Dict[str, Any]:
    """
    Dispatch automatic threshold crossing alert across active channels (Mobile Push + Email).
    Runs asynchronously in thread pool so background balance polling is never blocked.
    """
    timestamp = datetime.now().strftime("%d-%b-%Y %I:%M:%S %p")
    channels = []

    # 1. Primary: Instant Mobile Push directly to phone
    mobile_task = asyncio.to_thread(
        send_mobile_push_alert,
        account_email=account_email,
        prev_balance=prev_balance,
        current_balance=current_balance,
        timestamp=timestamp,
        threshold=threshold,
    )

    # 2. Secondary: SMTP Email (if configured)
    email_task = asyncio.to_thread(
        send_threshold_email_sync,
        account_email=account_email,
        prev_balance=prev_balance,
        current_balance=current_balance,
        timestamp=timestamp,
        threshold=threshold,
    )

    mobile_res, email_res = await asyncio.gather(mobile_task, email_task, return_exceptions=True)

    mobile_sent = isinstance(mobile_res, tuple) and mobile_res[0] is True
    mobile_detail = mobile_res[1] if isinstance(mobile_res, tuple) else str(mobile_res)

    email_sent = isinstance(email_res, tuple) and email_res[0] is True
    email_detail = email_res[1] if isinstance(email_res, tuple) else str(email_res)

    if mobile_sent:
        channels.append("Mobile Push")
    if email_sent:
        channels.append("Email")

    return {
        "account_id": account_id,
        "account_email": account_email,
        "prev_balance": prev_balance,
        "current_balance": current_balance,
        "threshold": threshold,
        "timestamp": timestamp,
        "mobile_sent": mobile_sent,
        "mobile_detail": mobile_detail,
        "email_sent": email_sent,
        "email_detail": email_detail,
        "channels": ", ".join(channels) if channels else "None",
    }


def send_mobile_tracking_failure_alert(
    account_email: str,
    error_reason: str,
    timestamp: str,
) -> Tuple[bool, str]:
    """Disabled by user request: No notifications on tracking stoppage."""
    return False, "Tracking failure alerts disabled by user preference."


def send_email_tracking_failure_alert(
    account_email: str,
    error_reason: str,
    timestamp: str,
) -> Tuple[bool, str]:
    """Disabled by user request: No notifications on tracking stoppage."""
    return False, "Tracking failure alerts disabled by user preference."


async def dispatch_tracking_failure_alert(
    account_id: str,
    account_email: str,
    error_reason: str,
) -> Dict[str, Any]:
    """Disabled by user request: No notifications on tracking stoppage."""
    return {
        "account_id": account_id,
        "account_email": account_email,
        "error_reason": error_reason,
        "mobile_sent": False,
        "email_sent": False,
        "status": "disabled",
    }
