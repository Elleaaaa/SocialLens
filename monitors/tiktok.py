# monitors/tiktok.py
"""TikTok profile monitor using Playwright with saved session + manual login.

Loads the saved session (tiktok_state.json) first. If still valid,
no login is needed. If the profile page fails to load or returns a
block/captcha page, requires manual login via a visible browser
window. Login completion is auto-detected.

- Follower count: Parse __UNIVERSAL_DATA from profile page
- Video list: Scroll profile grid, collect /video/ links
- Video dates: Visit each video page, extract createTime from
  __UNIVERSAL_DATA (Unix timestamp)
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import scan_state
from monitors.base import BaseMonitor, parse_count
from config import (
    DELAY_BETWEEN_POSTS_SEC,
    MAX_POSTS_TO_DATE,
    PAGE_TIMEOUT_MS,
    TIKTOK_STATE_FILE,
    TIKTOK_SCROLL_ROUNDS,
    TIKTOK_STALE_SCROLLS,
    TIKTOK_SCROLL_DELAY_MS,
    LOGIN_TIMEOUT_SEC,
)

UNIVERSAL_DATA_RE = re.compile(
    r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
VIDEO_ID_RE = re.compile(r"/video/(\d+)")
CREATE_TIME_RE = re.compile(r'"createTime"\s*:\s*"?(\d{8,})"?')
LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email"


class TikTokMonitor(BaseMonitor):
    platform = "tiktok"
    metric_label = "followers"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._username = self._extract_username(profile["url"])
        self._profile_url = f"https://www.tiktok.com/@{self._username}"
        self._logged_in = False

    @staticmethod
    def _extract_username(url):
        """Extract username from a TikTok profile URL."""
        url = url.split("?")[0].rstrip("/")
        return url.split("@")[-1]

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _save_session(self):
        """Save current browser state to the session file."""
        try:
            state = await self.page.context.storage_state()
            with open(TIKTOK_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            print("  - Session saved for future runs")
        except Exception as exc:
            print(f"  ! Could not save session: {exc}")

    async def _ensure_logged_in(self):
        """Ensure we can access the TikTok profile page.

        1. Check if the session (loaded via context storage_state) is valid.
        2. If the page loads with valid data, proceed without login.
        3. If blocked (captcha, parse failure, connection reset),
           require manual login (auto-detected).
        """
        if self._logged_in:
            return True

        # Session is loaded via context storage_state in main.py
        if await self._is_session_valid():
            print("  - TikTok session still valid, skipping login")
            self._logged_in = True
            return True

        # Session is not valid — require fresh manual login
        print("  - TikTok login required. Please log in in the browser window.")
        print(
            f"  - Waiting up to {LOGIN_TIMEOUT_SEC // 60} minutes for you to log in..."
        )

        if await self._do_manual_login():
            self._logged_in = True
            return True

        return False

    async def _is_session_valid(self):
        """Check if the profile page loads successfully with valid data.

        TikTok doesn't have a clean login-redirect like Instagram, so
        we verify by checking that the profile page returns parseable
        __UNIVERSAL_DATA. If it does, the session (or anonymous
        access) is working.
        """
        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)

            html = await self.page.content()
            m = UNIVERSAL_DATA_RE.search(html)
            if m:
                data = json.loads(m.group(1))
                user_detail = data.get("__DEFAULT_SCOPE__", {}).get(
                    "webapp.user-detail", {}
                )
                user_info = user_detail.get("userInfo", {})
                if user_info:
                    return True
            return False
        except Exception:
            return False

    async def _verify_logged_in_on_page(self):
        """Verify login by checking for authenticated-only elements
        on the current page WITHOUT navigating away.

        Used during the login poll so we never disrupt the user
        mid-captcha or mid-challenge.
        """
        try:
            current_url = self.page.url
            # Must not be on login page
            if "login" in current_url:
                return False
            # Check for the upload button — only present when logged in
            upload = await self.page.query_selector(
                'a[href*="/upload"], [data-e2e="upload-icon"]'
            )
            if upload:
                return True
            # Check for the user's profile avatar in the nav bar
            avatar = await self.page.query_selector(
                '[data-e2e="profile-icon"], [data-e2e="avatar"]'
            )
            if avatar:
                return True
            # Check for the message inbox icon
            inbox = await self.page.query_selector(
                '[data-e2e="inbox-icon"], [data-e2e="message-icon"]'
            )
            if inbox:
                return True
            return False
        except Exception:
            return False

    async def _do_manual_login(self):
        """Open the login page and wait for the user to log in.

        Login completion is auto-detected by checking for authenticated
        elements. We never navigate away during the poll so the user
        can complete captcha and 2FA without interruption.
        """
        try:
            await self.page.goto(
                LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
            print("  - Opened TikTok login page. Please log in manually.")
        except Exception as exc:
            print(f"  ! Could not open TikTok login page: {exc}")
            return False

        # Poll until login is verified. We only OBSERVE the page —
        # we never navigate away, so the user can complete 2FA,
        # captcha, and checkpoints without being interrupted.
        start = time.time()
        poll_ms = 2000
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            if scan_state.is_cancelled():
                print("  ! Login cancelled by user")
                return False
            await self.page.wait_for_timeout(poll_ms)

            if await self._verify_logged_in_on_page():
                print("  - TikTok login detected. Saving fresh session...")
                await self._save_session()
                await self.page.wait_for_timeout(2000)
                return True
            # Not logged in yet — keep waiting.

        print("  ! TikTok login timed out. Skipping TikTok profiles.")
        return False

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    async def _fetch_universal_data(self, url):
        """Navigate to a TikTok page and parse __UNIVERSAL_DATA.

        Uses Playwright (TikTok blocks httpx with captcha pages).
        """
        try:
            response = await self.page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
            )
            await self.page.wait_for_timeout(3000)

            if response and response.status == 429:
                print(f"    ! 429 rate limited, backing off...")
                await asyncio.sleep(30)
                return None

            html = await self.page.content()
            m = UNIVERSAL_DATA_RE.search(html)
            if not m:
                return None

            return json.loads(m.group(1))
        except Exception as exc:
            print(f"    ! fetch error: {exc}")
            return None

    async def fetch_stats(self):
        """Get follower count from the TikTok profile page."""
        logged_in = await self._ensure_logged_in()
        if not logged_in:
            self.followers = 0
            return self.followers

        try:
            data = await self._fetch_universal_data(self._profile_url)
            if not data:
                print("  ! TikTok: could not parse profile page data")
                self.followers = 0
                return self.followers

            user_detail = data.get("__DEFAULT_SCOPE__", {}).get(
                "webapp.user-detail", {}
            )
            stats = user_detail.get("userInfo", {}).get("stats", {})

            followers = stats.get("followerCount", 0)
            video_count = stats.get("videoCount", 0)
            self.followers = followers
            print(f"  - Followers: {followers:,}")
            print(f"  - Videos: {video_count:,}")
            return self.followers

        except Exception as exc:
            print(f"  ! TikTok stats error: {exc}")
            self.followers = 0
            return self.followers

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch videos with publish dates.

        Uses Playwright to scroll the profile grid and collect video
        links, then visits each video page to extract the exact
        publish date (createTime timestamp).
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()

        logged_in = await self._ensure_logged_in()
        if not logged_in:
            return []

        # Phase 1: Scroll profile grid to collect video IDs
        video_ids = await self._scroll_grid_for_videos(seen_shortcodes)

        if not video_ids:
            print("  - No videos found on profile grid")
            return []

        print(f"  - Total unique videos: {len(video_ids)}")

        # Apply MAX_POSTS_TO_DATE cap if set
        limit = MAX_POSTS_TO_DATE if MAX_POSTS_TO_DATE > 0 else len(video_ids)
        video_ids = video_ids[:limit]
        if MAX_POSTS_TO_DATE > 0 and len(video_ids) == MAX_POSTS_TO_DATE:
            print(f"  - Capped at {MAX_POSTS_TO_DATE} videos for date extraction")

        # Phase 2: Visit each video page to extract the date
        print("  - Fetching publish dates from video pages...")
        posts = []
        dated_count = 0

        for i, video_id in enumerate(video_ids):
            try:
                published_at = await self._fetch_video_date(video_id)
            except Exception as exc:
                print(f"    ! error on {video_id}: {exc}")
                published_at = None
            await asyncio.sleep(DELAY_BETWEEN_POSTS_SEC)

            if published_at:
                dated_count += 1

            if i < 20 or (i + 1) % 50 == 0:
                status = "OK" if published_at else "--"
                print(f"    {status} {video_id} -> {published_at or 'no date'}")

            url = f"https://www.tiktok.com/@{self._username}/video/{video_id}"
            title = ""
            posts.append(self.make_post(video_id, title, url, published_at))

        print(f"  - Dated: {dated_count}/{len(posts)}")
        print(f"  - Total: {len(posts)} videos collected")
        return posts

    async def _scroll_grid_for_videos(self, seen_shortcodes):
        """Scroll the TikTok profile grid to collect video IDs.

        TikTok loads videos via JavaScript as the user scrolls.
        Uses window.scrollTo + End key to trigger lazy loading.
        Stops early when hitting an already-seen video (incremental).
        """
        print(f"  - Loading TikTok profile: {self._profile_url}")
        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"  ! TikTok: could not load profile page: {exc}")
            return []

        all_video_ids = []
        stopped_early = False
        stale_scrolls = 0
        for scroll_round in range(TIKTOK_SCROLL_ROUNDS):
            links = await self.page.query_selector_all('a[href*="/video/"]')
            new_ids = []
            for link in links:
                href = await link.get_attribute("href") or ""
                m = VIDEO_ID_RE.search(href)
                if m:
                    vid = m.group(1)
                    if vid not in all_video_ids:
                        new_ids.append(vid)
            if new_ids:
                stale_scrolls = 0
                for vid in new_ids:
                    all_video_ids.append(vid)
                    if vid in seen_shortcodes:
                        print(
                            f"  - Stopped early: hit already-seen "
                            f"video ({vid}) at position {len(all_video_ids)}"
                        )
                        stopped_early = True
                        break
            else:
                stale_scrolls += 1
            if stopped_early:
                break
            if stale_scrolls >= TIKTOK_STALE_SCROLLS:
                print(
                    f"  - Grid exhausted (no new videos after "
                    f"{TIKTOK_STALE_SCROLLS} scrolls)"
                )
                break
            print(f"  - Scroll {scroll_round + 1}: {len(all_video_ids)} videos total")
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.keyboard.press("End")
            await self.page.wait_for_timeout(TIKTOK_SCROLL_DELAY_MS)
        return all_video_ids

    async def _fetch_video_date(self, video_id):
        """Fetch exact publish date from a TikTok video page.

        Extracts createTime (Unix timestamp) from
        __UNIVERSAL_DATA_FOR_REHYDRATION__.
        """
        url = f"https://www.tiktok.com/@{self._username}/video/{video_id}"

        try:
            data = await self._fetch_universal_data(url)
            if not data:
                return None

            video_detail = data.get("__DEFAULT_SCOPE__", {}).get(
                "webapp.video-detail", {}
            )

            # Check for deleted/private videos
            status_code = video_detail.get("statusCode", 0)
            if status_code != 0:
                return None

            item_struct = video_detail.get("itemInfo", {}).get("itemStruct", {})

            create_time = item_struct.get("createTime")
            if create_time:
                return self._timestamp_to_iso(int(create_time))

            # Fallback: search for createTime in the raw data
            text = json.dumps(data)
            m = CREATE_TIME_RE.search(text)
            if m:
                return self._timestamp_to_iso(int(m.group(1)))

        except Exception:
            pass
        return None

    @staticmethod
    def _timestamp_to_iso(ts):
        """Convert a Unix timestamp to ISO 8601 with UTC timezone."""
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OSError):
            return None
