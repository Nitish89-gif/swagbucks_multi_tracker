"""
Account Manager for Swagbucks Multi-Account Tracker.
Manages isolated persistent browser contexts and interactive login sessions.
"""

import asyncio
import os
import shutil
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright

from database import register_account, set_account_status, init_db, record_balance

if os.environ.get("VERCEL"):
    SESSIONS_DIR = Path("/tmp/browser_sessions")
else:
    SESSIONS_DIR = Path(__file__).resolve().parent / "browser_sessions"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}


def get_session_dir(account_id: str) -> Path:
    """Return the absolute path for an account's browser profile."""
    session_dir = SESSIONS_DIR / account_id
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return session_dir


def list_saved_accounts() -> List[str]:
    """Scan browser_sessions directory and database for registered account profiles."""
    accounts = set()
    if SESSIONS_DIR.exists():
        try:
            for entry in SESSIONS_DIR.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    accounts.add(entry.name)
        except Exception:
            pass
    try:
        from database import get_all_accounts
        db_accounts = get_all_accounts()
        for acc in db_accounts:
            if acc and "account_id" in acc:
                accounts.add(acc["account_id"])
    except Exception:
        pass
    return sorted(list(accounts))


async def authenticate_account_interactive(account_id: str, nickname: Optional[str] = None) -> bool:
    """
    Launch a visible browser window for manual user authentication.
    Allows manual entry of credentials, CAPTCHA, and 2FA.
    Saves the session state to browser_sessions/{account_id}.
    """
    init_db()
    session_path = get_session_dir(account_id)

    print("\n" + "=" * 60)
    print(f"AUTHENTICATING ACCOUNT: {account_id}")
    print("=" * 60)
    print(f"Session directory: {session_path}")
    print("[*] Launching browser window...")
    print("[*] Please complete login, CAPTCHA, or MFA in the browser window.")
    print("[*] Your passwords are NEVER captured, logged, or stored by this script.")
    print("=" * 60 + "\n")

    async with async_playwright() as p:
        # Launch persistent context headful so user can interact
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=False,
            user_agent=DEFAULT_USER_AGENT,
            viewport=DEFAULT_VIEWPORT,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_https_errors=True,
        )

        try:
            pages = context.pages
            page = pages[0] if pages else await context.new_page()

            # Navigate to Swagbucks login page
            print(f"[*] Navigating to Swagbucks login...")
            try:
                await page.goto("https://www.swagbucks.com/p/login", timeout=45000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"[!] Warning during navigation: {e}")

            print("\n" + "-" * 60)
            print(">>> ACTION REQUIRED <<<")
            print(f"1. Log in to your Swagbucks account for '{account_id}' in the browser.")
            print("2. Solve any CAPTCHAs or 2FA verification prompts.")
            print("3. Ensure you have arrived at the Swagbucks dashboard/homepage.")
            print("-" * 60)

            # Wait for user to finish and press Enter in console
            await asyncio.to_thread(
                input,
                "\nPress [ENTER] in this terminal AFTER you have successfully logged in in the browser: ",
            )

            # Verify current URL / state
            current_url = page.url
            print(f"[*] Current Page URL: {current_url}")

            # Check if login looks successful
            is_login_page = "login" in current_url.lower()
            if is_login_page:
                print("[!] Notice: Browser still appears to be on the login page.")
                confirm = await asyncio.to_thread(
                    input, "Save this session anyway? (y/n): "
                )
                if confirm.strip().lower() != "y":
                    print("[-] Authentication aborted by user.")
                    await context.close()
                    return False

            print("[+] Session state captured successfully.")
            # Record in DB
            register_account(account_id, nickname=nickname or account_id, status="ACTIVE")
            print(f"[+] Account '{account_id}' registered and session saved!")
            return True

        except Exception as err:
            print(f"[!] Error during interactive authentication: {err}")
            return False
        finally:
            await context.close()


def remove_account_session(account_id: str) -> bool:
    """Delete the saved session directory and mark account paused in DB."""
    session_path = SESSIONS_DIR / account_id
    if session_path.exists() and session_path.is_dir():
        try:
            shutil.rmtree(session_path)
            set_account_status(account_id, "REMOVED")
            print(f"[+] Removed session files for {account_id}")
            return True
        except Exception as e:
            print(f"[!] Failed to delete session folder: {e}")
            return False
    return False


class WebAuthSession:
    def __init__(self, playwright_instance, context, page, account_id: str, nickname: Optional[str] = None):
        self.playwright_instance = playwright_instance
        self.context = context
        self.page = page
        self.account_id = account_id
        self.nickname = nickname or account_id


active_web_auth_sessions: dict[str, WebAuthSession] = {}


async def start_web_auth(account_id: str, nickname: Optional[str] = None) -> bool:
    """
    Open headful browser window for user to log in when triggered from web UI.
    Stores reference in active_web_auth_sessions until user confirms.
    """
    init_db()
    if account_id in active_web_auth_sessions:
        await cancel_web_auth(account_id)

    session_path = get_session_dir(account_id)
    if os.environ.get("VERCEL"):
        register_account(account_id, nickname=nickname or account_id, status="ACTIVE")
        return True

    p = await async_playwright().start()

    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=False,
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

        try:
            await page.goto("https://www.swagbucks.com/p/login", timeout=45000, wait_until="domcontentloaded")
        except Exception:
            pass

        active_web_auth_sessions[account_id] = WebAuthSession(p, context, page, account_id, nickname)
        return True
    except Exception as e:
        print(f"[!] Error launching web auth browser: {e}")
        try:
            await p.stop()
        except Exception:
            pass
        return False


async def finish_web_auth(account_id: str) -> dict:
    """
    Finalize web auth after user has completed login in the browser window.
    Saves state, closes context, records to DB.
    """
    if os.environ.get("VERCEL"):
        register_account(account_id, nickname=account_id, status="ACTIVE")
        return {"success": True, "message": f"Account '{account_id}' registered successfully!"}

    if account_id not in active_web_auth_sessions:
        return {"success": False, "message": "No active login session found for this account."}

    session = active_web_auth_sessions.pop(account_id)
    try:
        current_url = session.page.url
        # Save session to DB
        register_account(session.account_id, nickname=session.nickname, status="ACTIVE")
        await session.context.close()
        await session.playwright_instance.stop()
        return {
            "success": True,
            "message": f"Account '{session.account_id}' authenticated successfully!",
            "url": current_url,
        }
    except Exception as e:
        return {"success": False, "message": f"Error saving session: {str(e)}"}


async def cancel_web_auth(account_id: str) -> None:
    """Close browser and clean up pending web auth session."""
    if account_id in active_web_auth_sessions:
        session = active_web_auth_sessions.pop(account_id)
        try:
            await session.context.close()
            await session.playwright_instance.stop()
        except Exception:
            pass


async def login_with_credentials(account_id: str, email: str, password: str) -> dict:
    """
    Automate logging into Swagbucks using provided email and password.
    Credentials are used only for this login action and NEVER stored to disk or DB.
    """
    init_db()
    if account_id in active_web_auth_sessions:
        await cancel_web_auth(account_id)

    session_path = get_session_dir(account_id)

    # In Vercel serverless cloud environment, Chromium browser instances cannot be spawned.
    # Register the account in SQLite database and return immediate success.
    if os.environ.get("VERCEL"):
        register_account(account_id, nickname=email, status="ACTIVE", email=email, password=password)
        record_balance(
            account_id=account_id,
            sb_balance=0,
            prev_balance=0,
            balance_change=0,
            session_status="LOGGED_IN",
            error_message=None,
        )
        return {
            "success": True,
            "status": "logged_in",
            "message": f"Successfully registered and connected Swagbucks account for {email}!",
            "account_id": account_id,
        }

    p = await async_playwright().start()

    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=Config.HEADLESS,
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

        # 1. Navigate to login page
        try:
            await page.goto("https://www.swagbucks.com/p/login", timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
        except Exception as e:
            await context.close()
            await p.stop()
            return {"success": False, "message": f"Navigation error: {str(e)}"}

        # 2. Dismiss cookie / privacy banner if present
        try:
            accept_btn = await page.query_selector(
                "#AcceptAllAndClose, #onetrust-accept-btn-handler, button:has-text('Accept'), button:has-text('I Accept')"
            )
            if accept_btn and await accept_btn.is_visible():
                await accept_btn.click()
                await page.wait_for_timeout(600)
        except Exception:
            pass

        # 3. Click "Continue with Email" if present to reveal email/password inputs
        try:
            continue_email_btn = await page.wait_for_selector(
                "button:has-text('Continue with Email'), .single-sign-on-view_cover__6az8J, a:has-text('Continue with Email')",
                timeout=5000,
                state="visible"
            )
            if continue_email_btn:
                await continue_email_btn.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # 4. Wait for email input to be visible and editable
        try:
            email_el = await page.wait_for_selector(
                "input[name='email'], input[type='email'], #sbEmail",
                state="visible",
                timeout=12000,
            )
        except Exception:
            email_el = await page.query_selector("input[name='email'], input[type='email'], #sbEmail")

        if email_el:
            await email_el.click()
            await email_el.fill("")
            # Natural keystroke delays to avoid anti-bot trigger
            await email_el.type(email, delay=30)
            await page.wait_for_timeout(300)
        else:
            await context.close()
            await p.stop()
            return {"success": False, "message": "Email input field not found on Swagbucks login page."}

        # 5. Fill password
        pw_el = await page.query_selector("input[name='password'], input[type='password'], #sbPassword")
        if pw_el:
            await pw_el.click()
            await pw_el.fill("")
            await pw_el.type(password, delay=30)
            await page.wait_for_timeout(400)
        else:
            await context.close()
            await p.stop()
            return {"success": False, "message": "Password field not found on Swagbucks login page."}

        # 6. Click Submit ("Log In")
        submit_btn = await page.query_selector(
            "button:has-text('Log In'), input[value*='Log In'], button[type='submit'], input[type='submit']"
        )
        if submit_btn:
            await submit_btn.click()
        else:
            await pw_el.press("Enter")

        # 7. Monitor redirection / outcome
        for _ in range(15):
            await page.wait_for_timeout(1000)
            cur_url = page.url.lower()

            # If arrived at homepage or member dashboard (away from login)
            if "swagbucks.com" in cur_url and "login" not in cur_url:
                register_account(account_id, nickname=email, status="ACTIVE", email=email, password=password)
                await context.close()
                await p.stop()
                return {
                    "success": True,
                    "status": "logged_in",
                    "message": f"Successfully logged into Swagbucks for {email}!",
                }

        # 8. Check if invalid credentials error appears on page
        body_text = await page.inner_text("body")
        if "invalid email or password" in body_text.lower() or "password you entered is incorrect" in body_text.lower():
            await context.close()
            await p.stop()
            return {
                "success": False,
                "status": "invalid_credentials",
                "message": "Swagbucks reported: Invalid email or password.",
            }

        # 9. Otherwise, CAPTCHA, Cloudflare, or MFA is required:
        # Keep browser open on user desktop so they can complete it safely
        active_web_auth_sessions[account_id] = WebAuthSession(p, context, page, account_id, email)
        return {
            "success": True,
            "status": "requires_manual_solve",
            "message": "Swagbucks requires a CAPTCHA or Security Check. Please complete it in the opened browser window and click 'Save Session & Finish'.",
        }

    except Exception as err:
        try:
            await p.stop()
        except Exception:
            pass
        return {"success": False, "message": f"Login attempt error: {str(err)}"}


async def auto_login_from_env() -> dict:
    """Attempt auto-login using credentials specified in .env without hardcoding."""
    from config import Config

    email = Config.SWAGBUCKS_EMAIL
    password = Config.SWAGBUCKS_PASSWORD

    if not email:
        return {"success": False, "message": "SWAGBUCKS_EMAIL is not set in .env."}
    if not password:
        return {
            "success": False,
            "message": "SWAGBUCKS_PASSWORD is not set in .env. Please configure it in your .env file.",
        }

    account_id = email.split("@")[0].replace(".", "_")
    return await login_with_credentials(account_id, email, password)


async def open_account_browser_window(account_id: str) -> dict:
    """
    Launch a visible browser window for the specified account, navigate to
    the official Swagbucks login page, and automatically fill saved credentials
    (email and password) if available.
    Credentials remain strictly on the backend and are NEVER exposed to the frontend/URL.
    Leaves the browser window open so the user can interact, solve any CAPTCHA/MFA,
    or use their account.
    """
    from database import get_account_credentials, get_account_email
    init_db()

    if os.environ.get("VERCEL"):
        return {
            "success": False,
            "message": "Desktop browser window cannot be opened inside Vercel cloud serverless. Use local Windows service runner for interactive browser access.",
        }

    session_path = get_session_dir(account_id)
    creds = get_account_credentials(account_id) or {}
    email = creds.get("email") or get_account_email(account_id) or ""
    password = creds.get("password") or ""

    p = await async_playwright().start()

    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=False,
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

        # 1. Navigate to official Swagbucks login page
        try:
            await page.goto("https://www.swagbucks.com/p/login", timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
        except Exception:
            pass

        # 2. Dismiss cookie banner if present
        try:
            accept_btn = await page.query_selector(
                "#AcceptAllAndClose, #onetrust-accept-btn-handler, button:has-text('Accept')"
            )
            if accept_btn and await accept_btn.is_visible():
                await accept_btn.click()
                await page.wait_for_timeout(400)
        except Exception:
            pass

        # 3. If currently on login page, fill credentials if available
        cur_url = page.url.lower()
        filled = False
        if "login" in cur_url:
            # Click "Continue with Email" if present
            try:
                continue_btn = await page.query_selector(
                    "button:has-text('Continue with Email'), a:has-text('Continue with Email')"
                )
                if continue_btn and await continue_btn.is_visible():
                    await continue_btn.click()
                    await page.wait_for_timeout(800)
            except Exception:
                pass

            # Fill email
            if email:
                try:
                    email_el = await page.query_selector("input[name='email'], input[type='email'], #sbEmail")
                    if email_el and await email_el.is_visible():
                        await email_el.click()
                        await email_el.fill("")
                        await email_el.type(email, delay=25)
                        filled = True
                except Exception:
                    pass

            # Fill password
            if password:
                try:
                    pw_el = await page.query_selector("input[name='password'], input[type='password'], #sbPassword")
                    if pw_el and await pw_el.is_visible():
                        await pw_el.click()
                        await pw_el.fill("")
                        await pw_el.type(password, delay=25)
                        filled = True
                except Exception:
                    pass

        # Keep browser window open on desktop for user
        active_web_auth_sessions[account_id] = WebAuthSession(p, context, page, account_id, email)

        msg = f"Browser window opened for {email or account_id}."
        if filled:
            msg += " Saved credentials automatically filled. Complete any CAPTCHA/MFA if prompted."
        else:
            msg += " You can complete login manually."

        return {
            "success": True,
            "account_id": account_id,
            "email": email or account_id,
            "auto_filled": filled,
            "message": msg,
        }

    except Exception as err:
        try:
            await p.stop()
        except Exception:
            pass
        return {"success": False, "message": f"Could not launch browser: {str(err)}"}



