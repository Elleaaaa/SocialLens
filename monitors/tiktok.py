# monitors/tiktok.py
"""TikTok profile monitor using Playwright with saved session + manual login.

Loads the saved session (tiktok_state.json) first. If still valid,
no login is needed. If the profile page fails to load or returns a
block/captcha page, requires manual login via a visible browser
window. Login completion is auto-detected.

Scanning logic (applies on EVERY scan, not just the first):
  - Every scan captures only posts from the last FIRST_SCAN_DAYS_LIMIT
    (7) days, then stops.
  - Every scan stops as soon as it hits a post older than 7 days
    (7-day cutoff).
  - Every scan also stops when it hits an already-seen post
    (incremental stop — avoids re-scanning posts already in the DB).
  - Whichever condition triggers first wins; the scan breaks immediately.
  - is_older_than_days() returns False for missing/unparseable dates,
    so a post whose date cannot be determined is kept rather than
    discarded (it will be stopped by the incremental check instead).
  - The post cap (FIRST_SCAN_POST_CAP) is no longer used for stopping;
    the 7-day cutoff handles it on every scan.

Video collection source (in priority order):
  1. Direct call to TikTok's /api/post/item_list/ endpoint using the
     secUid embedded in the profile page's __UNIVERSAL_DATA__. Returns
     id + desc + createTime directly. Same-origin fetch carries the
     session cookies (including msToken). Does NOT depend on the SPA
     firing the XHR, so it is deterministic across runs/accounts.
  2. Passive capture of the /api/post/item_list/ XHR while the profile
     page loads. Used only when the direct call cannot be made.
  3. Fallback: scroll the profile grid to collect video IDs, then visit
     each video page for its createTime. Used only when no item_list
     data is available at all.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from monitors.base import BaseMonitor, is_older_than_days
from config import (
    DELAY_BETWEEN_POSTS_SEC,
    PAGE_TIMEOUT_MS,
    TIKTOK_STATE_FILE,
    FIRST_SCAN_DAYS_LIMIT,
)

UNIVERSAL_DATA_RE = re.compile(
    r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
VIDEO_ID_RE = re.compile(r"/video/(\d+)")
CREATE_TIME_RE = re.compile(r'"createTime"\s*:\s*"?(\d{8,})"?')
ITEM_LIST_URL_FRAGMENT = "/api/post/item_list/"
ITEM_LIST_API = "https://www.tiktok.com/api/post/item_list/"
LOGIN_TIMEOUT_SEC = 300
LOGIN_URL = "https://www.tiktok.com/login/phone-or-email/email"


class TikTokMonitor(BaseMonitor):
    platform = "tiktok"
    metric_label = "followers"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._username = self._extract_username(profile["url"])
        self._profile_url = f"https://www.tiktok.com/@{self._username}"
        self._logged_in = False
        self._login_attempted = False
        self._sec_uid = None

    @staticmethod
    def _extract_username(url):
        """Extract username from a TikTok profile URL."""
        url = url.split("?")[0].rstrip("/")
        return url.split("@")[-1]

    # ------------------------------------------------------------------
    # Session / login management
    # ------------------------------------------------------------------

    async def _load_saved_session(self):
        """Load cookies from the saved session file into the browser context."""
        state_path = Path(TIKTOK_STATE_FILE)
        if not state_path.exists():
            return
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            cookies = state.get("cookies", [])
            if cookies:
                await self.page.context.add_cookies(cookies)
                print("  - Loaded saved TikTok session")
        except Exception as exc:
            print(f"  ! Could not load saved session: {exc}")

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
        Loads the saved session (if any) and checks if it is still
        valid. Does NOT open a login page or wait for manual login —
        if the session is invalid, returns False immediately so the
        scan can proceed against the public profile anyway.
        Caches the result so it only runs ONCE per monitor instance.
        """
        if self._login_attempted:
            return self._logged_in
        self._login_attempted = True
        await self._load_saved_session()
        if await self._is_session_valid():
            print("  - TikTok session still valid, skipping login")
            self._logged_in = True
            return True
        print("  - No valid TikTok session — proceeding without login")
        return False

    async def _is_session_valid(self):
        """Check if the profile page loads successfully with valid data."""
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
        """Verify login by checking for authenticated-only elements."""
        try:
            current_url = self.page.url
            if "login" in current_url:
                return False
            upload = await self.page.query_selector(
                'a[href*="/upload"], [data-e2e="upload-icon"]'
            )
            if upload:
                return True
            avatar = await self.page.query_selector(
                '[data-e2e="profile-icon"], [data-e2e="avatar"]'
            )
            if avatar:
                return True
            inbox = await self.page.query_selector(
                '[data-e2e="inbox-icon"], [data-e2e="message-icon"]'
            )
            if inbox:
                return True
            return False
        except Exception:
            return False

    async def _do_manual_login(self):
        """Open the login page and wait for the user to log in."""
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

        start = time.time()
        poll_ms = 2000
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            await self.page.wait_for_timeout(poll_ms)

            if await self._verify_logged_in_on_page():
                print("  - TikTok login detected. Saving fresh session...")
                await self._save_session()
                await self.page.wait_for_timeout(2000)
                return True

        print("  ! TikTok login timed out. Skipping TikTok profiles.")
        return False

    # ------------------------------------------------------------------
    # Data extraction helpers
    # ------------------------------------------------------------------

    async def _fetch_universal_data(self, url):
        """Navigate to a TikTok page and parse __UNIVERSAL_DATA."""
        try:
            await self.page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS
            )
            await self.page.wait_for_timeout(3000)

            html = await self.page.content()
            m = UNIVERSAL_DATA_RE.search(html)
            if not m:
                return None

            return json.loads(m.group(1))
        except Exception:
            return None

    async def _fetch_sec_uid(self):
        """Extract the secUid from the profile page's __UNIVERSAL_DATA__.

        Cached after the first read. Used to call the item_list endpoint
        directly.
        """
        if self._sec_uid:
            return self._sec_uid

        data = await self._fetch_universal_data(self._profile_url)
        if not data:
            return None

        user_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
        sec_uid = user_detail.get("userInfo", {}).get("user", {}).get("secUid")
        if sec_uid:
            self._sec_uid = sec_uid
        return sec_uid

    async def _get_mstoken(self):
        """Read the msToken cookie value from the browser context.

        TikTok's item_list API commonly expects msToken as a query
        parameter. The cookie is set by TikTok and available same-origin.
        Returns an empty string if not present (call still attempted).
        """
        try:
            cookies = await self.page.context.cookies("https://www.tiktok.com")
            for c in cookies:
                if c.get("name") == "msToken":
                    return c.get("value", "")
        except Exception:
            pass
        return ""

    async def fetch_stats(self):
        """Get follower count from the TikTok profile page."""
        logged_in = await self._ensure_logged_in()
        if not logged_in:
            print("  ! Not logged in — attempting scan anyway (public profile)")
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

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch videos with publish dates.

        Walks videos newest-first and stops as soon as it hits either:
          - a post older than FIRST_SCAN_DAYS_LIMIT (7) days (7-day cutoff), or
          - an already-seen video (incremental stop).
        Whichever triggers first wins. This applies on EVERY scan, not
        just the first.

        Collection sources, in priority order:
          1. Direct /api/post/item_list/ call using secUid (most reliable).
          2. Passive capture of the item_list XHR.
          3. Grid scroll + per-video page dates (last resort).

        A post with a missing/unparseable date is kept
        (is_older_than_days() returns False) rather than discarded; it
        will be caught by the incremental stop instead.
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()

        print(f"  - Capturing videos from last {FIRST_SCAN_DAYS_LIMIT} days only")

        if not await self._ensure_logged_in():
            print("  ! Not logged in — attempting scan anyway (public profile)")
        # Primary: direct item_list API call.

        # Primary: direct item_list API call.
        posts = await self._fetch_posts_via_item_list_api(seen_shortcodes)
        if posts is not None:
            print(f"  - Total: {len(posts)} videos collected")
            return posts

        # Secondary: passive item_list XHR capture.
        posts = await self._fetch_posts_via_item_list_xhr(seen_shortcodes)
        if posts is not None:
            print(f"  - Total: {len(posts)} videos collected")
            return posts

        # Tertiary: grid + per-video pages.
        print("  - Falling back to grid + video pages")
        posts = await self._fetch_posts_via_grid(seen_shortcodes)
        print(f"  - Total: {len(posts)} videos collected")
        return posts

    async def _fetch_posts_via_item_list_api(self, seen_shortcodes):
        """Collect videos by calling /api/post/item_list/ directly.

        Uses the secUid from the profile page. Same-origin fetch carries
        the session cookies (including msToken, also passed as a query
        param). Returns up to 35 videos per request; paginates via
        cursor/hasMore. Applies both stop rules per video.

        Returns None to signal "try the next fallback" (secUid missing
        or endpoint blocked/returned non-itemList). Returns a list
        (possibly empty) when a real answer was obtained.
        """
        sec_uid = await self._fetch_sec_uid()
        if not sec_uid:
            print("  ! Could not extract secUid; skipping direct item_list call")
            return None

        print(f"  - secUid: {sec_uid[:24]}...")

        posts = []
        cursor = 0
        has_more = True
        page_num = 0
        stopped = False

        while has_more and page_num < 20 and not stopped:
            page_num += 1
            ms_token = await self._get_mstoken()

            # Build the API URL. msToken included as a query param when
            # available (TikTok commonly expects it). Credentials are
            # sent automatically via same-origin fetch with
            # credentials:'include'.
            params = [
                "aid=1988",
                "count=35",
                f"cursor={cursor}",
                "device_platform=web_pc",
                f"secUid={quote(sec_uid, safe='')}",
                "creator_hdl=" + quote(self._username, safe=""),
            ]
            if ms_token:
                params.append(f"msToken={quote(ms_token, safe='')}")
            url = ITEM_LIST_API + "?" + "&".join(params)

            try:
                data = await self.page.evaluate(
                    """async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            if (!r.ok) return {__error: 'http_' + r.status};
                            return await r.json();
                        } catch (e) {
                            return {__error: String(e)};
                        }
                    }""",
                    url,
                )
            except Exception as exc:
                print(f"  ! item_list API call failed: {exc}")
                return None

            if not data or (isinstance(data, dict) and data.get("__error")):
                err = data.get("__error") if data else "no_data"
                print(f"  ! item_list API blocked/empty ({err}); trying fallback")
                return None

            # No itemList key => likely a block/error payload, not a
            # genuine empty profile. Signal fallback.
            if not isinstance(data, dict) or "itemList" not in data:
                print("  ! item_list API: no itemList in response; trying fallback")
                return None

            item_list = data.get("itemList") or []
            if not item_list:
                print("  - item_list: empty itemList (no videos)")
                break

            print(f"  - item_list page {page_num}: {len(item_list)} videos")

            for item in item_list:
                vid = str(item.get("id") or "")
                if not vid:
                    continue

                title = item.get("desc", "") or ""
                ct = item.get("createTime")
                published_at = None
                if ct:
                    try:
                        published_at = self._timestamp_to_iso(int(ct))
                    except (ValueError, TypeError):
                        published_at = None

                # Incremental stop: already in the database.
                if vid in seen_shortcodes:
                    print(
                        f"  - Stopped: hit already-seen video ({vid}) "
                        f"at position {len(posts) + 1}"
                    )
                    stopped = True
                    break

                # 7-day cutoff: older than the scan window.
                if is_older_than_days(published_at, FIRST_SCAN_DAYS_LIMIT):
                    print(
                        f"  - Stopped: video {vid} is older than "
                        f"{FIRST_SCAN_DAYS_LIMIT} days"
                    )
                    stopped = True
                    break

                post_url = f"https://www.tiktok.com/@{self._username}/video/{vid}"
                posts.append(self.make_post(vid, title, post_url, published_at))

            if stopped:
                break

            cursor = data.get("cursor", cursor + len(item_list))
            has_more = bool(data.get("hasMore", False))
            if not has_more:
                print("  - item_list: no more videos (hasMore=false)")
                break

            await asyncio.sleep(DELAY_BETWEEN_POSTS_SEC)

        return posts

    async def _fetch_posts_via_item_list_xhr(self, seen_shortcodes):
        """Secondary: capture TikTok's own item_list XHR passively.

        Used only when the direct API call cannot be made. Returns None
        if no item_list response is ever captured (signals grid fallback).
        """
        collected = []  # newest-first, deduped
        seen_ids = set()
        state = {"captured": False}

        async def on_response(response):
            if ITEM_LIST_URL_FRAGMENT not in response.url:
                return
            state["captured"] = True
            try:
                data = await response.json()
            except Exception:
                return
            for item in data.get("itemList") or []:
                vid = item.get("id")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    collected.append(item)

        self.page.on("response", on_response)
        posts = []
        idx = 0
        stopped = False
        try:
            await self.page.goto(
                self._profile_url,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)

            stale = 0
            for scroll_round in range(20):
                stopped = self._drain_item_list_items(
                    collected, idx, seen_shortcodes, posts
                )
                idx = len(collected)
                if stopped:
                    break

                before = len(seen_ids)
                await self.page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                await self.page.keyboard.press("End")
                await self.page.wait_for_timeout(2500)

                if len(seen_ids) == before:
                    stale += 1
                else:
                    stale = 0

                if scroll_round == 0:
                    print(f"  - item_list XHR: {len(seen_ids)} videos captured so far")
                if stale >= 4:
                    print("  - item_list XHR: no more videos (grid exhausted)")
                    break

            if not stopped:
                self._drain_item_list_items(collected, idx, seen_shortcodes, posts)
        finally:
            try:
                self.page.remove_listener("response", on_response)
            except Exception:
                pass

        if not state["captured"]:
            return None  # signal fallback
        return posts

    def _drain_item_list_items(self, collected, start_idx, seen_shortcodes, posts):
        """Apply both stop rules to collected items from start_idx onward.

        Appends kept posts to `posts`. Returns True if a stop condition
        triggered (caller should stop scrolling), False otherwise.

        is_older_than_days() is called on every post, including those
        with a None date, so missing dates are kept (helper returns
        False) rather than discarded.
        """
        i = start_idx
        while i < len(collected):
            item = collected[i]
            i += 1
            vid = str(item.get("id") or "")
            if not vid:
                continue

            title = item.get("desc", "") or ""
            ct = item.get("createTime")
            published_at = None
            if ct:
                try:
                    published_at = self._timestamp_to_iso(int(ct))
                except (ValueError, TypeError):
                    published_at = None

            if vid in seen_shortcodes:
                print(
                    f"  - Stopped: hit already-seen video ({vid}) "
                    f"at position {len(posts) + 1}"
                )
                return True

            if is_older_than_days(published_at, FIRST_SCAN_DAYS_LIMIT):
                print(
                    f"  - Stopped: video {vid} is older than "
                    f"{FIRST_SCAN_DAYS_LIMIT} days"
                )
                return True

            url = f"https://www.tiktok.com/@{self._username}/video/{vid}"
            posts.append(self.make_post(vid, title, url, published_at))

        return False

    async def _fetch_posts_via_grid(self, seen_shortcodes):
        """Tertiary fallback: collect video IDs from the profile grid,
        then visit each video page for its date. Both stop rules are
        applied per video in the date loop.
        """
        video_ids = await self._scroll_grid_for_videos(seen_shortcodes)
        if not video_ids:
            print("  - No videos found on profile grid")
            return []

        print(f"  - Unique videos to date: {len(video_ids)}")
        print("  - Fetching publish dates from video pages...")

        posts = []
        dated_count = 0

        for i, video_id in enumerate(video_ids):
            published_at = await self._fetch_video_date(video_id)
            await asyncio.sleep(DELAY_BETWEEN_POSTS_SEC)

            if published_at:
                dated_count += 1

            if video_id in seen_shortcodes:
                print(
                    f"  - Stopped: hit already-seen video ({video_id}) "
                    f"at position {len(posts) + 1}"
                )
                break

            if is_older_than_days(published_at, FIRST_SCAN_DAYS_LIMIT):
                print(
                    f"  - Stopped: video {video_id} is older than "
                    f"{FIRST_SCAN_DAYS_LIMIT} days"
                )
                break

            if i < 20 or (i + 1) % 50 == 0:
                status = "OK" if published_at else "--"
                print(f"    {status} {video_id} -> {published_at or 'no date'}")

            url = f"https://www.tiktok.com/@{self._username}/video/{video_id}"
            title = ""
            posts.append(self.make_post(video_id, title, url, published_at))

        print(f"  - Dated: {dated_count}/{len(video_ids)}")
        return posts

    async def _scroll_grid_for_videos(self, seen_shortcodes):
        """Scroll the TikTok profile grid to collect video IDs.

        Stops when hitting an already-seen video (incremental stop), or
        when the grid is exhausted. The seen-check happens BEFORE
        appending so the already-seen video is not re-visited.
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
        seen_in_grid = set()
        stopped_early = False
        stale_scrolls = 0
        for scroll_round in range(50):
            links = await self.page.query_selector_all('a[href*="/video/"]')
            round_ids = []
            for link in links:
                href = await link.get_attribute("href") or ""
                m = VIDEO_ID_RE.search(href)
                if m:
                    vid = m.group(1)
                    if vid not in seen_in_grid:
                        seen_in_grid.add(vid)
                        round_ids.append(vid)

            if not round_ids:
                stale_scrolls += 1
            else:
                stale_scrolls = 0
                for vid in round_ids:
                    if vid in seen_shortcodes:
                        print(
                            f"  - Stopped early: hit already-seen video "
                            f"({vid}) at position {len(all_video_ids) + 1}"
                        )
                        stopped_early = True
                        break
                    all_video_ids.append(vid)

            if stopped_early:
                break
            if stale_scrolls >= 5:
                print("  - Grid exhausted (no new videos after 5 scrolls)")
                break

            print(
                f"  - Scroll {scroll_round + 1}: " f"{len(all_video_ids)} videos total"
            )
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.page.keyboard.press("End")
            await self.page.wait_for_timeout(2000)
        return all_video_ids

    async def _fetch_video_date(self, video_id):
        """Fetch exact publish date from a TikTok video page."""
        url = f"https://www.tiktok.com/@{self._username}/video/{video_id}"

        try:
            data = await self._fetch_universal_data(url)
            if not data:
                return None

            video_detail = data.get("__DEFAULT_SCOPE__", {}).get(
                "webapp.video-detail", {}
            )

            status_code = video_detail.get("statusCode", 0)
            if status_code != 0:
                return None

            item_struct = video_detail.get("itemInfo", {}).get("itemStruct", {})

            create_time = item_struct.get("createTime")
            if create_time:
                return self._timestamp_to_iso(int(create_time))

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
