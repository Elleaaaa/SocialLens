# monitors/instagram.py
"""Instagram profile monitor using Playwright with saved session + manual login.

Loads the saved session (ig_state.json) first. If the session is still
valid, no login is needed. If expired, requires manual login via a
visible browser window. Login completion is auto-detected.
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
    MAX_POSTS_TO_DATE,
    DELAY_BETWEEN_POSTS_SEC,
    INSTAGRAM_SCROLL_ROUNDS,
    INSTAGRAM_STATE_FILE,
    LOGIN_TIMEOUT_SEC,
)

ISO_DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
FOLLOWERS_RE = re.compile(r"([\d.,]+[KMBkmb]?)\s*[Ff]ollowers")
SESSION_VALID_URL = "https://www.instagram.com/accounts/edit/"


class InstagramMonitor(BaseMonitor):
    platform = "instagram"
    metric_label = "followers"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._username = self._extract_username(profile["url"])
        self._logged_in = False

    @staticmethod
    def _extract_username(url):
        """Extract the username from an Instagram profile URL."""
        url = url.split("?")[0].rstrip("/")
        return url.split("/")[-1]

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _ensure_logged_in(self):
        """Ensure the user is logged in to Instagram.

        1. Check if the session (loaded via context storage_state) is valid.
        2. If valid, proceed without login.
        3. If expired, require manual login (auto-detected).
        """
        if self._logged_in:
            return True

        # Session is loaded via context storage_state in main.py
        if await self._is_session_valid():
            print("  - Instagram session still valid, skipping login")
            self._logged_in = True
            return True

        # Session is not valid — require fresh manual login
        print("  - Instagram login required. Please log in in the browser window.")
        print(
            f"  - Waiting up to {LOGIN_TIMEOUT_SEC // 60} minutes for you to log in..."
        )

        if await self._do_manual_login():
            self._logged_in = True
            return True

        return False

    async def _is_session_valid(self):
        """Check if the current browser session is authenticated.

        Navigates to the account edit page. If not logged in, Instagram
        redirects to the login page.
        """
        try:
            await self.page.goto(SESSION_VALID_URL, wait_until="domcontentloaded")
            await self.page.wait_for_timeout(3000)
            if "login" in self.page.url:
                return False
            return True
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
            if "login" in current_url or "challenge" in current_url:
                return False
            search = await self.page.query_selector(
                'input[placeholder*="Search"], input[aria-label*="Search"]'
            )
            if search:
                return True
            nav_links = await self.page.query_selector_all(
                'nav a, [role="navigation"] a'
            )
            if len(nav_links) >= 3:
                return True
            profile_link = await self.page.query_selector('a[href*="/accounts/"]')
            if profile_link:
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
                "https://www.instagram.com/accounts/login/",
                wait_until="domcontentloaded",
            )
            await self.page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"  ! Could not open Instagram login page: {exc}")
            return False

        # Handle cookie consent dialog
        try:
            decline = self.page.locator("button:has-text('Decline optional cookies')")
            if await decline.count() > 0:
                await decline.click()
                await self.page.wait_for_timeout(1000)
        except Exception:
            pass

        start = time.time()
        poll_ms = 2000
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            if scan_state.is_cancelled():
                print("  ! Login cancelled by user")
                return False
            await self.page.wait_for_timeout(poll_ms)
            if await self._verify_logged_in_on_page():
                print("  - Instagram login detected. Saving fresh session...")
                try:
                    await self.page.context.storage_state(path=INSTAGRAM_STATE_FILE)
                    print(f"  - Session saved to {INSTAGRAM_STATE_FILE}")
                except Exception as exc:
                    print(f"  ! Could not save session state: {exc}")
                await self.page.wait_for_timeout(2000)
                return True

        print("  ! Instagram login timed out. Skipping Instagram profiles.")
        return False

    async def _navigate_to_profile(self):
        """Navigate to the profile page and verify we're not redirected
        to login.
        """
        profile_url = f"https://www.instagram.com/{self._username}/"
        try:
            await self.page.goto(profile_url, wait_until="domcontentloaded")
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
        """Fetch posts with publish dates, stopping early."""
        logged_in = await self._ensure_logged_in()
        if not logged_in:
            return []

        if not await self._navigate_to_profile():
            print("  ! Could not load profile after login")
            return []

        if seen_shortcodes is None:
            seen_shortcodes = set()

        print("  - Phase 1: collecting post shortcodes from grid...")
        all_shortcodes = []
        stop_shortcode = None
        stale_scrolls = 0
        max_rounds = INSTAGRAM_SCROLL_ROUNDS if INSTAGRAM_SCROLL_ROUNDS > 0 else 100
        scroll_round = 0
        while scroll_round < max_rounds:
            scroll_round += 1
            shortcodes = await self._collect_shortcodes()
            new_shortcodes = [sc for sc in shortcodes if sc not in all_shortcodes]
            if new_shortcodes:
                stale_scrolls = 0
                for sc in new_shortcodes:
                    all_shortcodes.append(sc)
                    if sc in seen_shortcodes:
                        stop_shortcode = sc
                        break
                if stop_shortcode:
                    break
            else:
                stale_scrolls += 1
                if stale_scrolls >= 6:
                    break
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.wait_for_timeout(2000)

        if stop_shortcode:
            print(
                f"  - Stopped scrolling: hit already-seen post "
                f"({stop_shortcode}) at position {len(all_shortcodes)}"
            )
        else:
            print(f"  - Grid exhausted after {len(all_shortcodes)} posts")

        new_shortcodes = [sc for sc in all_shortcodes if sc not in seen_shortcodes]
        print(
            f"  - {len(new_shortcodes)} new posts to date "
            f"(out of {len(all_shortcodes)} on grid)"
        )
        if not new_shortcodes:
            return []

        # Apply MAX_POSTS_TO_DATE cap if set
        limit = MAX_POSTS_TO_DATE if MAX_POSTS_TO_DATE > 0 else len(new_shortcodes)
        new_shortcodes = new_shortcodes[:limit]
        if MAX_POSTS_TO_DATE > 0 and len(new_shortcodes) == MAX_POSTS_TO_DATE:
            print(f"  - Capped at {MAX_POSTS_TO_DATE} posts for date extraction")

        print("  - Phase 2: extracting publish dates...")
        posts = []
        for shortcode in new_shortcodes:
            try:
                published_at = await self._get_post_date(shortcode)
            except Exception as exc:
                print(f"    ! error on {shortcode}: {exc}")
                published_at = None
            post_url = f"https://www.instagram.com/p/{shortcode}/"
            if published_at:
                print(f"    OK {shortcode} -> {published_at}")
            else:
                print(f"    -- {shortcode} -> no date found")
            posts.append(self.make_post(shortcode, "", post_url, published_at))
            await asyncio.sleep(DELAY_BETWEEN_POSTS_SEC)
        print(f"  - Total: {len(posts)} new posts collected")
        return posts

    async def _collect_shortcodes(self):
        """Gather unique post shortcodes from rendered grid links."""
        links = await self.page.query_selector_all('a[href*="/p/"], a[href*="/reel/"]')
        seen, shortcodes = set(), []
        for link in links:
            href = await link.get_attribute("href") or ""
            shortcode = href.rstrip("/").split("/")[-1]
            if not shortcode or shortcode in seen:
                continue
            seen.add(shortcode)
            shortcodes.append(shortcode)
        return shortcodes

    async def _get_post_date(self, shortcode):
        """Extract the publish datetime from a post page."""
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        try:
            response = await self.page.goto(post_url, wait_until="domcontentloaded")
            if response and response.status == 429:
                print(f"    ! 429 rate limited on {shortcode}, backing off...")
                await asyncio.sleep(30)
                return None
            try:
                await self.page.wait_for_selector("time", timeout=8000)
            except Exception:
                pass
            await self.page.wait_for_timeout(1000)
            time_elements = await self.page.query_selector_all("time[datetime]")
            if time_elements:
                main_times = await self.page.query_selector_all(
                    "article time[datetime]"
                )
                target_elements = main_times if main_times else time_elements
                for el in target_elements:
                    dt = await el.get_attribute("datetime")
                    if dt and ISO_DATETIME_RE.search(dt):
                        return self._normalize_datetime(dt)
            html = await self.page.content()
            m = ISO_DATETIME_RE.search(html)
            if m:
                return self._normalize_datetime(m.group(1))
        except Exception as exc:
            print(f"    ! date error for {shortcode}: {exc}")
        return None

    @staticmethod
    def _normalize_datetime(value):
        """Normalize a datetime string to ISO 8601 with UTC timezone."""
        try:
            cleaned = value.strip().rstrip("Z")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, TypeError):
            return value

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
            desc = (
                await self.page.get_attribute('meta[name="description"]', "content")
                or ""
            )
            followers = self._extract_followers(desc)
            if followers:
                self.followers = followers
                return self.followers

            og = (
                await self.page.get_attribute(
                    'meta[property="og:description"]', "content"
                )
                or ""
            )
            followers = self._extract_followers(og)
            if followers:
                self.followers = followers
                return self.followers

            body = await self.page.inner_text("body")
            followers = self._extract_followers(body)
            if followers:
                self.followers = followers
                return self.followers

            print("  ! instagram: followers text not found")
        except Exception as exc:
            print(f"  ! instagram stats error: {exc}")
            self.followers = 0
        return self.followers

    @staticmethod
    def _extract_followers(text):
        """Pull the follower count from a block of text."""
        m = FOLLOWERS_RE.search(text)
        if m:
            return parse_count(m.group(1))
        return 0


# ----------------------------------------------------------------------
# Standalone login helper (called from main.py --login)
# ----------------------------------------------------------------------


async def interactive_login(playwright):
    """Open a visible browser for one-time manual Instagram login."""
    from config import INSTAGRAM_STATE_FILE, USER_AGENT

    browser = await playwright.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        print("=" * 60)
        print("  Instagram Manual Login")
        print("=" * 60)
        print()
        print("  1. Log in to Instagram with your credentials")
        print("  2. Complete any security checkpoint or 2FA if prompted")
        print("  3. Login is detected automatically")
        print()

        await page.goto(
            "https://www.instagram.com/accounts/login/",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(2000)

        try:
            decline = page.locator("button:has-text('Decline optional cookies')")
            if await decline.count() > 0:
                await decline.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        start = time.time()
        poll_ms = 2000
        logged_in = False
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            await page.wait_for_timeout(poll_ms)
            current_url = page.url
            if "login" not in current_url and "challenge" not in current_url:
                await page.wait_for_timeout(3000)
                if "login" not in page.url:
                    logged_in = True
                    break

        if logged_in:
            await context.storage_state(path=INSTAGRAM_STATE_FILE)
            print(f"  Login successful. Session saved to: {INSTAGRAM_STATE_FILE}")
        else:
            print("  Login timed out. Session was not saved.")
    finally:
        await browser.close()
    return logged_in
