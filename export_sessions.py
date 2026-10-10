import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from account_manager import list_saved_accounts, get_session_dir, DEFAULT_USER_AGENT, DEFAULT_VIEWPORT

SESSIONS_OUT_DIR = Path("data/sessions")
SESSIONS_OUT_DIR.mkdir(parents=True, exist_ok=True)

async def export_all():
    accounts = list_saved_accounts()
    print(f"Exporting storage state for {len(accounts)} accounts...")
    async with async_playwright() as p:
        for acc in accounts:
            sdir = get_session_dir(acc)
            try:
                ctx = await p.chromium.launch_persistent_context(
                    str(sdir), headless=True, user_agent=DEFAULT_USER_AGENT, viewport=DEFAULT_VIEWPORT
                )
                state = await ctx.storage_state()
                await ctx.close()
                out_path = SESSIONS_OUT_DIR / f"{acc}.json"
                out_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                cookies_count = len(state.get("cookies", []))
                print(f"[+] Exported {acc}: {cookies_count} cookies -> {out_path.name}")
            except Exception as e:
                print(f"[-] Failed on {acc}: {e}")

if __name__ == "__main__":
    asyncio.run(export_all())
