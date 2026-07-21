# monitors/facebook.py
"""Facebook page monitor using Playwright with saved session + manual login.

Loads the saved session (fb_state.json) first. If still valid,
no login is needed. If expired, requires manual login via a visible
browser window. Login completion is auto-detected.

- Follower count: Parse from page after login
- Post list: Scrape timeline after login
- Post dates: Extract from aria-label timestamp links
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import scan_state
from monitors.base import BaseMonitor, parse_count
from config import (
    DELAY_BETWEEN_POSTS_SEC,
    MAX_POSTS_TO_DATE,
    PAGE_TIMEOUT_MS,
    FB_SESSION_FILE,
    FB_SCROLL_ROUNDS,
    FB_STALE_SCROLLS,
    FB_SCROLL_DELAY_MS,
    LOCAL_TIMEZONE_OFFSET,
    LOGIN_TIMEOUT_SEC,
)

FOLLOWERS_RE = re.compile(r"([\d,.]+[KMBkmb]?)\s*[Ff]ollowers")
LIKES_RE = re.compile(r"([\d,.]+[KMBkmb]?)\s*[Ll]ikes")
STORY_FBID_RE = re.compile(r"story_fbid=(pfbid\w+|\d+)")
POST_ID_RE = re.compile(r"/posts/(\d+)")
LOGIN_URL = "https://www.facebook.com/login.php"


class FacebookMonitor(BaseMonitor):
    platform = "facebook"
    metric_label = "followers"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._page_url = profile["url"].split("?")[0].rstrip("/")
        self._logged_in = False

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _save_session(self):
        """Save current browser state to the session file."""
        try:
            state = await self.page.context.storage_state()
            with open(FB_SESSION_FILE, "w") as f:
                json.dump(state, f, indent=2)
            print("  - Session saved for future runs")
        except Exception as exc:
            print(f"  ! Could not save session: {exc}")

    async def _ensure_logged_in(self):
        """Ensure the user is logged in to Facebook.

        1. Check if the session (loaded via context storage_state) is valid.
        2. If valid, proceed without login.
        3. If expired, require manual login (auto-detected).
        """
        if self._logged_in:
            return True

        # Session is loaded via context storage_state in main.py
        if await self._is_session_valid():
            print("  - Facebook session still valid, skipping login")
            self._logged_in = True
            return True

        # Session is not valid — require fresh manual login
        print("  - Facebook login required. Please log in in the browser window.")
        print(
            f"  - Waiting up to {LOGIN_TIMEOUT_SEC // 60} minutes for you to log in..."
        )

        if await self._do_manual_login():
            self._logged_in = True
            return True

        return False

    async def _is_session_valid(self):
        """Check if the current session is authenticated.

        Navigates to the target page directly. If redirected to login,
        the session is invalid.
        """
        try:
            await self.page.goto(
                self._page_url,
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
        """Verify login by checking for authenticated-only elements
        on the current page WITHOUT navigating away.

        Used during the login poll so we never disrupt the user
        mid-captcha, mid-2FA, or mid-checkpoint.
        """
        try:
            current_url = self.page.url
            if (
                "login" in current_url
                or "checkpoint" in current_url
                or "two_step" in current_url
            ):
                return False
            # Logged-in Facebook pages have a navigation bar with role="navigation"
            nav = await self.page.query_selector(
                '[role="navigation"], div[role="banner"]'
            )
            if nav:
                return True
            # Check for the user's account menu / profile link
            account = await self.page.query_selector(
                'a[href*="/me/"], a[aria-label*="Account"], a[aria-label*="account"]'
            )
            if account:
                return True
            # Check for "Create" / compose bar (only when logged in)
            compose = await self.page.query_selector(
                'a[aria-label*="Create"], a[href*="/stories/create"]'
            )
            if compose:
                return True
            return False
        except Exception:
            return False

    async def _do_manual_login(self):
        """Open the login page and wait for the user to log in.

        Login completion is auto-detected by checking for authenticated
        elements. We never navigate away during the poll so the user
        can complete captcha, 2FA, and checkpoints without interruption.
        """
        try:
            await self.page.goto(
                LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
            print("  - Opened Facebook login page. Please log in manually.")
        except Exception as exc:
            print(f"  ! Could not open Facebook login page: {exc}")
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
                print("  - Facebook login detected. Saving fresh session...")
                await self._save_session()
                await self.page.wait_for_timeout(2000)
                return True
            # Not logged in yet — keep waiting.

        print("  ! Facebook login timed out. Skipping Facebook profiles.")
        return False

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    async def fetch_stats(self):
        """Get follower count from the Facebook page.

        Requires login. Navigates to the page and extracts
        the follower count from the page header.
        """
        logged_in = await self._ensure_logged_in()
        if not logged_in:
            self.followers = 0
            return self.followers

        try:
            await self.page.goto(
                self._page_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(4000)

            if "login" in self.page.url:
                print("  ! Redirected to login when loading page")
                self.followers = 0
                return self.followers

            # Check for rate-limit / block page
            body_text = await self.page.inner_text("body")
            if (
                "temporarily blocked" in body_text.lower()
                or "restricted" in body_text.lower()
            ):
                print("  ! Facebook rate limit or block detected")
                self.followers = 0
                return self.followers

            m = FOLLOWERS_RE.search(body_text)
            if m:
                self.followers = parse_count(m.group(1))
                print(f"  - Followers: {self.followers:,}")
                return self.followers

            m = LIKES_RE.search(body_text)
            if m:
                self.followers = parse_count(m.group(1))
                print(f"  - Followers (from likes): {self.followers:,}")
                return self.followers

            html = await self.page.content()
            desc = re.search(r'<meta\s+name="description"\s+content="([^"]*)"', html)
            if desc:
                m = FOLLOWERS_RE.search(desc.group(1))
                if m:
                    self.followers = parse_count(m.group(1))
                    print(f"  - Followers: {self.followers:,}")
                    return self.followers

            print("  ! facebook: followers text not found")
            self.followers = 0
        except Exception as exc:
            print(f"  ! facebook stats error: {exc}")
            self.followers = 0
        return self.followers

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch posts with publish dates from the Facebook timeline."""
        if seen_shortcodes is None:
            seen_shortcodes = set()

        logged_in = await self._ensure_logged_in()
        if not logged_in:
            return []

        try:
            await self.page.goto(
                self._page_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"  ! facebook: could not load page: {exc}")
            return []

        if "login" in self.page.url:
            print("  ! Redirected to login when loading timeline")
            return []

        # Check for rate-limit / block page
        body_text = await self.page.inner_text("body")
        if (
            "temporarily blocked" in body_text.lower()
            or "restricted" in body_text.lower()
        ):
            print("  ! Facebook rate limit or block detected")
            return []

        posts = await self._scroll_timeline(seen_shortcodes)
        print(f"  - Total posts collected: {len(posts)}")

        # Apply MAX_POSTS_TO_DATE cap if set
        limit = MAX_POSTS_TO_DATE if MAX_POSTS_TO_DATE > 0 else len(posts)
        if MAX_POSTS_TO_DATE > 0 and len(posts) > limit:
            posts = posts[:limit]
            print(f"  - Capped at {limit} posts")

        return posts

    async def _scroll_timeline(self, seen_shortcodes):
        """Scroll the Facebook timeline to collect posts with dates.

        Facebook's DOM uses aria-label attributes on timestamp links
        instead of data-utime. The format is like:
        aria-label="Monday, June 22, 2026 at 5:37 AM"
        Post URLs use /reel/{id}/, /posts/{id}/, or /photo/?fbid={id}
        """
        all_posts = []
        stopped_early = False
        stale_scrolls = 0
        for scroll_round in range(FB_SCROLL_ROUNDS):
            posts_on_page = await self.page.evaluate("""
                () => {
                    const results = [];
                    const links = document.querySelectorAll('a[aria-label]');
                    const dateRegex = /(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\\s*(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d+,?\\s+\\d{4}/i;
                    
                    for (const link of links) {
                        const ariaLabel = link.getAttribute('aria-label') || '';
                        if (!dateRegex.test(ariaLabel)) continue;
                        
                        const dateStr = ariaLabel.match(dateRegex)[0];
                        const text = link.innerText || '';
                        
                        let container = link.closest('[role="article"], [data-pagelet], div[class*="feed"], div[class*="post"]');
                        if (!container) container = link.parentElement;
                        if (!container) continue;
                        
                        let postLink = container.querySelector('a[href*="/reel/"], a[href*="/posts/"], a[href*="/photo/?fbid"], a[href*="story_fbid"]');
                        if (!postLink) {
                            const href = link.href || '';
                            if (href.includes('/reel/') || href.includes('/posts/') || href.includes('fbid=')) {
                                postLink = link;
                            }
                        }
                        if (!postLink) continue;
                        
                        const href = postLink.href;
                        
                        let postId = null;
                        let reelMatch = href.match(/\\/reel\\/(\\d+)/);
                        let postMatch = href.match(/\\/posts\\/(\\d+)/);
                        let fbidMatch = href.match(/fbid=(\\d+)/);
                        let storyMatch = href.match(/story_fbid=(pfbid\\w+|\\d+)/);
                        
                        if (reelMatch) postId = reelMatch[1];
                        else if (postMatch) postId = postMatch[1];
                        else if (fbidMatch) postId = fbidMatch[1];
                        else if (storyMatch) postId = storyMatch[1];
                        
                        if (!postId) continue;
                        
                        if (results.some(r => r.postId === postId)) continue;
                        
                        results.push({
                            postId: postId,
                            href: href.split('?')[0],
                            dateStr: dateStr,
                            relativeTime: text,
                        });
                    }
                    
                    return results;
                }
            """)
            new_posts = []
            for p in posts_on_page:
                post_id = p.get("postId")
                if not post_id:
                    continue
                if any(existing["post_id"] == post_id for existing in all_posts):
                    continue
                published_at = self._parse_fb_date(p.get("dateStr", ""))
                new_posts.append(
                    {
                        "post_id": post_id,
                        "url": p.get("href", self._page_url),
                        "published_at": published_at,
                    }
                )
            if new_posts:
                stale_scrolls = 0
                for post in new_posts:
                    all_posts.append(post)
                    if post["post_id"] in seen_shortcodes:
                        print(
                            f"  - Stopped early: hit already-seen "
                            f"post ({post['post_id']}) at position {len(all_posts)}"
                        )
                        stopped_early = True
                        break
            else:
                stale_scrolls += 1
            if stopped_early:
                break
            if stale_scrolls >= FB_STALE_SCROLLS:
                print(
                    "  - Timeline exhausted (no new posts after "
                    f"{FB_STALE_SCROLLS} scrolls)"
                )
                break
            print(f"  - Scroll {scroll_round + 1}: {len(all_posts)} posts total")
            await self.page.evaluate("""
                () => {
                    window.scrollTo({
                        top: document.body.scrollHeight,
                        behavior: 'smooth'
                    });
                }
            """)
            await self.page.wait_for_timeout(FB_SCROLL_DELAY_MS)

        result = []
        dated_count = 0
        for post in all_posts:
            if post["published_at"]:
                dated_count += 1
            result.append(
                self.make_post(
                    post["post_id"],
                    "",
                    post["url"],
                    post["published_at"],
                )
            )
        print(f"  - Dated: {dated_count}/{len(result)}")
        return result

    @staticmethod
    def _parse_fb_date(date_str):
        """Parse Facebook date string to ISO 8601.

        Handles formats like:
        'Monday, June 22, 2026 at 5:37 AM'

        Facebook timestamps are in the viewer's local timezone (UTC+8).
        We subtract the offset to store as UTC, which is then reversed
        by the date filter's +offset conversion for comparison.
        """
        try:
            cleaned = re.sub(
                r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\s*",
                "",
                date_str,
            )
            cleaned = cleaned.replace(" at ", " ")
            for fmt in [
                "%B %d, %Y %I:%M %p",
                "%B %d, %Y %H:%M",
                "%B %d, %Y",
            ]:
                try:
                    dt = datetime.strptime(cleaned.strip(), fmt)
                    # Facebook shows time in viewer's local timezone
                    # Store as UTC by subtracting the local offset
                    dt = dt - timedelta(hours=LOCAL_TIMEZONE_OFFSET)
                    return dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
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
