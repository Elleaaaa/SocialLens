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
    POST_DATE_WAIT_MS,
)

SESSION_VALID_URL = "https://www.instagram.com/accounts/edit/"
LOGIN_TIMEOUT_SEC = 300  # 5 minutes max to complete login


def _extract_taken_at(data):
    """Recursively search a JSON blob for taken_at_timestamp / taken_at (int)."""
    if isinstance(data, dict):
        for key in ("taken_at_timestamp", "taken_at"):
            v = data.get(key)
            if isinstance(v, int):
                return v
        for v in data.values():
            r = _extract_taken_at(v)
            if r:
                return r
    elif isinstance(data, list):
        for v in data:
            r = _extract_taken_at(v)
            if r:
                return r
    return None


def _epoch_to_iso(ts):
    """Convert a Unix epoch (seconds) to ISO 8601 UTC string."""
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
    except Exception:
        return None


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

        # Attach the GraphQL listener BEFORE navigating to the profile,
        # so we capture the initial page-load posts query (first ~12 posts).
        # If attached after navigation, the first page's response is missed.
        shortcode_dates = {}

        async def _handle_response(response):
            try:
                if "graphql/query" not in response.url:
                    return
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype:
                    return
                data = await response.json()
            except Exception:
                return
            self._collect_shortcode_dates(data, shortcode_dates)

        self.page.on("response", _handle_response)
        try:
            if not await self._navigate_to_profile():
                print("  ! Could not load profile after login")
                return []

            print(f"  - Capturing posts from last {FIRST_SCAN_DAYS_LIMIT} days only")

            print("  - Phase 1: collecting post shortcodes + dates from grid...")
            all_shortcodes = await self._scroll_grid_for_shortcodes(
                seen_shortcodes, shortcode_dates
            )

            new_shortcodes = [sc for sc in all_shortcodes if sc not in seen_shortcodes]
            print(
                f"  - {len(new_shortcodes)} new posts to date "
                f"(of {len(all_shortcodes)} total, "
                f"{len(shortcode_dates)} dates captured)"
            )

            if not new_shortcodes:
                print("  - No new posts to fetch dates for")
                return []

            # Phase 2: Build post list using captured dates.
            # Only visit a post page individually if the GraphQL capture
            # missed its date (rare fallback).
            posts = []
            for shortcode in new_shortcodes:
                published_at = shortcode_dates.get(shortcode)
                if not published_at:
                    # Fallback: visit the post page directly.
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
        finally:
            try:
                self.page.remove_listener("response", _handle_response)
            except Exception:
                pass

    async def _scroll_grid_for_shortcodes(self, seen_shortcodes, shortcode_dates):
        """Scroll the Instagram grid to collect post shortcodes.

        shortcode_dates is populated by the response listener attached
        in fetch_posts (which captures GraphQL responses). This method
        only reads from it for the progress log.

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

            print(
                f"  - Scroll {scroll_round + 1}: "
                f"{len(all_shortcodes)} shortcodes, "
                f"{len(shortcode_dates)} dates"
            )
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.wait_for_timeout(2000)

        return all_shortcodes

    @staticmethod
    def _collect_shortcode_dates(data, out):
        """Walk a GraphQL JSON blob and map shortcode -> ISO date.

        The profile posts query returns media nodes containing an
        identifier and a timestamp. Instagram renamed 'shortcode' to
        'code' in recent schema updates, so we check both. The
        timestamp is 'taken_at_timestamp' or 'taken_at' (Unix epoch).
        """
        if isinstance(data, dict):
            sc = data.get("shortcode") or data.get("code")
            ts = data.get("taken_at_timestamp") or data.get("taken_at")
            if sc and isinstance(ts, int) and sc not in out:
                out[sc] = _epoch_to_iso(ts)
            for v in data.values():
                InstagramMonitor._collect_shortcode_dates(v, out)
        elif isinstance(data, list):
            for v in data:
                InstagramMonitor._collect_shortcode_dates(v, out)

    @staticmethod
    def _extract_shortcode(href):
        """Extract the shortcode from an Instagram post or reel URL."""
        # Match /p/SHORTCODE/ or /reel/SHORTCODE/
        m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
        return m.group(1) if m else None

    async def _get_post_date(self, shortcode):
        """Extract the publish datetime from a post or reel page.

        /p/ and /reel/ URLs are interchangeable, so we always use /p/.

        Instagram renders a <time> element showing relative text ("2H",
        "3D") with the full date in its title attribute (the hover
        tooltip) and/or its datetime attribute. We wait for the <time>
        element to render, then read every attribute it carries.

        Falls back to capturing the GraphQL/JSON XHR response (which
        contains taken_at_timestamp as a Unix epoch).

        Returns None if no date is found (caller keeps collecting).
        """
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        captured = {"ts": None}

        async def _handle_response(response):
            try:
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype:
                    return
                if "instagram.com" not in response.url:
                    return
                data = await response.json()
            except Exception:
                return
            ts = _extract_taken_at(data)
            if ts:
                captured["ts"] = ts

        self.page.on("response", _handle_response)
        try:
            try:
                await self.page.goto(
                    post_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT_MS,
                )
            except Exception as exc:
                print(f"    ! nav error for {shortcode}: {exc}")

            # Wait for React to render the <time> element. This is the
            # element that shows "2H" / "3D" and carries the date in its
            # title (hover tooltip) and/or datetime attribute.
            try:
                await self.page.wait_for_selector("time", timeout=POST_DATE_WAIT_MS)
            except Exception:
                pass

            # Read every attribute on every <time> element. The publish
            # date (not a comment timestamp) is the FIRST <time> in the
            # DOM, but we check all of them to be safe.
            times = await self.page.query_selector_all("time")
            for t in times:
                dt = await t.get_attribute("datetime")
                if dt:
                    return dt
                title = await t.get_attribute("title")
                if title:
                    return title

            # Fallback: GraphQL/JSON XHR response captured by the listener.
            if captured["ts"]:
                return _epoch_to_iso(captured["ts"])

            # Fallback: epoch embedded in page HTML/JSON.
            html = await self.page.content()
            m = re.search(r'"taken_at_timestamp"\s*:\s*(\d{10,})', html)
            if m:
                return _epoch_to_iso(int(m.group(1)))
            m = re.search(r'"taken_at"\s*:\s*(\d{10,})', html)
            if m:
                return _epoch_to_iso(int(m.group(1)))

            # Fallback: datePublished in JSON-LD.
            m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
            if m:
                return m.group(1)
        except Exception as exc:
            print(f"    ! date error for {shortcode}: {exc}")
        finally:
            try:
                self.page.remove_listener("response", _handle_response)
            except Exception:
                pass
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
