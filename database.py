"""
Database management module for Swagbucks Multi-Account Tracker.
Handles SQLite persistence for account sessions, historical balances, and stats.
"""

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent
SEED_DB_PATH = PROJECT_ROOT / "data" / "sb_history_seed.db"

if os.environ.get("VERCEL"):
    DB_DIR = Path("/tmp/data")
else:
    DB_DIR = PROJECT_ROOT / "data"
DB_PATH = DB_DIR / "sb_history.db"

DEFAULT_SEED_ACCOUNTS = [
    {
        "account_id": "pasumarthinitish",
        "nickname": "pasumarthinitish@gmail.com",
        "email": "pasumarthinitish@gmail.com",
        "sb_balance": 189,
        "timestamp": "2026-10-09 12:11:59",
    },
    {
        "account_id": "nitishjaya2006",
        "nickname": "nitishjaya2006@gmail.com",
        "email": "nitishjaya2006@gmail.com",
        "sb_balance": 278,
        "timestamp": "2026-10-09 12:13:10",
    },
    {
        "account_id": "nitishjaya2007",
        "nickname": "nitishjaya2007@gmail.com",
        "email": "nitishjaya2007@gmail.com",
        "sb_balance": 215,
        "timestamp": "2026-10-09 12:13:10",
    },
    {
        "account_id": "nitishjaya2009",
        "nickname": "nitishjaya2009@gmail.com",
        "email": "nitishjaya2009@gmail.com",
        "sb_balance": 66,
        "timestamp": "2026-10-09 12:11:44",
    },
    {
        "account_id": "nitishpasuma",
        "nickname": "nitishpasuma@yahoo.com",
        "email": "nitishpasuma@yahoo.com",
        "sb_balance": 35,
        "timestamp": "2026-10-09 12:11:49",
    },
    {
        "account_id": "dndndbfnf571",
        "nickname": "dndndbfnf571@gmail.com",
        "email": "dndndbfnf571@gmail.com",
        "sb_balance": 15,
        "timestamp": "2026-10-09 12:13:05",
    },
    {
        "account_id": "unit8creativestudio",
        "nickname": "unit8creativestudio@yahoo.com",
        "email": "unit8creativestudio@yahoo.com",
        "sb_balance": 472,
        "timestamp": "2026-10-09 12:11:59",
    },
    {
        "account_id": "nitishjaya2011",
        "nickname": "nitishjaya2011@outlook.com",
        "email": "nitishjaya2011@outlook.com",
        "sb_balance": 372,
        "timestamp": "2026-10-09 12:11:49",
    },
    {
        "account_id": "nitishjaya2005",
        "nickname": "nitishjaya2005@gmail.com",
        "email": "nitishjaya2005@gmail.com",
        "sb_balance": 51,
        "timestamp": "2026-10-09 12:13:05",
    },
    {
        "account_id": "npasumar2",
        "nickname": "npasumar2@gitam.in",
        "email": "npasumar2@gitam.in",
        "sb_balance": 71,
        "timestamp": "2026-10-09 12:11:54",
    },
    {
        "account_id": "pasumanigish",
        "nickname": "pasumanigish@gmail.com",
        "email": "pasumanigish@gmail.com",
        "sb_balance": 517,
        "timestamp": "2026-10-09 12:11:54",
    },
    {
        "account_id": "nitishjaya2008",
        "nickname": "nitishjaya2008@gmail.com",
        "email": "nitishjaya2008@gmail.com",
        "sb_balance": 50,
        "timestamp": "2026-10-09 12:11:44",
    },
]


def seed_database_if_needed():
    """Ensure database has initial seed data on cold starts or Vercel deployments."""
    try:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        should_seed = False
        if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
            should_seed = True
        else:
            try:
                conn_chk = sqlite3.connect(str(DB_PATH), timeout=3)
                cur_chk = conn_chk.cursor()
                row = cur_chk.execute("SELECT COUNT(*) FROM accounts").fetchone()
                conn_chk.close()
                if not row or row[0] == 0:
                    should_seed = True
            except Exception:
                should_seed = True

        if should_seed and SEED_DB_PATH.exists():
            shutil.copyfile(str(SEED_DB_PATH), str(DB_PATH))
    except Exception as e:
        pass


def get_connection() -> sqlite3.Connection:
    """Ensure database directory exists, seeds data if needed, and returns an active SQLite connection."""
    seed_database_if_needed()
    try:
        DB_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 10000;")
        conn.execute("PRAGMA journal_mode = WAL;")
    except Exception:
        pass
    return conn


def init_db() -> None:
    """Initialize required tables and indexes if they do not exist."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Accounts table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                nickname TEXT,
                status TEXT DEFAULT 'ACTIVE',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Historical balance entries
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sb_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sb_balance INTEGER,
                prev_balance INTEGER,
                balance_change INTEGER,
                session_status TEXT NOT NULL,
                error_message TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(account_id)
            )
            """
        )

        # Threshold alerts history
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS threshold_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                email TEXT,
                threshold INTEGER DEFAULT 530,
                prev_balance INTEGER,
                current_balance INTEGER,
                channels_sent TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Index for fast lookup by account and time
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_account_time 
            ON sb_history (account_id, timestamp DESC)
            """
        )

        # Add migration columns to accounts safely if not present
        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN alert_530_triggered INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN last_alert_time TIMESTAMP")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN email TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN password TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN tracking_error_alerted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE sb_history ADD COLUMN ip_used TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN last_fetch_ip TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN day_start_sb INTEGER")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE accounts ADD COLUMN day_start_date TEXT")
        except sqlite3.OperationalError:
            pass

        # Ensure default seed accounts exist if database is empty
        cursor.execute("SELECT COUNT(*) FROM accounts")
        count_row = cursor.fetchone()
        if not count_row or count_row[0] == 0:
            for item in DEFAULT_SEED_ACCOUNTS:
                acc_id = item["account_id"]
                nick = item["nickname"]
                em = item["email"]
                ts = item.get("timestamp") or "2026-10-09 12:13:10"
                sb = item["sb_balance"]
                cursor.execute(
                    """
                    INSERT INTO accounts (account_id, nickname, status, email, password, last_updated, day_start_sb, day_start_date, last_fetch_ip)
                    VALUES (?, ?, 'ACTIVE', ?, NULL, ?, ?, ?, '104.28.192.45')
                    """,
                    (acc_id, nick, em, ts, sb, datetime.now().strftime("%Y-%m-%d")),
                )
                cursor.execute(
                    """
                    INSERT INTO sb_history (account_id, timestamp, sb_balance, prev_balance, balance_change, session_status, error_message, ip_used)
                    VALUES (?, ?, ?, ?, 0, 'Logged In', NULL, '104.28.192.45')
                    """,
                    (acc_id, ts, sb, sb),
                )

        conn.commit()


def register_account(
    account_id: str,
    nickname: Optional[str] = None,
    status: str = "ACTIVE",
    email: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """Register or update an account entry with persistent credentials for zero-prompt re-auth."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO accounts (account_id, nickname, status, email, password, last_updated)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                nickname = COALESCE(excluded.nickname, accounts.nickname),
                status = excluded.status,
                email = COALESCE(excluded.email, accounts.email),
                password = COALESCE(excluded.password, accounts.password),
                last_updated = CURRENT_TIMESTAMP
            """,
            (account_id, nickname or email or account_id, status, email, password),
        )
        conn.commit()


def get_account_credentials(account_id: str) -> Optional[Dict[str, Optional[str]]]:
    """Retrieve stored email and password for automatic background re-login."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT email, password, nickname FROM accounts WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        if not row:
            return None
        em = row["email"] or (row["nickname"] if row["nickname"] and "@" in row["nickname"] else None)
        return {
            "email": em or (account_id if "@" in account_id else None),
            "password": row["password"],
        }


def set_account_status(account_id: str, status: str) -> None:
    """Update status for an account (e.g. ACTIVE, SESSION_EXPIRED, ERROR)."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE accounts 
            SET status = ?, last_updated = CURRENT_TIMESTAMP
            WHERE account_id = ?
            """,
            (status, account_id),
        )
        conn.commit()


def get_all_accounts() -> List[Dict[str, Any]]:
    """Return all configured accounts from database."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT * FROM accounts ORDER BY account_id ASC")
        return [dict(row) for row in cursor.fetchall()]


def get_latest_record(account_id: str) -> Optional[Dict[str, Any]]:
    """Get the most recent balance record for a given account."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM sb_history 
            WHERE account_id = ? 
            ORDER BY timestamp DESC, id DESC 
            LIMIT 1
            """,
            (account_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def record_balance(
    account_id: str,
    sb_balance: Optional[int],
    prev_balance: Optional[int],
    balance_change: int,
    session_status: str,
    error_message: Optional[str] = None,
    ip_used: Optional[str] = None,
) -> int:
    """
    Record a new monitoring check result into SQLite with full timestamp and IP address.
    Also tracks the exact starting balance of the day for date comparison.
    Returns the newly inserted row ID.
    """
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_date = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sb_history (
                account_id, 
                timestamp,
                sb_balance, 
                prev_balance, 
                balance_change, 
                session_status, 
                error_message,
                ip_used
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                now_ts,
                sb_balance,
                prev_balance,
                balance_change,
                session_status,
                error_message,
                ip_used,
            ),
        )
        try:
            conn.execute(
                """
                UPDATE accounts 
                SET last_updated = ?,
                    last_fetch_ip = COALESCE(?, last_fetch_ip),
                    day_start_sb = CASE 
                        WHEN day_start_date IS NULL OR day_start_date != ? THEN COALESCE(?, day_start_sb)
                        ELSE day_start_sb 
                    END,
                    day_start_date = CASE 
                        WHEN day_start_date IS NULL OR day_start_date != ? THEN ?
                        ELSE day_start_date 
                    END
                WHERE account_id = ?
                """,
                (now_ts, ip_used, today_date, sb_balance, today_date, today_date, account_id)
            )
        except Exception:
            pass
        conn.commit()
        return cursor.lastrowid


def update_account_balance(account_id: str, new_balance: int, note: Optional[str] = None) -> Dict[str, Any]:
    """
    Update or set an account's SB balance directly.
    Calculates change from previous record, records into sb_history with 'Logged In',
    updates account last_updated, and returns updated details.
    """
    last_record = get_latest_record(account_id)
    prev_balance = last_record["sb_balance"] if (last_record and last_record["sb_balance"] is not None) else 0
    change = new_balance - prev_balance

    record_balance(
        account_id=account_id,
        sb_balance=new_balance,
        prev_balance=prev_balance,
        balance_change=change,
        session_status="Logged In",
        error_message=note,
    )
    set_account_status(account_id, "ACTIVE")
    return {
        "account_id": account_id,
        "prev_balance": prev_balance,
        "current_balance": new_balance,
        "change": change,
        "status": "Logged In"
    }


def batch_sync_accounts(account_list: List[Dict[str, Any]]) -> int:
    """
    Bulk sync accounts and their latest balances into the database.
    Used by cloud sync or import.
    """
    count = 0
    for item in account_list:
        acc_id = item.get("account_id")
        if not acc_id:
            continue
        email = item.get("email") or acc_id
        nickname = item.get("nickname") or email
        sb = item.get("current_sb") if "current_sb" in item else item.get("sb_balance", 0)
        register_account(acc_id, nickname=nickname, status="ACTIVE", email=email)
        update_account_balance(acc_id, int(sb))
        count += 1
    return count


def get_account_stats(account_id: str) -> Dict[str, Any]:
    """
    Calculate and return comprehensive statistics for an account:
    - current_sb
    - previous_sb
    - latest_change
    - today_change
    - highest_balance
    - lowest_balance
    - last_successful_check
    - successful_checks
    - failed_checks
    """
    with get_connection() as conn:
        # Latest record
        cursor = conn.execute(
            """
            SELECT sb_balance, prev_balance, balance_change, session_status, timestamp 
            FROM sb_history 
            WHERE account_id = ? 
            ORDER BY timestamp DESC, id DESC 
            LIMIT 1
            """,
            (account_id,),
        )
        latest = cursor.fetchone()

        # Min / Max balances across valid records
        cursor = conn.execute(
            """
            SELECT 
                MAX(sb_balance) AS highest_balance,
                MIN(sb_balance) AS lowest_balance
            FROM sb_history
            WHERE account_id = ? AND sb_balance IS NOT NULL
            """,
            (account_id,),
        )
        min_max = cursor.fetchone()

        # Today's change (sum of balance_change recorded today)
        cursor = conn.execute(
            """
            SELECT COALESCE(SUM(balance_change), 0) AS today_change
            FROM sb_history
            WHERE account_id = ? 
              AND DATE(timestamp, 'localtime') = DATE('now', 'localtime')
            """,
            (account_id,),
        )
        today_row = cursor.fetchone()

        # Counts: successful checks vs failed checks
        cursor = conn.execute(
            """
            SELECT 
                COUNT(CASE WHEN session_status = 'Logged In' THEN 1 END) AS successful_checks,
                COUNT(CASE WHEN session_status != 'Logged In' THEN 1 END) AS failed_checks
            FROM sb_history
            WHERE account_id = ?
            """,
            (account_id,),
        )
        counts = cursor.fetchone()

        # Last successful check timestamp
        cursor = conn.execute(
            """
            SELECT timestamp 
            FROM sb_history 
            WHERE account_id = ? AND session_status = 'Logged In'
            ORDER BY timestamp DESC, id DESC 
            LIMIT 1
            """,
            (account_id,),
        )
        last_success = cursor.fetchone()

        # Fetch account email and nickname
        acc_info = conn.execute("SELECT email, nickname FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        email_val = (
            acc_info["email"]
            if acc_info and acc_info["email"]
            else (acc_info["nickname"] if acc_info and acc_info["nickname"] and "@" in acc_info["nickname"] else account_id)
        )

        # Calculate exact day starting balance (recorded at start of day / ~12:00 midnight)
        start_of_day_row = conn.execute(
            """
            SELECT sb_balance FROM sb_history
            WHERE account_id = ? AND DATE(timestamp, 'localtime') = DATE('now', 'localtime')
            ORDER BY timestamp ASC, id ASC
            LIMIT 1
            """,
            (account_id,)
        ).fetchone()
        if not start_of_day_row:
            start_of_day_row = conn.execute(
                """
                SELECT sb_balance FROM sb_history
                WHERE account_id = ? AND DATE(timestamp, 'localtime') < DATE('now', 'localtime')
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (account_id,)
            ).fetchone()

        curr_sb = latest["sb_balance"] if latest and latest["sb_balance"] is not None else 0
        day_start_val = (
            start_of_day_row["sb_balance"]
            if start_of_day_row and start_of_day_row["sb_balance"] is not None
            else (acc_info["day_start_sb"] if (acc_info and "day_start_sb" in acc_info.keys() and acc_info["day_start_sb"] is not None) else curr_sb)
        )

        ip_val = None
        if latest and "ip_used" in latest.keys() and latest["ip_used"]:
            ip_val = latest["ip_used"]
        elif acc_info and "last_fetch_ip" in acc_info.keys() and acc_info["last_fetch_ip"]:
            ip_val = acc_info["last_fetch_ip"]

        return {
            "account_id": account_id,
            "email": email_val,
            "nickname": acc_info["nickname"] if acc_info else account_id,
            "current_sb": curr_sb,
            "previous_sb": latest["prev_balance"] if latest and latest["prev_balance"] is not None else 0,
            "latest_change": latest["balance_change"] if latest and latest["balance_change"] is not None else 0,
            "day_start_sb": day_start_val,
            "ip_used": ip_val,
            "status": latest["session_status"] if latest else "Not Checked",
            "last_check_time": latest["timestamp"] if latest else None,
            "highest_balance": min_max["highest_balance"] if min_max and min_max["highest_balance"] is not None else 0,
            "lowest_balance": min_max["lowest_balance"] if min_max and min_max["lowest_balance"] is not None else 0,
            "today_change": today_row["today_change"] if today_row else 0,
            "successful_checks": counts["successful_checks"] if counts else 0,
            "failed_checks": counts["failed_checks"] if counts else 0,
            "last_successful_check": last_success["timestamp"] if last_success else "Never",
        }


def get_aggregate_summary() -> Dict[str, Any]:
    """
    Calculate totals across all active accounts:
    - total_current_sb
    - total_latest_change
    - total_today_change
    """
    accounts = get_all_accounts()
    total_current = 0
    total_latest_change = 0
    total_today = 0

    account_stats_list = []
    for acc in accounts:
        stats = get_account_stats(acc["account_id"])
        total_current += stats["current_sb"]
        total_latest_change += stats["latest_change"]
        total_today += stats["today_change"]
        account_stats_list.append(stats)

    return {
        "accounts": account_stats_list,
        "total_current_sb": total_current,
        "total_latest_change": total_latest_change,
        "total_today_change": total_today,
        "total_accounts": len(accounts),
    }


def get_balance_history(account_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent historical balance records for charts."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT timestamp, sb_balance, balance_change, session_status 
            FROM sb_history 
            WHERE account_id = ? AND sb_balance IS NOT NULL
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (account_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_account_records(account_id: str) -> None:
    """Delete an account and its historical records from the database."""
    with get_connection() as conn:
        conn.execute("DELETE FROM sb_history WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM threshold_alerts WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
        conn.commit()


def is_threshold_alert_triggered(account_id: str) -> bool:
    """Check if threshold alert has already been fired for the current crossing."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT alert_530_triggered FROM accounts WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        return bool(row and row["alert_530_triggered"])


def set_threshold_alert_state(account_id: str, triggered: bool) -> None:
    """Update threshold alert trigger state to prevent duplicate alerts."""
    val = 1 if triggered else 0
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE accounts 
            SET alert_530_triggered = ?,
                last_alert_time = CASE WHEN ? = 1 THEN CURRENT_TIMESTAMP ELSE last_alert_time END
            WHERE account_id = ?
            """,
            (val, val, account_id),
        )
        conn.commit()


def can_send_daily_threshold_alert(account_id: str) -> bool:
    """
    Check if a threshold alert can be sent for today.
    Enforces the rule: MAXIMUM ONE ALERT PER CALENDAR DAY.
    Returns True if no alert was sent today (or never sent).
    Returns False if an alert was already sent on the current date.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "SELECT last_alert_time FROM accounts WHERE account_id = ?",
            (account_id,)
        )
        row = cursor.fetchone()
        if not row or not row["last_alert_time"]:
            return True

        last_time_str = str(row["last_alert_time"])
        # Format can be '2026-10-08 18:48:05' or ISO '2026-10-08T18:48:05'
        last_date = last_time_str.split()[0].split("T")[0]
        today_date = datetime.now().strftime("%Y-%m-%d")

        # Allow alert only if the last alert was on a previous date
        return last_date != today_date


def record_daily_threshold_alert(account_id: str) -> None:
    """Record that a threshold alert was successfully sent for today."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE accounts 
            SET last_alert_time = CURRENT_TIMESTAMP,
                alert_530_triggered = 1
            WHERE account_id = ?
            """,
            (account_id,)
        )
        conn.commit()


def is_tracking_error_alerted(account_id: str) -> bool:
    """Check if tracking failure alert has already been fired for the current error state."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT tracking_error_alerted FROM accounts WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        try:
            return bool(row and row["tracking_error_alerted"])
        except Exception:
            return False


def set_tracking_error_alert_state(account_id: str, alerted: bool) -> None:
    """Update tracking failure alert state to prevent duplicate alert spam."""
    val = 1 if alerted else 0
    with get_connection() as conn:
        try:
            conn.execute(
                "UPDATE accounts SET tracking_error_alerted = ? WHERE account_id = ?",
                (val, account_id),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass


def record_threshold_alert(
    account_id: str,
    email: str,
    prev_balance: int,
    current_balance: int,
    channels_sent: str,
    threshold: int = 530,
) -> None:
    """Record an audit log of sent threshold crossing alert."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO threshold_alerts (account_id, email, threshold, prev_balance, current_balance, channels_sent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account_id, email, threshold, prev_balance, current_balance, channels_sent),
        )
        conn.commit()


def get_account_email(account_id: str) -> str:
    """Retrieve the exact email associated with an account ID."""
    with get_connection() as conn:
        cursor = conn.execute("SELECT email, nickname FROM accounts WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        if row:
            if row["email"] and "@" in row["email"]:
                return row["email"]
            if row["nickname"] and "@" in row["nickname"]:
                return row["nickname"]
        if "@" in account_id:
            return account_id
        from config import Config
        return Config.SWAGBUCKS_EMAIL or account_id


def get_accounts_above_threshold(threshold: int = 530) -> List[Dict[str, Any]]:
    """Return all accounts whose latest recorded balance is greater than or equal to the threshold."""
    accounts = get_all_accounts()
    above_list = []
    for acc in accounts:
        stats = get_account_stats(acc["account_id"])
        current_sb = stats["current_sb"]
        if current_sb is not None and current_sb >= threshold:
            email = acc.get("nickname") or acc["account_id"]
            above_list.append({
                "account_id": acc["account_id"],
                "email": email,
                "current_sb": current_sb,
                "previous_sb": stats["previous_sb"],
                "status": f"{threshold}+ SB Reached",
                "session_status": stats["status"],
                "last_check_time": stats["last_check_time"] or stats["last_successful_check"],
                "latest_change": stats["latest_change"],
            })
    return sorted(above_list, key=lambda x: x["current_sb"], reverse=True)


def get_last_successful_check_overall() -> Optional[str]:
    """Return timestamp of the most recent successful check across any account."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT timestamp 
            FROM sb_history 
            WHERE session_status = 'Logged In' 
            ORDER BY timestamp DESC, id DESC 
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        return row["timestamp"] if row else None


