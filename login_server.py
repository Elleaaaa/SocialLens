# login_server.py
"""Remote browser login via noVNC (Linux) or direct browser (Windows).

On Linux: launches Xvfb + x11vnc + websockify so users can log in
through their web browser via noVNC.

On Windows: launches a visible browser directly (no VNC needed).
"""

import asyncio
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path

from playwright.async_api import async_playwright

from config import (
    BASE_DIR,
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

NOVNC_PORT = 8080
DISPLAY_NUM = 99
IS_WINDOWS = platform.system() == "Windows"


class LoginSession:
    """Manages a single remote login session."""

    def __init__(self, platform_name: str):
        self.platform = platform_name
        self.session_id = str(uuid.uuid4())[:8]
        self.display = f":{DISPLAY_NUM}"
        self.xvfb_proc = None
        self.vnc_proc = None
        self.websockify_proc = None
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None
        self.started_at = 0
        self.completed = False

    async def start(self) -> dict:
        """Launch browser (and VNC stack on Linux)."""
        self.started_at = time.time()

        if IS_WINDOWS:
            return await self._start_windows()
        else:
            return await self._start_linux()

    async def _start_windows(self):
        """On Windows, just launch a visible browser directly."""
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = await self.browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        self.page = await self.context.new_page()

        login_url = LOGIN_URLS.get(self.platform, "")
        if login_url:
            await self.page.goto(login_url, wait_until="domcontentloaded")

        return {
            "session_id": self.session_id,
            "platform": self.platform,
            "vnc_url": None,
            "login_url": login_url,
            "mode": "direct",
        }

    async def _start_linux(self):
        """On Linux, launch Xvfb + VNC + browser."""
        # Kill any existing processes on this display
        subprocess.run(["pkill", "-f", f"Xvfb {self.display}"], capture_output=True)
        subprocess.run(["pkill", "-f", "x11vnc.*:99"], capture_output=True)
        subprocess.run(
            ["pkill", "-f", f"websockify.*{NOVNC_PORT}"], capture_output=True
        )

        # Start Xvfb (virtual framebuffer)
        self.xvfb_proc = subprocess.Popen(
            ["Xvfb", self.display, "-screen", "0", "1280x800x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)

        env = os.environ.copy()
        env["DISPLAY"] = self.display

        # Start x11vnc (VNC server)
        self.vnc_proc = subprocess.Popen(
            [
                "x11vnc",
                "-display",
                self.display,
                "-nopw",
                "-forever",
                "-shared",
                "-rfbport",
                "5999",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        time.sleep(1)

        # Start websockify (WebSocket proxy for noVNC)
        novnc_path = self._find_novnc_path()
        self.websockify_proc = subprocess.Popen(
            ["websockify", "--web", novnc_path, str(NOVNC_PORT), "localhost:5999"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)

        # Launch browser on the virtual display
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            env=env,
        )
        self.context = await self.browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        self.page = await self.context.new_page()

        login_url = LOGIN_URLS.get(self.platform, "")
        if login_url:
            await self.page.goto(login_url, wait_until="domcontentloaded")

        return {
            "session_id": self.session_id,
            "platform": self.platform,
            "vnc_url": f"/vnc/vnc.html?autoconnect=true&host=localhost&port={NOVNC_PORT}",
            "login_url": login_url,
            "mode": "vnc",
        }

    async def save_and_close(self) -> dict:
        """Save the browser session and clean up all processes."""
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
        """Close browser and kill all subprocesses."""
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

        if not IS_WINDOWS:
            for proc in [self.websockify_proc, self.vnc_proc, self.xvfb_proc]:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()

    def is_expired(self) -> bool:
        """Check if the session has exceeded the login timeout."""
        if self.completed:
            return True
        return time.time() - self.started_at > LOGIN_TIMEOUT_SEC

    @staticmethod
    def _find_novnc_path() -> str:
        """Find the noVNC web directory (Linux only)."""
        candidates = [
            "/usr/share/novnc",
            "/usr/share/novnc/web",
            "/var/www/novnc",
            "/opt/novnc",
        ]
        for path in candidates:
            if os.path.isdir(path) and os.path.exists(os.path.join(path, "vnc.html")):
                return path
        fallback = str(BASE_DIR / "novnc_web")
        os.makedirs(fallback, exist_ok=True)
        return fallback


# Singleton session manager
_current_session: LoginSession | None = None


async def start_login_session(platform_name: str) -> dict:
    """Start a new remote login session."""
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
        "mode": "direct" if IS_WINDOWS else "vnc",
    }
