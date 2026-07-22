# login_server.py
"""Screenshot-based remote browser login.

Works on any server (no display, no VNC, no system packages needed).
Uses headless Chromium + screenshots + input injection.
"""

import asyncio
import time
import uuid

from playwright.async_api import async_playwright

from config import (
    USER_AGENT,
    INSTAGRAM_STATE_FILE,
    FB_SESSION_FILE,
    THREADS_STATE_FILE,
    TIKTOK_STATE_FILE,
    LOGIN_TIMEOUT_SEC,
)

LOGIN_URLS = {
    "instagram": "https://www.instagram.com/accounts/login/",
    "facebook": "https://www.facebook.com/login/",
    "threads": "https://www.threads.net/login",
    "tiktok": "https://www.tiktok.com/login",
}

STATE_FILES = {
    "instagram": INSTAGRAM_STATE_FILE,
    "facebook": FB_SESSION_FILE,
    "threads": THREADS_STATE_FILE,
    "tiktok": TIKTOK_STATE_FILE,
}

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800


class LoginSession:
    """Manages a single screenshot-based login session."""

    def __init__(self, platform_name: str):
        self.platform = platform_name
        self.session_id = str(uuid.uuid4())[:8]
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.started_at = 0
        self.completed = False

    async def start(self) -> dict:
        """Launch headless browser and navigate to login page."""
        self.started_at = time.time()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = await self.browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        )
        self.page = await self.context.new_page()

        login_url = LOGIN_URLS.get(self.platform, "")
        if login_url:
            await self.page.goto(login_url, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)

        return {
            "session_id": self.session_id,
            "platform": self.platform,
            "login_url": login_url,
            "mode": "screenshot",
        }

    async def screenshot(self) -> bytes:
        """Take a screenshot of the current page."""
        if not self.page:
            return b""
        return await self.page.screenshot(type="png")

    async def click(self, x: int, y: int):
        """Click at coordinates on the page."""
        if not self.page:
            return
        await self.page.mouse.click(x, y)
        await self.page.wait_for_timeout(500)

    async def type_text(self, text: str):
        """Type text on the page (at current cursor position)."""
        if not self.page:
            return
        await self.page.keyboard.type(text)
        await self.page.wait_for_timeout(300)

    async def press_key(self, key: str):
        """Press a key (e.g., 'Enter', 'Tab')."""
        if not self.page:
            return
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(500)

    async def save_and_close(self) -> dict:
        """Save the browser session and clean up."""
        state_file = STATE_FILES.get(self.platform)
        saved = False

        if state_file and self.context:
            try:
                await self.context.storage_state(path=state_file)
                saved = True
            except Exception as exc:
                print(f"  ! Could not save session: {exc}")

        await self._cleanup()
        self.completed = True

        return {
            "platform": self.platform,
            "saved": saved,
            "state_file": state_file if saved else None,
        }

    async def cancel(self):
        """Cancel without saving and clean up."""
        await self._cleanup()
        self.completed = True

    async def _cleanup(self):
        """Close browser and all resources."""
        try:
            if self.page:
                await self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass

    def is_expired(self) -> bool:
        """Check if the session has exceeded the login timeout."""
        if self.completed:
            return True
        return time.time() - self.started_at > LOGIN_TIMEOUT_SEC


# Singleton session manager
_current_session: LoginSession | None = None


async def start_login_session(platform_name: str) -> dict:
    """Start a new login session."""
    global _current_session

    if _current_session and not _current_session.completed:
        await _current_session.cancel()

    _current_session = LoginSession(platform_name)
    return await _current_session.start()


async def complete_login_session() -> dict:
    """Save the current session and clean up."""
    global _current_session
    if not _current_session or _current_session.completed:
        return {"error": "No active login session"}

    result = await _current_session.save_and_close()
    _current_session = None
    return result


async def cancel_login_session() -> dict:
    """Cancel the current session without saving."""
    global _current_session
    if not _current_session or _current_session.completed:
        return {"error": "No active login session"}

    await _current_session.cancel()
    _current_session = None
    return {"status": "cancelled"}


def get_session_status() -> dict:
    """Get the status of the current login session."""
    if not _current_session:
        return {"active": False}

    if _current_session.is_expired() and not _current_session.completed:
        asyncio.create_task(cancel_login_session())
        return {"active": False, "expired": True}

    return {
        "active": not _current_session.completed,
        "platform": _current_session.platform,
        "session_id": _current_session.session_id,
        "elapsed": int(time.time() - _current_session.started_at),
        "timeout": LOGIN_TIMEOUT_SEC,
        "mode": "screenshot",
    }


async def get_screenshot() -> bytes:
    """Get a screenshot of the current login session."""
    if not _current_session or _current_session.completed:
        return b""
    return await _current_session.screenshot()


async def click_page(x: int, y: int):
    """Click at coordinates on the current login session."""
    if not _current_session or _current_session.completed:
        return
    await _current_session.click(x, y)


async def type_on_page(text: str):
    """Type text on the current login session."""
    if not _current_session or _current_session.completed:
        return
    await _current_session.type_text(text)


async def press_page_key(key: str):
    """Press a key on the current login session."""
    if not _current_session or _current_session.completed:
        return
    await _current_session.press_key(key)
