# monitors/threads.py
"""Threads profile monitor using Playwright with saved session + manual login.

Loads the saved session (threads_state.json) first. If still valid,
no login is needed. If expired, requires manual login via a visible
browser window. Login is done directly on Threads — does NOT use
the Instagram ("Continue with Instagram") flow.
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
    PAGE_TIMEOUT_MS,
    THREADS_STATE_FILE,
    THREADS_SCROLL_ROUNDS,
    THREADS_STALE_SCROLLS,
    THREADS_SCROLL_DELAY_MS,
    LOGIN_TIMEOUT_SEC,
)

SJS_SCRIPT_RE = re.compile(
    r"<script[^>]*data-sjs[^>]*>(.*?)</script>",
    re.DOTALL,
)
FOLLOWER_COUNT_RE = re.compile(r'"follower_count"\s*:\s*(\d+)')


def nested_lookup(key, data):
    """Recursively search for a key in nested dict/list."""
    results = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == key:
                results.append(v)
            results.extend(nested_lookup(key, v))
    elif isinstance(data, list):
        for item in data:
            results.extend(nested_lookup(key, item))
    return results


class ThreadsMonitor(BaseMonitor):
    platform = "threads"
    metric_label = "followers"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._username = self._extract_username(profile["url"])
        self._profile_url = f"https://www.threads.net/@{self._username}"
        self._logged_in = False

    @staticmethod
    def _extract_username(url):
        """Extract username from a Threads URL."""
        url = url.split("?")[0].rstrip("/")
        return url.split("@")[-1]

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _check_login_wall(self):
        """Check if the Threads login/signup modal is present."""
        try:
            return await self.page.evaluate("""
                () => {
                    let modal = document.querySelector('div[role="dialog"]');
                    if (modal) {
                        let text = modal.textContent || '';
                        if (text.includes('Continue with Instagram') ||
                            text.includes('Join Threads')) {
                            return true;
                        }
                    }
                    return false;
                }
            """)
        except Exception:
            return False

    async def _dismiss_login_wall(self):
        """Try to dismiss the login wall if it appears."""
        try:
            close_btn = self.page.locator(
                'div[role="dialog"] button:has-text("Not now"), '
                'div[role="dialog"] [aria-label="Close"], '
                'div[role="dialog"] svg[aria-label*="Close"]'
            )
            if await close_btn.count() > 0:
                await close_btn.first.click()
                await self.page.wait_for_timeout(2000)
                return True
        except Exception:
            pass
        return False

    async def _ensure_logged_in(self):
        """Ensure the user is logged in to Threads.

        1. Navigate to the profile and check for login wall.
        2. If no login wall, session is valid — proceed.
        3. If login wall present, require manual login.
        """
        if self._logged_in:
            return True

        # Navigate to profile to check if login is needed
        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
        except Exception:
            pass

        login_wall = await self._check_login_wall()
        if not login_wall:
            print("  - No login required (session valid)")
            self._logged_in = True
            return True

        # Need fresh manual login — directly on Threads, NOT via Instagram
        print("  - Threads login required. Please log in in the browser window.")
        print(
            f"  - Waiting up to {LOGIN_TIMEOUT_SEC // 60} minutes for you to log in..."
        )
        success = await self._perform_manual_login()
        if success:
            self._logged_in = True
            return True
        return False

    async def _save_session(self):
        """Save current browser state to the session file."""
        try:
            state = await self.page.context.storage_state()
            with open(THREADS_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            print("  - Session saved")
        except Exception as exc:
            print(f"  ! Could not save session: {exc}")

    async def _perform_manual_login(self):
        """Open the Threads login page and wait for the user to log in.

        Does NOT click 'Continue with Instagram'. The user handles the
        entire login flow manually in the visible browser. Login
        completion is auto-detected by polling for the profile page
        loading without a login wall.
        """
        try:
            # Navigate directly to the Threads login page
            await self.page.goto(
                "https://www.threads.net/login",
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
            print("  - Opened Threads login page. Please log in manually.")

            # Poll until login is complete.
            # We detect login by checking if we can navigate to the
            # profile without hitting the login wall.
            start = time.time()
            poll_ms = 3000
            while time.time() - start < LOGIN_TIMEOUT_SEC:
                if scan_state.is_cancelled():
                    print("  ! Login cancelled by user")
                    return False
                await self.page.wait_for_timeout(poll_ms)

                # Check if the user has navigated away from the login page
                current_url = self.page.url
                if "login" in current_url:
                    continue

                # Try navigating to the profile to verify login
                try:
                    await self.page.goto(
                        self._profile_url,
                        wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT_MS,
                    )
                    await self.page.wait_for_timeout(3000)
                except Exception:
                    continue

                login_wall = await self._check_login_wall()
                if not login_wall:
                    print("  - Threads login detected. Saving fresh session...")
                    await self._save_session()
                    return True

            print("  ! Threads login timed out. Skipping Threads profiles.")
            return False

        except Exception as exc:
            print(f"  ! Threads login error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    async def _get_rendered_html(self):
        """Navigate to the profile page and return rendered HTML."""
        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
            return await self.page.content()
        except Exception as exc:
            print(f"  ! threads: could not load profile page: {exc}")
            return ""

    def _parse_sjs_scripts(self, html):
        """Parse all data-sjs scripts from HTML."""
        scripts = SJS_SCRIPT_RE.findall(html)
        parsed = []
        for script in scripts:
            try:
                parsed.append(json.loads(script))
            except json.JSONDecodeError:
                pass
        return parsed

    async def fetch_stats(self):
        """Get follower count from the Threads profile page."""
        html = await self._get_rendered_html()
        if not html:
            self.followers = 0
            return self.followers

        scripts = self._parse_sjs_scripts(html)

        for data in scripts:
            counts = nested_lookup("follower_count", data)
            if counts:
                self.followers = counts[0]
                print(f"  - Followers: {self.followers:,}")
                return self.followers

        m = FOLLOWER_COUNT_RE.search(html)
        if m:
            self.followers = int(m.group(1))
            print(f"  - Followers: {self.followers:,}")
            return self.followers

        print("  ! threads: followers not found")
        self.followers = 0
        return self.followers

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch posts with publish dates from the Threads profile."""
        if seen_shortcodes is None:
            seen_shortcodes = set()

        logged_in = await self._ensure_logged_in()
        if not logged_in:
            print("  - Attempting to load posts without login (limited results)")

        all_posts = []
        stopped_early = False
        stale_scrolls = 0
        seen_codes = set()

        for scroll_round in range(THREADS_SCROLL_ROUNDS):
            posts = await self._extract_posts_from_dom()

            new_posts = []
            for post in posts:
                if post["post_id"] in seen_codes:
                    continue
                seen_codes.add(post["post_id"])
                new_posts.append(post)

            if new_posts:
                stale_scrolls = 0
                for post in new_posts:
                    all_posts.append(post)
                    if post["post_id"] in seen_shortcodes:
                        print(
                            f"  - Stopped early: hit already-seen "
                            f"post ({post['post_id']}) at position "
                            f"{len(all_posts)}"
                        )
                        stopped_early = True
                        break
            else:
                stale_scrolls += 1

            if stopped_early:
                break

            if stale_scrolls >= THREADS_STALE_SCROLLS:
                print(
                    "  - Timeline exhausted (no new posts after "
                    f"{THREADS_STALE_SCROLLS} scrolls)"
                )
                break

            print(f"  - Scroll {scroll_round + 1}: {len(all_posts)} posts total")

            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.keyboard.press("End")
            await self.page.wait_for_timeout(THREADS_SCROLL_DELAY_MS)

        result = []
        dated_count = 0
        for post in all_posts:
            if post["published_at"]:
                dated_count += 1
            result.append(
                self.make_post(
                    post["post_id"],
                    post.get("title", ""),
                    post["url"],
                    post["published_at"],
                )
            )

        print(f"  - Dated: {dated_count}/{len(result)}")
        return result

    async def _extract_posts_from_dom(self):
        """Extract posts from rendered DOM elements."""
        try:
            posts_data = await self.page.evaluate("""
                () => {
                    const results = [];
                    const links = document.querySelectorAll('a[href*="/post/"]');
                    for (const link of links) {
                        const href = link.href;
                        const match = href.match(/\\/post\\/([A-Za-z0-9_-]+)/);
                        if (!match) continue;
                        const code = match[1];
                        
                        let timeEl = link.querySelector('time');
                        if (!timeEl) {
                            let container = link.closest('[role="article"], [data-testid], div[class*="post"]');
                            if (container) {
                                timeEl = container.querySelector('time');
                            }
                        }
                        
                        const datetime = timeEl ? 
                            (timeEl.getAttribute('datetime') || '') : '';
                        
                        results.push({
                            code: code,
                            href: href.split('?')[0],
                            datetime: datetime,
                        });
                    }
                    return results;
                }
            """)

            posts = []
            for p in posts_data:
                code = p.get("code")
                if not code:
                    continue

                published_at = None
                dt_str = p.get("datetime", "")
                if dt_str:
                    try:
                        cleaned = dt_str.rstrip("Z").split(".")[0]
                        dt = datetime.fromisoformat(cleaned)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        published_at = dt.isoformat()
                    except (ValueError, TypeError):
                        pass

                posts.append(
                    {
                        "post_id": code,
                        "url": p.get("href", ""),
                        "published_at": published_at,
                        "title": "",
                    }
                )

            return posts

        except Exception as exc:
            print(f"  ! threads DOM extraction error: {exc}")
            return []
