"""Instagram profile monitor using Playwright with saved session + manual login.

Loads the saved session (ig_state.json) first. If the session is still
valid, no login is needed. If expired, requires manual login via a
visible browser window. Login completion is auto-detected.

Scanning logic:
- Every scan stops as soon as a post older than 7 days is found.
- Every scan also stops when hitting an already-seen post (incremental).
- Whichever condition triggers first wins.
"""

import asyncio
import json
import re
import time
from pathlib import Path

from monitors.base import BaseMonitor, parse_count, is_older_than_days
from config import (
    MAX_POSTS_TO_DATE,
    DELAY_BETWEEN_POSTS_SEC,
    INSTAGRAM_STATE_FILE,
    PAGE_TIMEOUT_MS,
    FIRST_SCAN_DAYS_LIMIT,
    INSTAGRAM_SCROLL_ROUNDS,
)

SESSION_VALID_URL = "https://www.instagram.com/accounts/edit/"
LOGIN_TIMEOUT_SEC = 300  # 5 minutes max to complete login


class InstagramMonitor(BaseMonitor):
    platform = "instagram"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._username = self._extract_username(profile["url"])

    @staticmethod
    def _extract_username(url):
        """Extract the username from an Instagram profile URL."""
        url = url.split("?")[0].rstrip("/")
        if url.endswith("/"):
            url = url[:-1]
        return url.split("/")[-1]

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _load_saved_session(self):
        """Load cookies from the saved session file into the browser context."""
        state_path = Path(INSTAGRAM_STATE_FILE)
        if not state_path.exists():
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            cookies = state.get("cookies", [])
            if cookies:
                await self.page.context.add_cookies(cookies)
                print("  - Loaded saved Instagram session")
        except Exception as exc:
            print(f"  ! Could not load saved session: {exc}")

    async def _ensure_logged_in(self):
        """Ensure the user is logged in to Instagram."""
        if self._logged_in:
            return True

        await self._load_saved_session()

        if await self._is_session_valid():
            print("  - Instagram session still valid, skipping login")
            self._logged_in = True
            return True

        print("  - Instagram login required. Please log in in the browser window.")
        print(
            f"  - Waiting up to {LOGIN_TIMEOUT_SEC // 60} minutes for you to log in..."
        )

        if await self._do_manual_login():
            self._logged_in = True
            return True

        return False

    async def _is_session_valid(self):
        """Check if the current browser session is authenticated."""
        try:
            await self.page.goto(
                SESSION_VALID_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
            current_url = self.page.url
            if "login" in current_url:
                return False
            return True
        except Exception:
            return False

    async def _verify_logged_in_on_page(self):
        """Verify login by checking for authenticated-only elements."""
        try:
            current_url = self.page.url
            if "login" in current_url or "challenge" in current_url:
                return False
            search = await self.page.query_selector(
                'input[placeholder*="Search"], input[aria-label*="Search"]'
            )
            if search:
                return True
            nav = await self.page.query_selector('nav[role="navigation"]')
            if nav:
                return True
            avatar = await self.page.query_selector(
                'a[href*="/accounts/"], img[alt*="profile"]'
            )
            if avatar:
                return True
            return False
        except Exception:
            return False

    async def _do_manual_login(self):
        """Open the login page and wait for the user to log in."""
        try:
            await self.page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  ! Could not open Instagram login page: {exc}")
            return False

        try:
            decline = self.page.locator("button:has-text('Decline optional cookies')")
            if await decline.count() > 0:
                await decline.first.click()
                await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        start = time.time()
        poll_ms = 2000
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            await self.page.wait_for_timeout(poll_ms)

            if await self._verify_logged_in_on_page():
                print("  - Instagram login detected. Saving fresh session...")
                try:
                    await self.page.context.storage_state(path=INSTAGRAM_STATE_FILE)
                    print(f"  - Session saved to {INSTAGRAM_STATE_FILE}")
                except Exception as exc:
                    print(f"  ! Could not save session state: {exc}")
                await self.page.wait_for_timeout(3000)
                return True

        print("  ! Instagram login timed out. Skipping Instagram profiles.")
        return False

    async def _navigate_to_profile(self):
        """Navigate to the profile page and verify we're not redirected to login."""
        profile_url = f"https://www.instagram.com/{self._username}/"
        try:
            await self.page.goto(
                profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  ! instagram: could not navigate to profile: {exc}")
            return False

        if "login" in self.page.url:
            print("  ! Redirected to login when loading profile")
            return False
        return True

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch posts with publish dates, stopping early.

        Every scan stops when a post older than 7 days is found,
        or when hitting an already-seen post (incremental stop).
        Whichever condition triggers first wins.
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()

        logged_in = await self._ensure_logged_in()
        if not logged_in:
            return []

        if not await self._navigate_to_profile():
            print("  ! Could not load profile after login")
            return []

        print(f"  - Capturing posts from last {FIRST_SCAN_DAYS_LIMIT} days only")

        print("  - Phase 1: collecting post shortcodes from grid...")
        all_shortcodes = await self._scroll_grid_for_shortcodes(seen_shortcodes)

        new_shortcodes = [sc for sc in all_shortcodes if sc not in seen_shortcodes]
        print(
            f"  - {len(new_shortcodes)} new posts to date "
            f"(of {len(all_shortcodes)} total)"
        )

        if not new_shortcodes:
            print("  - No new posts to fetch dates for")
            return []

        # Phase 2: Visit each new post page to extract the date
        posts = []
        for shortcode in new_shortcodes:
            published_at = await self._get_post_date(shortcode)
            post_url = f"https://www.instagram.com/p/{shortcode}/"
            if not published_at:
                print(f"    -- {shortcode} -> no date found")
            else:
                # Stop if post is older than 7 days (applies to every scan)
                if is_older_than_days(published_at, FIRST_SCAN_DAYS_LIMIT):
                    print(
                        f"  - Stopped: post {shortcode} is older than "
                        f"{FIRST_SCAN_DAYS_LIMIT} days"
                    )
                    break
            posts.append(
                self.make_post(
                    shortcode,
                    "",
                    post_url,
                    published_at,
                )
            )
            await asyncio.sleep(DELAY_BETWEEN_POSTS_SEC)

        print(f"  - Total: {len(posts)} new posts collected")
        return posts

    async def _scroll_grid_for_shortcodes(self, seen_shortcodes):
        """Scroll the Instagram grid to collect post shortcodes.

        Stops when hitting an already-seen post (incremental stop),
        or when the grid is exhausted.
        """
        all_shortcodes = []
        stopped_early = False
        stale_scrolls = 0
        stale_scroll_limit = max(1, INSTAGRAM_SCROLL_ROUNDS)

        for scroll_round in range(INSTAGRAM_SCROLL_ROUNDS):
            links = await self.page.query_selector_all(
                'a[href*="/p/"], a[href*="/reel/"]'
            )
            new_shortcodes = []
            for link in links:
                href = await link.get_attribute("href") or ""
                shortcode = self._extract_shortcode(href)
                if not shortcode or shortcode in all_shortcodes:
                    continue
                new_shortcodes.append(shortcode)

            if new_shortcodes:
                stale_scrolls = 0
                for sc in new_shortcodes:
                    all_shortcodes.append(sc)
                    # Stop at already-seen post (incremental)
                    if sc in seen_shortcodes:
                        print(f"  - Stopped scrolling: hit already-seen post ({sc})")
                        stopped_early = True
                        break
            else:
                stale_scrolls += 1

            if stopped_early:
                break
            if stale_scrolls >= stale_scroll_limit:
                print(f"  - Grid exhausted after {len(all_shortcodes)} posts")
                break

            print(f"  - Scroll {scroll_round + 1}: {len(all_shortcodes)} shortcodes")
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.wait_for_timeout(2000)

        return all_shortcodes

    @staticmethod
    def _extract_shortcode(href):
        """Extract the shortcode from an Instagram post or reel URL."""
        # Match /p/SHORTCODE/ or /reel/SHORTCODE/
        m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
        return m.group(1) if m else None

    async def _get_post_date(self, shortcode):
        """Extract the publish datetime from a post page.
        Non-blocking: tries query_selector (instant) instead of
        wait_for_selector (8s timeout). Falls back to regex on the
        page HTML. Returns None if no date is found, so the caller
        keeps collecting rather than stopping.
        """
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        try:
            await self.page.goto(
                post_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            # Non-blocking: returns immediately if no <time> element
            # exists, instead of waiting 8 seconds for one to appear.
            times = await self.page.query_selector_all("time[datetime]")
            for t in times:
                dt = await t.get_attribute("datetime")
                if dt:
                    return dt
            html = await self.page.content()
            m = re.search(
                r'datetime="(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+\dZ:.]+)"',
                html,
            )
            if m:
                return m.group(1)
        except Exception as exc:
            print(f"    ! date error for {shortcode}: {exc}")
        return None

    async def fetch_stats(self):
        """Get the exact follower count from the profile page."""
        if not self._logged_in:
            logged_in = await self._ensure_logged_in()
            if not logged_in:
                self.followers = 0
                return self.followers

        if not await self._navigate_to_profile():
            print("  ! instagram: could not load profile for stats")
            self.followers = 0
            return self.followers

        try:
            await self.page.wait_for_timeout(3000)
            html = await self.page.content()

            m = re.search(r"(\d[\d.,]*[KMBkmb]?)\s*followers?", html, re.IGNORECASE)
            if m:
                self.followers = parse_count(m.group(1))
                print(f"  - Followers: {self.followers:,}")
                return self.followers

            m = re.search(
                r'<meta\s+name="description"\s+content="([^"]*followers[^"]*)"',
                html,
                re.IGNORECASE,
            )
            if m:
                desc = m.group(1)
                m2 = re.search(r"(\d[\d.,]*[KMBkmb]?)\s*[Ff]ollowers", desc)
                if m2:
                    self.followers = parse_count(m2.group(1))
                    print(f"  - Followers: {self.followers:,}")
                    return self.followers

            print("  ! instagram: followers text not found")
            self.followers = 0
        except Exception as exc:
            print(f"  ! instagram stats error: {exc}")
            self.followers = 0
        return self.followers
