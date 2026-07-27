"""Threads profile monitor using Playwright with saved session + manual login.

Loads the saved session (threads_state.json) first. If still valid,
no login is needed. If expired, requires manual login via a visible
browser window. Login is done directly on Threads — does NOT use
the Instagram ("Continue with Instagram") flow.

Scanning logic (7-Day Scan Rule):
- Every scan stops as soon as a post older than 7 days is found.
- Every scan also stops when hitting an already-seen post (incremental).
- Whichever condition triggers first wins.
- The post that triggers a stop is NOT collected.
- This applies to every scan, not just the first.

Data source: /api/graphql XHR responses (captured on load and on scroll),
which carry thread_items with exact post.taken_at timestamps and post.code
shortcodes. Quoted/reposted posts from other users are filtered out.
Embedded data-sjs JSON and DOM extraction are fallbacks.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from monitors.base import BaseMonitor, parse_count, is_older_than_days
from config import (
    THREADS_STATE_FILE,
    PAGE_TIMEOUT_MS,
    THREADS_SCROLL_ROUNDS,
    THREADS_STALE_SCROLLS,
    THREADS_SCROLL_DELAY_MS,
    FIRST_SCAN_DAYS_LIMIT,
)

LOGIN_TIMEOUT_SEC = 300

# Keys that contain OTHER users' posts (quotes/reposts of external content).
# Only skip these — do NOT skip reply/thread structures, which contain the
# profile owner's own posts.
_SKIP_KEYS = {
    "quoted_post",
    "reposted_post",
}


def nested_lookup(key, data):
    """Recursively search for all values matching a key in nested dicts/lists."""
    results = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == key:
                if isinstance(v, list):
                    results.extend(v)
                else:
                    results.append(v)
            if isinstance(v, (dict, list)):
                results.extend(nested_lookup(key, v))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                results.extend(nested_lookup(key, item))
    return results


class ThreadsMonitor(BaseMonitor):
    platform = "threads"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._username = self._extract_username(profile["url"])
        self._profile_url = f"https://www.threads.net/@{self._username}"
        self._captured_posts = []

    @staticmethod
    def _extract_username(url):
        """Extract username from a Threads URL."""
        url = url.split("?")[0].rstrip("/")
        return url.split("@")[-1]

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _load_saved_session(self):
        """Load cookies from the saved session file into the browser context."""
        state_path = Path(THREADS_STATE_FILE)
        if not state_path.exists():
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            cookies = state.get("cookies", [])
            if cookies:
                await self.page.context.add_cookies(cookies)
                print("  - Loaded saved Threads session")
        except Exception as exc:
            print(f"  ! Could not load saved session: {exc}")

    async def _check_login_wall(self):
        """Check if the Threads login/signup modal is present."""
        try:
            return await self.page.evaluate("""
                () => {
                    let modal = document.querySelector('div[role="dialog"]');
                    if (modal) {
                        let text = modal.innerText || '';
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
        except Exception:
            pass

    async def _ensure_logged_in(self):
        """Ensure the user is logged in to Threads."""
        if self._logged_in:
            return True

        await self._load_saved_session()

        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"  ! threads: could not load profile page: {exc}")
            return False

        login_wall = await self._check_login_wall()
        if not login_wall:
            print("  - No login required (session valid)")
            self._logged_in = True
            return True

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
        """Open the Threads login page and wait for the user to log in."""
        try:
            await self.page.goto(
                "https://www.threads.net/login",
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
            print("  - Opened Threads login page. Please log in manually.")
        except Exception as exc:
            print(f"  ! Threads login navigation error: {exc}")
            return False

        start = time.time()
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            current_url = self.page.url
            if "login" in current_url:
                await self.page.wait_for_timeout(2000)
                continue
            try:
                await self.page.goto(
                    self._profile_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT_MS,
                )
                await self.page.wait_for_timeout(3000)
            except Exception:
                await self.page.wait_for_timeout(2000)
                continue
            login_wall = await self._check_login_wall()
            if not login_wall:
                print("  - Threads login detected. Saving fresh session...")
                await self._save_session()
                if await self._check_login_wall():
                    await self._dismiss_login_wall()
                return True
            await self.page.wait_for_timeout(2000)

        print("  ! Threads login timed out. Skipping Threads profiles.")
        return False

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    async def _load_profile(self):
        """Load the profile page."""
        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"  ! threads: could not load profile page: {exc}")
            return False
        return True

    async def fetch_stats(self):
        """Get follower count from the Threads profile page."""
        logged_in = await self._ensure_logged_in()
        if not logged_in:
            self.followers = 0
            return self.followers

        if not await self._load_profile():
            self.followers = 0
            return self.followers

        try:
            html = await self.page.content()
            follower_matches = nested_lookup("follower_count", html)
            if not follower_matches:
                scripts = re.findall(
                    r"<script[^>]*data-sjs[^>]*>(.*?)</script>",
                    html,
                    re.DOTALL,
                )
                for script in scripts:
                    try:
                        data = json.loads(script)
                        follower_matches.extend(nested_lookup("follower_count", data))
                    except json.JSONDecodeError:
                        continue

            if follower_matches:
                self.followers = parse_count(follower_matches[0])
                print(f"  - Followers: {self.followers:,}")
                return self.followers

            m = re.search(r"([\d,.]+[KMBkmb]?)\s*[Ff]ollowers", html)
            if m:
                self.followers = parse_count(m.group(1))
                print(f"  - Followers: {self.followers:,}")
                return self.followers

            print("  ! threads: followers not found")
            self.followers = 0
        except Exception as exc:
            print(f"  ! threads stats error: {exc}")
            self.followers = 0
        return self.followers

    async def _scroll_page(self):
        """Scroll down to trigger lazy-loaded posts."""
        try:
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception as exc:
            print(f"  ! threads: scroll error: {exc}")

    # ------------------------------------------------------------------
    # JSON / XHR-based extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _taken_at_to_iso(taken_at):
        """Convert a Unix timestamp (seconds) to an ISO 8601 string.

        Returns None if the value is missing or unparseable, so the post
        is kept rather than discarded (per the missing-date contract).
        """
        if taken_at is None:
            return None
        try:
            return datetime.fromtimestamp(int(taken_at), tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError):
            return None

    @staticmethod
    def _is_post_older_than_days(published_at, days):
        """Check if a post is older than N days using day-granularity.

        Compares calendar dates (not exact time) so a post from July 18
        is NOT considered older than 7 days when checked on July 25
        (7 calendar days, not >7). Falls back to is_older_than_days for
        unparseable dates (returns False — post is kept).
        """
        if not published_at:
            return False
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now.date() - dt.date()).days > days
        except (ValueError, TypeError):
            return False

    def _extract_posts_from_payload(self, data):
        """Extract post dicts from any JSON payload.

        Recursively walks the tree looking for dict nodes that contain both
        `code` (post shortcode) and `taken_at` (Unix timestamp) keys.

        Only skips `quoted_post` and `reposted_post` keys — these contain
        OTHER users' posts (with their own older timestamps) that would
        cause false 7-day stops. Does NOT skip reply/thread structures,
        which contain the profile owner's own posts and replies.
        """
        results = []
        seen_codes = set()

        def _walk(node):
            if isinstance(node, dict):
                code = node.get("code")
                taken_at = node.get("taken_at")
                if code and taken_at is not None and code not in seen_codes:
                    seen_codes.add(code)
                    text = ""
                    cap = node.get("caption")
                    if isinstance(cap, dict):
                        text = cap.get("text", "") or ""
                    permalink = node.get("permalink") or (
                        f"https://www.threads.net/@{self._username}/post/{code}"
                    )
                    results.append(
                        {
                            "post_id": code,
                            "taken_at": taken_at,
                            "text": text,
                            "url": permalink,
                        }
                    )
                for k, v in node.items():
                    if k in _SKIP_KEYS:
                        continue
                    if isinstance(v, (dict, list)):
                        _walk(v)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        _walk(item)

        _walk(data)
        return results

    def _normalize_json_posts(self, raw_posts):
        """Convert raw JSON post dicts to the internal format, newest-first.

        Undated posts (None published_at) sort to the FRONT of the list so
        they are processed before any 7-day stop, per the missing-date
        contract (undated posts are kept, not discarded).
        """
        raw_posts.sort(key=lambda p: p.get("taken_at") or 0, reverse=True)
        normalized = []
        for p in raw_posts:
            normalized.append(
                {
                    "post_id": p["post_id"],
                    "published_at": self._taken_at_to_iso(p.get("taken_at")),
                    "url": p.get("url", self._profile_url),
                    "title": p.get("text", ""),
                }
            )
        return normalized

    async def _extract_posts_from_initial_html(self):
        """Parse embedded data-sjs JSON for posts with exact timestamps."""
        try:
            html = await self.page.content()
        except Exception as exc:
            print(f"  ! threads: could not read HTML: {exc}")
            return []
        raw = []
        scripts = re.findall(
            r"<script[^>]*data-sjs[^>]*>(.*?)</script>",
            html,
            re.DOTALL,
        )
        for script in scripts:
            try:
                data = json.loads(script)
            except json.JSONDecodeError:
                continue
            raw.extend(self._extract_posts_from_payload(data))
        return self._normalize_json_posts(raw)

    async def _on_graphql_response(self, response):
        """Capture any XHR response that contains Threads post data."""
        try:
            ctype = response.headers.get("content-type", "")
            if "json" not in ctype:
                return
            body = await response.json()
            posts = self._extract_posts_from_payload(body)
            if posts:
                print(f"  - [xhr] captured {len(posts)} posts from {response.url[:80]}")
                self._captured_posts.extend(posts)
        except Exception:
            pass

    def _drain_captured(self):
        """Return and clear newly captured XHR posts, normalized + newest-first."""
        if not self._captured_posts:
            return []
        batch = self._captured_posts
        self._captured_posts = []
        return self._normalize_json_posts(batch)

    # ------------------------------------------------------------------
    # DOM-based extraction (fallback)
    # ------------------------------------------------------------------

    async def _extract_posts_from_dom(self):
        """Extract posts from rendered DOM elements (fallback when no JSON)."""
        try:
            posts_data = await self.page.evaluate("""
                () => {
                    const results = [];
                    const links = document.querySelectorAll('a[href*="/post/"]');
                    for (const link of links) {
                        const href = link.href || '';
                        const match = href.match(/\\/post\\/([A-Za-z0-9_-]+)/);
                        if (!match) continue;
                        const postId = match[1];
                        if (results.some(r => r.postId === postId)) continue;

                        let timeEl = link.querySelector('time[datetime]') ||
                                     link.parentElement?.querySelector('time[datetime]');
                        let publishedAt = timeEl
                            ? timeEl.getAttribute('datetime')
                            : null;

                        results.push({
                            postId: postId,
                            href: href.split('?')[0],
                            publishedAt: publishedAt,
                            title: '',
                        });
                    }
                    return results;
                }
            """)
            posts = []
            for p in posts_data:
                post_id = p.get("postId")
                if not post_id:
                    continue
                posts.append(
                    {
                        "post_id": post_id,
                        "url": p.get("href", self._profile_url),
                        "published_at": p.get("publishedAt"),
                        "title": "",
                    }
                )
            return posts
        except Exception as exc:
            print(f"  ! threads DOM extraction error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Post collection + stop rules
    # ------------------------------------------------------------------

    def _drain_posts(self, batch, all_posts, seen_codes, seen_shortcodes):
        """Apply stop rules to a batch of posts.

        Returns True if a stop condition triggered. The triggering post is
        NOT collected. Checks incremental (already-seen) before 7-day cutoff.

        Uses day-granularity comparison for the 7-day check so a post from
        July 18 is NOT flagged as older than 7 days on July 25.
        """
        for post in batch:
            if post["post_id"] in seen_codes:
                continue
            seen_codes.add(post["post_id"])

            # Incremental stop (already in DB) — checked first so we never
            # re-collect a post we already have.
            if post["post_id"] in seen_shortcodes:
                print(
                    f"  - Stopped early: hit already-seen "
                    f"post ({post['post_id']}) at position "
                    f"{len(all_posts)}"
                )
                return True

            # 7-day cutoff — day-granularity comparison.
            # Returns False for missing/unparseable dates, so such posts are
            # kept rather than discarded.
            if self._is_post_older_than_days(
                post.get("published_at"), FIRST_SCAN_DAYS_LIMIT
            ):
                print(
                    f"  - Stopped: post {post['post_id']} is older "
                    f"than {FIRST_SCAN_DAYS_LIMIT} days"
                )
                return True

            all_posts.append(post)
        return False

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch posts with publish dates from the Threads profile.

        Every scan stops when a post older than 7 days is found, or when
        hitting an already-seen post (incremental stop). Whichever triggers
        first wins. The triggering post is NOT collected. Applies to every
        scan, not just the first.

        Data sources (combined, newest-first):
        1. /api/graphql XHR — posts loaded on load and on scroll (exact taken_at)
        2. Embedded data-sjs JSON — initial posts (exact taken_at)
        3. DOM extraction — fallback when no JSON is available

        The page is reloaded with the XHR listener already registered so the
        first-page GraphQL response (containing the newest posts) is captured.
        Quoted/reposted posts from other users are filtered out so only the
        profile owner's own posts are collected.
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()

        logged_in = await self._ensure_logged_in()
        if not logged_in:
            print("  - Attempting to load posts without login (limited results)")

        print(f"  - Capturing posts from last {FIRST_SCAN_DAYS_LIMIT} days only")

        all_posts = []
        seen_codes = set()  # dedup within this scan
        stopped_early = False
        stale_scrolls = 0

        # Register the XHR listener BEFORE reloading the profile so the
        # first-page GraphQL response (containing the newest posts) is
        # captured.
        self._captured_posts = []
        self.page.on("response", self._on_graphql_response)

        try:
            try:
                await self.page.goto(
                    self._profile_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT_MS,
                )
                await self.page.wait_for_timeout(5000)
            except Exception as exc:
                print(f"  ! threads: could not reload profile for posts: {exc}")

            # Extract from all available sources, combining unique posts.
            xhr_initial = self._drain_captured()
            embedded = await self._extract_posts_from_initial_html()
            dom_posts = await self._extract_posts_from_dom()

            # Merge all sources — dedup happens in _drain_posts via seen_codes.
            # JSON sources (XHR + embedded) have exact taken_at timestamps;
            # DOM posts have datetime attributes. JSON sources take priority
            # for any post that appears in multiple sources.
            merged_by_id = {}
            for post in xhr_initial + embedded:
                merged_by_id[post["post_id"]] = post
            for post in dom_posts:
                if post["post_id"] not in merged_by_id:
                    merged_by_id[post["post_id"]] = post

            # Sort newest-first by published_at. Undated posts (None) sort
            # to the FRONT so they are processed before any 7-day stop.
            initial_batch = sorted(
                merged_by_id.values(),
                key=lambda p: p.get("published_at") or "9999",
                reverse=True,
            )

            if initial_batch:
                print(
                    f"  - Source: combined JSON+DOM ({len(initial_batch)} initial posts)"
                )
            else:
                print("  - Source: DOM (no posts captured)")

            stopped_early = self._drain_posts(
                initial_batch, all_posts, seen_codes, seen_shortcodes
            )

            for scroll_round in range(THREADS_SCROLL_ROUNDS):
                if stopped_early:
                    break
                if stale_scrolls >= THREADS_STALE_SCROLLS:
                    print(
                        "  - Timeline exhausted (no new posts after "
                        f"{THREADS_STALE_SCROLLS} scrolls)"
                    )
                    break

                await self._scroll_page()
                await self.page.wait_for_timeout(THREADS_SCROLL_DELAY_MS)

                batch = self._drain_captured()
                if not batch:
                    batch = await self._extract_posts_from_dom()

                if batch:
                    stale_scrolls = 0
                    stopped_early = self._drain_posts(
                        batch, all_posts, seen_codes, seen_shortcodes
                    )
                else:
                    stale_scrolls += 1

                print(f"  - Scroll {scroll_round + 1}: {len(all_posts)} posts total")
        finally:
            try:
                self.page.remove_listener("response", self._on_graphql_response)
            except Exception:
                pass

        result = []
        for post in all_posts:
            if post["published_at"]:
                result.append(
                    self.make_post(
                        post["post_id"],
                        post.get("title", ""),
                        post["url"],
                        post["published_at"],
                    )
                )
        print(f"  - Total posts collected: {len(result)}")
        return result
