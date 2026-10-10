"""
Configuration module for Swagbucks Multi-Account Tracker.
Loads settings from .env file securely. Never exposes passwords.
Pure Email Alert System.
"""

import os
from pathlib import Path
from typing import Optional, Tuple
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

# Load .env if present
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()


class Config:
    # Server & Runtime
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))
    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")
    CHECK_INTERVAL_SECONDS: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "86400"))  # Default 24 hours (86400s)
    SCHEDULE_DAILY_AT_MIDNIGHT: bool = os.getenv("SCHEDULE_DAILY_AT_MIDNIGHT", "true").lower() in ("true", "1", "yes")
    TARGET_CHECK_TIME: str = os.getenv("TARGET_CHECK_TIME", "00:00").strip()  # 00:00 (12:00 AM Midnight start of day)
    ACCOUNT_FETCH_GAP_MIN_SECONDS: int = int(os.getenv("ACCOUNT_FETCH_GAP_MIN_SECONDS", "120"))  # 2 minutes gap
    ACCOUNT_FETCH_GAP_MAX_SECONDS: int = int(os.getenv("ACCOUNT_FETCH_GAP_MAX_SECONDS", "180"))  # 3 minutes gap
    USE_PROXY_ROTATION: bool = os.getenv("USE_PROXY_ROTATION", "true").lower() in ("true", "1", "yes")
    PROXY_LIST: str = os.getenv("PROXY_LIST", "").strip()
    BALANCE_THRESHOLD: int = int(os.getenv("BALANCE_THRESHOLD", "530"))
    MAX_CONCURRENT_CHECKS: int = int(os.getenv("MAX_CONCURRENT_CHECKS", "1"))

    # Swagbucks Auto-Login Credentials (Optional from .env)
    SWAGBUCKS_EMAIL: Optional[str] = os.getenv("SWAGBUCKS_EMAIL", "nitishjaya2011@outlook.com")
    SWAGBUCKS_PASSWORD: Optional[str] = os.getenv("SWAGBUCKS_PASSWORD", "")

    # Instant Mobile Push Notifications (Zero Setup, Zero Password, Direct to Mobile Lock Screen)
    MOBILE_ALERTS_ENABLED: bool = os.getenv("MOBILE_ALERTS_ENABLED", "true").lower() in ("true", "1", "yes")
    MOBILE_PUSH_TOPIC: str = os.getenv("MOBILE_PUSH_TOPIC", "sb_alerts_9676061877").strip()

    # Email Notifications (SMTP) - Optional Automatic Alert System
    EMAIL_ALERTS_ENABLED: bool = os.getenv("EMAIL_ALERTS_ENABLED", "true").lower() in ("true", "1", "yes")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "").strip() or os.getenv("ALERT_EMAIL_FROM", "").strip()
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "").strip()
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
    ALERT_EMAIL_FROM: str = os.getenv("ALERT_EMAIL_FROM", "").strip() or os.getenv("SMTP_USER", "").strip()
    ALERT_EMAIL_TO: str = os.getenv("ALERT_EMAIL_TO", "").strip()

    @classmethod
    def detect_smtp_settings(cls, email: str) -> Tuple[str, int]:
        """Automatically detect SMTP host and port based on domain."""
        if not email or "@" not in email:
            return "smtp.gmail.com", 587
        domain = email.lower().split("@")[1].strip()
        if "gmail.com" in domain:
            return "smtp.gmail.com", 587
        elif any(d in domain for d in ("outlook.com", "hotmail.com", "live.com", "office365.com")):
            return "smtp-mail.outlook.com", 587
        elif "yahoo.com" in domain:
            return "smtp.mail.yahoo.com", 587
        elif "zoho.com" in domain:
            return "smtp.zoho.com", 587
        elif "icloud.com" in domain:
            return "smtp.mail.me.com", 587
        return "smtp.gmail.com", 587

    @classmethod
    def mask_email(cls, email: str) -> str:
        """Safely mask email address (e.g. user@gmail.com -> u***r@gmail.com)."""
        if not email or "@" not in email:
            return "Not Configured"
        parts = email.strip().split("@", 1)
        user, domain = parts[0], parts[1]
        if len(user) <= 2:
            masked_user = user[0] + "*"
        else:
            masked_user = user[0] + ("*" * (len(user) - 2)) + user[-1]
        return f"{masked_user}@{domain}"

    @classmethod
    def is_email_configured(cls) -> bool:
        """Check if email settings are sufficient for sending real alerts."""
        return bool(
            cls.EMAIL_ALERTS_ENABLED
            and cls.SMTP_HOST
            and cls.SMTP_USER
            and cls.SMTP_PASSWORD
            and cls.ALERT_EMAIL_TO
        )

    @classmethod
    def save_to_env(cls, updates: dict) -> None:
        """Update or append keys in .env file while preserving comments."""
        lines = []
        if ENV_PATH.exists():
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

        remaining_keys = set(updates.keys())
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    new_lines.append(f"{key}={updates[key]}")
                    remaining_keys.discard(key)
                    continue
            new_lines.append(line)

        for key in remaining_keys:
            new_lines.append(f"{key}={updates[key]}")

        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    @classmethod
    def update_email(
        cls,
        sender_email: str,
        receiver_email: str,
        sender_password: Optional[str] = None,
        swagbucks_email: Optional[str] = None,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        enabled: bool = True,
    ) -> None:
        """Dynamically update Email settings in-memory and write to .env securely."""
        clean_sender = sender_email.strip()
        clean_receiver = receiver_email.strip()
        cls.SMTP_USER = clean_sender
        cls.ALERT_EMAIL_FROM = clean_sender
        cls.ALERT_EMAIL_TO = clean_receiver
        cls.EMAIL_ALERTS_ENABLED = enabled

        if sender_password and sender_password.strip():
            cls.SMTP_PASSWORD = sender_password.strip()

        if swagbucks_email is not None and swagbucks_email.strip():
            cls.SWAGBUCKS_EMAIL = swagbucks_email.strip()

        # Host detection / override
        if smtp_host and smtp_host.strip():
            cls.SMTP_HOST = smtp_host.strip()
        elif clean_sender:
            detected_host, detected_port = cls.detect_smtp_settings(clean_sender)
            cls.SMTP_HOST = detected_host
            if not smtp_port:
                cls.SMTP_PORT = detected_port

        if smtp_port:
            cls.SMTP_PORT = int(smtp_port)

        updates = {
            "EMAIL_ALERTS_ENABLED": "true" if enabled else "false",
            "SMTP_USER": cls.SMTP_USER,
            "ALERT_EMAIL_FROM": cls.ALERT_EMAIL_FROM,
            "ALERT_EMAIL_TO": cls.ALERT_EMAIL_TO,
            "SMTP_HOST": cls.SMTP_HOST,
            "SMTP_PORT": str(cls.SMTP_PORT),
            "SMTP_USE_TLS": "true" if cls.SMTP_USE_TLS else "false",
        }
        if cls.SMTP_PASSWORD:
            updates["SMTP_PASSWORD"] = cls.SMTP_PASSWORD
        if cls.SWAGBUCKS_EMAIL:
            updates["SWAGBUCKS_EMAIL"] = cls.SWAGBUCKS_EMAIL

        cls.save_to_env(updates)

    @classmethod
    def get_mobile_push_url(cls) -> str:
        """Return the subscription URL for instant mobile push alerts."""
        return f"https://ntfy.sh/{cls.MOBILE_PUSH_TOPIC}"

    @classmethod
    def update_mobile(cls, topic: str, enabled: bool = True) -> None:
        """Update mobile push topic and enabled state."""
        clean_topic = topic.strip() or "sb_alerts_9676061877"
        cls.MOBILE_PUSH_TOPIC = clean_topic
        cls.MOBILE_ALERTS_ENABLED = enabled
        cls.save_to_env({
            "MOBILE_ALERTS_ENABLED": "true" if enabled else "false",
            "MOBILE_PUSH_TOPIC": clean_topic,
        })

    @classmethod
    def get_safe_dict(cls) -> dict:
        """Return safe config dictionary with passwords strictly redacted."""
        return {
            "HOST": cls.HOST,
            "PORT": cls.PORT,
            "HEADLESS": cls.HEADLESS,
            "CHECK_INTERVAL_SECONDS": cls.CHECK_INTERVAL_SECONDS,
            "BALANCE_THRESHOLD": cls.BALANCE_THRESHOLD,
            "SWAGBUCKS_EMAIL": cls.SWAGBUCKS_EMAIL if cls.SWAGBUCKS_EMAIL else None,
            "HAS_SWAGBUCKS_PASSWORD": bool(cls.SWAGBUCKS_PASSWORD),
            "MOBILE_ALERTS_ENABLED": cls.MOBILE_ALERTS_ENABLED,
            "MOBILE_PUSH_TOPIC": cls.MOBILE_PUSH_TOPIC,
            "MOBILE_PUSH_URL": cls.get_mobile_push_url(),
            "EMAIL_ALERTS_ENABLED": cls.EMAIL_ALERTS_ENABLED,
            "SMTP_HOST": cls.SMTP_HOST,
            "SMTP_PORT": cls.SMTP_PORT,
            "BOT_SENDER_EMAIL": cls.SMTP_USER,
            "BOT_SENDER_MASKED": cls.mask_email(cls.SMTP_USER),
            "RECEIVER_EMAIL": cls.ALERT_EMAIL_TO,
            "RECEIVER_EMAIL_MASKED": cls.mask_email(cls.ALERT_EMAIL_TO),
            "HAS_EMAIL_PASSWORD": bool(cls.SMTP_PASSWORD),
            "IS_CONFIGURED": cls.is_email_configured(),
        }
