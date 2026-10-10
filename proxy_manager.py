"""
Proxy and IP address management module for Swagbucks Multi-Account Tracker.
Provides rotating IP addresses for multi-account balance fetching to avoid
detection, Cloudflare rate-limits, and IP-based account linkage.
"""

import os
import re
import json
import random
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("proxy_manager")

BASE_DIR = Path(__file__).resolve().parent
if os.environ.get("VERCEL"):
    DATA_DIR = Path("/tmp/data")
else:
    DATA_DIR = BASE_DIR / "data"

try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA_DIR = Path("/tmp/data")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

PROXIES_FILE = DATA_DIR / "proxies.txt"
CACHE_PROXIES_FILE = DATA_DIR / "working_proxies.json"
SEED_PROXIES_FILE = BASE_DIR / "data" / "proxies.txt"

# Public proxy scrapers for automatic free proxy pool rotation
PUBLIC_PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=4000&country=all&ssl=yes",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]


class ProxyManager:
    _instance = None

    def __init__(self):
        self.proxies: List[str] = []
        self.account_proxy_map: Dict[str, str] = {}
        self.last_fetch_time: Optional[float] = None
        self.load_proxies()

    @classmethod
    def get_instance(cls) -> "ProxyManager":
        if cls._instance is None:
            cls._instance = ProxyManager()
        return cls._instance

    def load_proxies(self) -> List[str]:
        """Load proxies from data/proxies.txt, .env PROXY_LIST, or cached working proxies."""
        loaded: List[str] = []

        # 1. Check data/proxies.txt and fallback seed file
        for candidate_file in [PROXIES_FILE, SEED_PROXIES_FILE]:
            if candidate_file.exists():
                try:
                    lines = candidate_file.read_text(encoding="utf-8").splitlines()
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            loaded.append(line)
                    if loaded:
                        break
                except Exception as e:
                    logger.warning(f"Error reading {candidate_file}: {e}")

        # 2. Check .env / os.getenv PROXY_LIST
        env_proxies = os.getenv("PROXY_LIST", "").strip()
        if env_proxies:
            for p in env_proxies.split(","):
                p = p.strip()
                if p and p not in loaded:
                    loaded.append(p)

        # 3. Check cached working proxies
        if not loaded and CACHE_PROXIES_FILE.exists():
            try:
                data = json.loads(CACHE_PROXIES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    loaded.extend([p for p in data if isinstance(p, str)])
            except Exception:
                pass

        self.proxies = list(dict.fromkeys(loaded))  # Remove duplicates preserving order
        return self.proxies

    def save_proxies(self, proxy_list: List[str]) -> None:
        """Save user proxy list to data/proxies.txt."""
        cleaned = [p.strip() for p in proxy_list if p.strip() and not p.strip().startswith("#")]
        self.proxies = list(dict.fromkeys(cleaned))
        try:
            PROXIES_FILE.write_text("\n".join(self.proxies) + "\n", encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write proxies to {PROXIES_FILE}: {e}")

    def fetch_public_proxies(self, limit: int = 30) -> List[str]:
        """Fetch free public HTTP/HTTPS proxies from community sources."""
        candidates = []
        for url in PUBLIC_PROXY_SOURCES:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode("utf-8", errors="ignore")
                    matches = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b", text)
                    if matches:
                        candidates.extend(matches)
                        if len(candidates) >= limit:
                            break
            except Exception:
                continue

        # Shuffle and sample
        random.shuffle(candidates)
        candidates = list(dict.fromkeys(candidates))[:limit]
        return candidates

    @staticmethod
    def test_proxy(proxy_str: str, timeout: int = 3) -> bool:
        """Test whether a proxy is actively responding."""
        if not proxy_str:
            return False
        formatted = proxy_str if ("://" in proxy_str) else f"http://{proxy_str}"
        try:
            proxy_handler = urllib.request.ProxyHandler({
                "http": formatted,
                "https": formatted
            })
            opener = urllib.request.build_opener(proxy_handler)
            req = urllib.request.Request(
                "http://httpbin.org/ip",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with opener.open(req, timeout=timeout) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        return False

    def get_proxy_for_account(
        self,
        account_id: str,
        account_index: int = 0,
        total_accounts: int = 1
    ) -> Optional[Dict[str, str]]:
        """
        Assign a distinct proxy / IP for the specified account.
        Returns a Playwright-compatible proxy dict:
        {"server": "http://...", "username": "...", "password": "..."} or None.
        """
        # Reload proxies in case user updated file or settings
        self.load_proxies()

        if not self.proxies:
            # No custom proxies available. Check if public proxies can be tested
            return None

        # Distribute proxies across accounts using round-robin / index mapping
        proxy_idx = account_index % len(self.proxies)
        selected_proxy = self.proxies[proxy_idx]
        self.account_proxy_map[account_id] = selected_proxy

        return self.format_playwright_proxy(selected_proxy)

    @staticmethod
    def format_playwright_proxy(proxy_str: str) -> Optional[Dict[str, str]]:
        """Parse proxy string into Playwright proxy dictionary."""
        if not proxy_str or not proxy_str.strip():
            return None

        proxy_str = proxy_str.strip()
        scheme = "http"
        user = None
        password = None
        host_port = proxy_str

        if "://" in proxy_str:
            scheme, host_port = proxy_str.split("://", 1)

        # Handle user:pass@host:port
        if "@" in host_port:
            auth_part, host_port = host_port.split("@", 1)
            if ":" in auth_part:
                user, password = auth_part.split(":", 1)
            else:
                user = auth_part

        server = f"{scheme}://{host_port}"
        result: Dict[str, str] = {"server": server}
        if user:
            result["username"] = user
        if password:
            result["password"] = password

        return result

    @staticmethod
    def get_current_public_ip(timeout: int = 4) -> str:
        """Fetch current public IP address of the machine."""
        services = [
            "https://api.ipify.org",
            "https://checkip.amazonaws.com",
            "https://icanhazip.com",
        ]
        for url in services:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    ip = resp.read().decode("utf-8").strip()
                    if ip and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                        return ip
            except Exception:
                continue
        return "Unknown"

    def get_status(self) -> Dict[str, Any]:
        """Return proxy pool statistics and configuration."""
        return {
            "total_proxies": len(self.proxies),
            "proxies": self.proxies,
            "has_proxies": len(self.proxies) > 0,
            "current_direct_ip": self.get_current_public_ip(),
            "account_mappings": self.account_proxy_map,
        }


proxy_manager = ProxyManager.get_instance()
