"""
Swagbucks Multi Tracker - 24/7 Dedicated Background Service Runner
Runs web_app.py and cloudflared tunnel completely detached from any terminal or IDE.
Features:
- Silent windowless background execution via pythonw.exe
- Automatic restart / watchdog if either process crashes
- Extracts public HTTPS URL and creates Desktop shortcut
- Dispatches instant mobile push alert via ntfy with clickable link
- Graceful shutdown support via stop_service.bat
"""

import sys
import os
import time
import subprocess
import re
import urllib.request
import json
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

LOG_FILE = DATA_DIR / "service_runner.log"
PID_FILE = DATA_DIR / "service_runner.pid"
URL_FILE = BASE_DIR / "current_public_url.txt"
def get_desktop_dir():
    userprofile = Path(os.environ.get("USERPROFILE", "C:\\Users\\pasum"))
    onedrive_desktop = userprofile / "OneDrive" / "Desktop"
    if onedrive_desktop.exists():
        return onedrive_desktop
    normal_desktop = userprofile / "Desktop"
    if normal_desktop.exists():
        return normal_desktop
    return userprofile

DESKTOP_DIR = get_desktop_dir()
SHORTCUT_FILE = DESKTOP_DIR / "Swagbucks_Tracker.url"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

PYTHON_EXE = str(BASE_DIR / ".venv" / "Scripts" / "python.exe")
if not os.path.exists(PYTHON_EXE):
    PYTHON_EXE = sys.executable
CLOUDFLARED_EXE = str(BASE_DIR / "cloudflared.exe")
WEB_APP_SCRIPT = str(BASE_DIR / "web_app.py")
NTFY_TOPIC = "sb_alerts_9676061877"

# Windows flags to suppress console windows
CREATE_NO_WINDOW = 0x08000000

# Windows sleep prevention flags (prevents PC from sleeping while tracker runs)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


def set_sleep_prevention(enable: bool = True):
    """
    Informs Windows that the system is performing vital background operations,
    preventing idle sleep and standby even when the laptop lid is closed.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            if enable:
                res = ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
                )
                logging.info("Windows sleep prevention activated (status: 0x%08X).", res if res is not None else 0)
            else:
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                logging.info("Windows sleep prevention released.")
        except Exception as e:
            logging.warning("Could not set Windows execution state: %s", e)


def is_already_running():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                creationflags=CREATE_NO_WINDOW
            )
            out = res.stdout.lower()
            if str(pid) in out and ("python" in out):
                return True
        except Exception:
            pass
    return False


def notify_phone(url):
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=f"Swagbucks Multi Tracker is active 24/7!\nOpen Live Dashboard: {url}".encode("utf-8"),
            headers={
                "Title": "Swagbucks Tracker Live 24/7",
                "Click": url,
                "Tags": "rocket,white_check_mark",
                "Priority": "high"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logging.info("Dispatched mobile push notification to %s", NTFY_TOPIC)
    except Exception as e:
        logging.warning("Failed to dispatch mobile notification: %s", e)


def create_desktop_shortcut(url):
    try:
        content = f"[InternetShortcut]\nURL={url}\nIconIndex=0\n"
        SHORTCUT_FILE.write_text(content, encoding="utf-8")
        logging.info("Saved desktop shortcut to %s", SHORTCUT_FILE)
    except Exception as e:
        logging.warning("Failed to create desktop shortcut: %s", e)


def save_url_file(url):
    try:
        URL_FILE.write_text(url, encoding="utf-8")
        logging.info("Saved public URL to %s: %s", URL_FILE, url)
    except Exception as e:
        logging.warning("Failed to write URL file: %s", e)


def is_backend_healthy():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False


def start_backend():
    logging.info("Starting web_app.py...")
    proc = subprocess.Popen(
        [PYTHON_EXE, WEB_APP_SCRIPT],
        cwd=str(BASE_DIR),
        creationflags=CREATE_NO_WINDOW
    )
    # Wait for backend to be ready
    for _ in range(30):
        time.sleep(1)
        if is_backend_healthy():
            logging.info("Backend is healthy on port 8080 (PID %d)", proc.pid)
            return proc
    logging.warning("Backend started but /health not responding yet (PID %d)", proc.pid)
    return proc


def start_tunnel():
    logging.info("Starting cloudflared tunnel...")
    tunnel_log = DATA_DIR / "cloudflared_service.log"
    log_fp = open(tunnel_log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [CLOUDFLARED_EXE, "tunnel", "--url", "http://localhost:8080"],
        cwd=str(BASE_DIR),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW
    )
    
    # Wait and parse public URL from tunnel log
    public_url = None
    for _ in range(30):
        time.sleep(1)
        if tunnel_log.exists():
            try:
                content = tunnel_log.read_text(encoding="utf-8", errors="ignore")
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                if match:
                    public_url = match.group(0)
                    break
            except Exception:
                pass
    
    if public_url:
        logging.info("Public tunnel established: %s (PID %d)", public_url, proc.pid)
        save_url_file(public_url)
        create_desktop_shortcut(public_url)
        notify_phone(public_url)
    else:
        logging.warning("Tunnel launched (PID %d) but URL not detected yet", proc.pid)

    return proc, public_url


def is_tunnel_healthy(url):
    tunnel_log = DATA_DIR / "cloudflared_service.log"
    if tunnel_log.exists():
        try:
            with open(tunnel_log, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-15:]
                for line in lines:
                    if "Unauthorized: Tunnel not found" in line:
                        return False
        except Exception:
            pass
    return True


def main():
    if is_already_running():
        logging.warning("Service runner already running. Exiting duplicate instance.")
        sys.exit(0)

    PID_FILE.write_text(str(os.getpid()))
    logging.info("=== Swagbucks Multi Tracker 24/7 Service Started (PID %d) ===", os.getpid())

    backend_proc = None
    tunnel_proc = None
    last_known_url = None

    try:
        set_sleep_prevention(True)
        backend_proc = start_backend()
        tunnel_proc, last_known_url = start_tunnel()

        # Watchdog loop
        while True:
            time.sleep(15)

            # Check backend process
            if backend_proc.poll() is not None or not is_backend_healthy():
                logging.warning("Backend process stopped or unhealthy. Restarting...")
                try:
                    if backend_proc.poll() is None:
                        backend_proc.terminate()
                except Exception:
                    pass
                backend_proc = start_backend()

            # Check tunnel process
            if tunnel_proc.poll() is not None or not is_tunnel_healthy(last_known_url):
                logging.warning("Tunnel dropped or unresponsive. Forcefully restarting tunnel...")
                try:
                    if tunnel_proc.poll() is None:
                        tunnel_proc.terminate()
                        time.sleep(1)
                except Exception:
                    pass
                tunnel_proc, last_known_url = start_tunnel()

    except (KeyboardInterrupt, SystemExit):
        logging.info("Service runner received exit signal.")
    except Exception as e:
        logging.exception("Unexpected error in service runner: %s", e)
    finally:
        set_sleep_prevention(False)
        logging.info("Shutting down child processes...")
        try:
            if backend_proc and backend_proc.poll() is None:
                backend_proc.terminate()
        except Exception:
            pass
        try:
            if tunnel_proc and tunnel_proc.poll() is None:
                tunnel_proc.terminate()
        except Exception:
            pass
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass
        logging.info("=== Service runner exited cleanly ===")


if __name__ == "__main__":
    main()
