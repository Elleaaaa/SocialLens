# config.py
"""Central configuration. Change values here, not across the codebase."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DB_PATH = DATA_DIR / "monitor.db"
LOCAL_TIMEZONE_OFFSET = int(os.environ.get("LOCAL_TIMEZONE_OFFSET", "8"))

BROWSER_HEADLESS = os.environ.get("BROWSER_HEADLESS", "false").lower() == "true"
BROWSER_SLOW_MO = 0
PAGE_TIMEOUT_MS = 30000
NAVIGATION_WAIT_MS = 15000

DELAY_BETWEEN_PROFILES_SEC = 5
DELAY_BETWEEN_POSTS_SEC = 2
MAX_POSTS_TO_DATE = 0
INSTAGRAM_SCROLL_ROUNDS = 0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

INSTAGRAM_STATE_FILE = os.environ.get(
    "INSTAGRAM_STATE_FILE", str(BASE_DIR / "ig_state.json")
)
TIKTOK_STATE_FILE = os.environ.get(
    "TIKTOK_STATE_FILE", str(BASE_DIR / "tiktok_state.json")
)
THREADS_STATE_FILE = os.environ.get(
    "THREADS_STATE_FILE", str(BASE_DIR / "threads_state.json")
)
FB_SESSION_FILE = os.environ.get("FB_SESSION_FILE", str(BASE_DIR / "fb_state.json"))

FB_SCROLL_ROUNDS = 30
FB_STALE_SCROLLS = 5
FB_SCROLL_DELAY_MS = 3500

THREADS_SCROLL_ROUNDS = 50
THREADS_STALE_SCROLLS = 10
THREADS_SCROLL_DELAY_MS = 5000

TIKTOK_SCROLL_ROUNDS = 50
TIKTOK_STALE_SCROLLS = 5
TIKTOK_SCROLL_DELAY_MS = 2000

YT_API_KEY = os.environ.get("YT_API_KEY", "")

MONITOR_API_KEY = os.environ.get("MONITOR_API_KEY", "")
MAX_CONCURRENT_SCANS = 1
SCAN_TIMEOUT_SEC = 3600
LOGIN_TIMEOUT_SEC = 300

# Auth credentials (for local/single-user use only)
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "sociallens")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "37nyF3078f!&KJpicuYA")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
SESSION_EXPIRY_DAYS = 7


def load_profiles():
    """Legacy loader kept for backward compatibility."""
    import json
    import warnings

    json_path = BASE_DIR / "profiles.json"
    if not json_path.exists():
        return [], 60

    warnings.warn(
        "Use storage.get_profiles() instead", DeprecationWarning, stacklevel=2
    )
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    profiles = data.get("profiles", [])
    schedule_minutes = data.get("schedule_minutes", 60)
    return profiles, schedule_minutes
