"""Facebook page monitor using Playwright with saved session + manual login.

Loads the saved session (fb_state.json) first. If still valid,
no login is needed. If expired, requires manual login via a visible
browser window. Login completion is auto-detected.

- Follower count: Parse from page after login
- Post list: Collect ALL reel links from the profile feed, then fetch
  each reel's timestamp. Also collect non-reel posts via timestamp
  detection.

7-Day Scan Rule (all browser-based platforms):
  Every scan captures only posts from the last 7 days, then stops.
  Two stop conditions compete per-post, newest-first, in a single pass:
    1. 7-day cutoff  -> post's published_at is older than the window
    2. incremental   -> post_id already seen (already in the DB)
  Whichever triggers first wins and the scan breaks immediately.
  is_older_than_days() returns False for missing/unparseable dates, so
  undated posts are kept (and will be stopped by the incremental check).
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from monitors.base import BaseMonitor, is_older_than_days, parse_count
from config import (
    PAGE_TIMEOUT_MS,
    FB_SESSION_FILE,
    FB_SCROLL_ROUNDS,
    FB_STALE_SCROLLS,
    FB_SCROLL_DELAY_MS,
    FIRST_SCAN_DAYS_LIMIT,
)

FOLLOWERS_RE = re.compile(r"([\d,.]+[KMBkmb]?)\s*[Ff]ollowers")
LIKES_RE = re.compile(r"([\d,.]+[KMBkmb]?)\s*[Ll]ikes")
SESSION_VALID_URL = "https://www.facebook.com/settings"
LOGIN_URL = "https://www.facebook.com/login.php"
LOGIN_TIMEOUT_SEC = 300

REL_TIME_RE = re.compile(
    r"(\d+)\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|w|wk|week|weeks)\b",
    re.IGNORECASE,
)


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

    async def _load_saved_session(self):
        state_path = Path(FB_SESSION_FILE)
        if not state_path.exists():
            return
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "cookies" in data:
                cookies = data["cookies"]
            elif isinstance(data, list):
                cookies = data
            else:
                print("  - Session file format unrecognized")
                return
            if cookies:
                await self.page.context.add_cookies(cookies)
                print("  - Loaded saved Facebook session")
        except Exception as exc:
            print(f"  ! Could not load saved session: {exc}")

    async def _save_session(self):
        try:
            state = await self.page.context.storage_state()
            with open(FB_SESSION_FILE, "w") as f:
                json.dump(state, f, indent=2)
            print("  - Session saved for future runs")
        except Exception as exc:
            print(f"  ! Could not save session: {exc}")

    async def _ensure_logged_in(self):
        if self._logged_in:
            return True
        await self._load_saved_session()
        if await self._is_session_valid():
            print("  - Facebook session still valid, skipping login")
            self._logged_in = True
            return True
        print("  - Facebook login required. Please log in in the browser window.")
        print(
            f"  - Waiting up to {LOGIN_TIMEOUT_SEC // 60} minutes for you to log in..."
        )
        if await self._do_manual_login():
            self._logged_in = True
            return True
        return False

    async def _is_session_valid(self):
        try:
            await self.page.goto(
                SESSION_VALID_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(3000)
            if "login" in self.page.url:
                return False
            return True
        except Exception:
            return False

    async def _verify_logged_in_on_page(self):
        try:
            current_url = self.page.url
            if (
                "login" in current_url
                or "checkpoint" in current_url
                or "two_step" in current_url
            ):
                return False
            nav = await self.page.query_selector(
                '[role="navigation"], div[role="banner"]'
            )
            if nav:
                return True
            account = await self.page.query_selector(
                'a[href*="/me/"], a[aria-label*="Account"], a[aria-label*="account"]'
            )
            if account:
                return True
            compose = await self.page.query_selector(
                'a[aria-label*="Create"], a[href*="/stories/create"]'
            )
            if compose:
                return True
            return False
        except Exception:
            return False

    async def _do_manual_login(self):
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
        start = time.time()
        poll_ms = 2000
        while time.time() - start < LOGIN_TIMEOUT_SEC:
            await self.page.wait_for_timeout(poll_ms)
            if await self._verify_logged_in_on_page():
                print("  - Facebook login detected. Saving fresh session...")
                await self._save_session()
                await self.page.wait_for_timeout(2000)
                return True
        print("  ! Facebook login timed out. Skipping Facebook profiles.")
        return False

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def fetch_stats(self):
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
            body_text = await self.page.inner_text("body")
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

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch posts from the profile feed.

        Collects ALL reel links from the profile feed (reels are the most
        common post type but were missed by the old container-based
        scraper), then fetches each reel's timestamp. Also collects
        non-reel posts via timestamp detection.

        Applies the 7-Day Scan Rule: stops as soon as it hits a post
        older than 7 days, or a post already seen — whichever comes first.
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()

        logged_in = await self._ensure_logged_in()
        if not logged_in:
            return []

        all_posts = []

        # Phase 1: collect reel IDs from the feed.
        reel_ids = await self._collect_reel_ids()
        print(f"  - Reel IDs found on feed: {len(reel_ids)}")

        # Phase 2: fetch each reel's timestamp, applying both stops.
        reels = await self._process_reels(reel_ids, seen_shortcodes)
        all_posts.extend(reels)

        # Phase 3: collect non-reel posts from the feed.
        timeline = await self._collect_timeline_posts(seen_shortcodes)
        all_posts.extend(timeline)

        # Dedupe.
        seen = set()
        deduped = []
        for post in all_posts:
            pid = getattr(post, "post_id", None)
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            deduped.append(post)

        print(f"  - Total posts collected: {len(deduped)}")
        return deduped

    # ------------------------------------------------------------------
    # Phase 1: collect reel IDs from the feed
    # ------------------------------------------------------------------

    async def _collect_reel_ids(self):
        """Scroll the profile feed and collect ALL reel IDs.

        Reels appear on the main feed as posts with links containing
        /reel/{id}. We collect them with no container logic and no
        timestamp requirement — the timestamp is fetched separately.
        """
        try:
            await self.page.goto(
                self._page_url,
                wait_until="networkidle",
                timeout=PAGE_TIMEOUT_MS,
            )
            await self.page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"  ! facebook: could not load feed: {exc}")
            return []

        if "login" in self.page.url:
            print("  ! Redirected to login when loading feed")
            return []

        reel_ids = []
        seen_local = set()
        stale_scrolls = 0

        for scroll_round in range(FB_SCROLL_ROUNDS):
            batch = await self.page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="/reel/"]');
                    const ids = [];
                    const seen = new Set();
                    for (const link of links) {
                        const match = link.href.match(/\\/reel\\/(\\d+)/);
                        if (match && !seen.has(match[1])) {
                            seen.add(match[1]);
                            ids.push(match[1]);
                        }
                    }
                    return ids;
                }""")

            new_count = 0
            for rid in batch:
                if rid not in seen_local:
                    seen_local.add(rid)
                    reel_ids.append(rid)
                    new_count += 1

            if new_count > 0:
                stale_scrolls = 0
            else:
                stale_scrolls += 1

            if stale_scrolls >= FB_STALE_SCROLLS:
                break

            print(
                f"  - Feed scroll {scroll_round + 1}: "
                f"{len(reel_ids)} reels collected"
            )
            await self.page.evaluate(
                "() => window.scrollTo({top: document.body.scrollHeight,"
                " behavior:'smooth'})"
            )
            await self.page.wait_for_timeout(FB_SCROLL_DELAY_MS)

        return reel_ids

    # ------------------------------------------------------------------
    # Phase 2: process reels (fetch timestamp + apply stops)
    # ------------------------------------------------------------------

    async def _process_reels(self, reel_ids, seen_shortcodes):
        """Fetch each reel's timestamp and apply the 7-day rule.

        Reel IDs are assumed newest-first (feed order). For each:
          - incremental stop if already in DB
          - fetch timestamp from the reel page
          - 7-day cutoff stop
        """
        if not reel_ids:
            return []

        all_posts = []

        for i, reel_id in enumerate(reel_ids):
            if reel_id in seen_shortcodes:
                print(
                    f"  - Stopped early: hit already-seen reel "
                    f"({reel_id}) at position {len(all_posts)}"
                )
                break

            ts_info = await self._fetch_reel_timestamp(reel_id)
            published_at = self._resolve_published_at(ts_info)

            if is_older_than_days(published_at, FIRST_SCAN_DAYS_LIMIT):
                print(
                    f"  - Stopped early: hit reel older than "
                    f"{FIRST_SCAN_DAYS_LIMIT} days "
                    f"({reel_id}) at position {len(all_posts)}"
                )
                break

            all_posts.append(
                self.make_post(
                    reel_id,
                    "",
                    f"https://www.facebook.com/reel/{reel_id}",
                    published_at,
                )
            )

            if (i + 1) % 5 == 0:
                print(
                    f"  - Reel {i + 1}/{len(reel_ids)}: " f"{len(all_posts)} in-window"
                )

        dated = sum(1 for p in all_posts if p.published_at)
        print(f"  - Reels: {len(all_posts)} kept, {dated} dated")
        return all_posts

    async def _fetch_reel_timestamp(self, reel_id):
        """Fetch a reel's page HTML and extract its timestamp.

        Uses fetch() within the page context (carries session cookies).
        """
        try:
            result = await self.page.evaluate(
                """async (reelId) => {
                    try {
                        const resp = await fetch('/reel/' + reelId, {
                            credentials: 'include'
                        });
                        const html = await resp.text();
                        const doc = new DOMParser().parseFromString(
                            html, 'text/html'
                        );

                        // 1. data-utime (exact Unix timestamp)
                        const utimeEl = doc.querySelector('[data-utime]');
                        if (utimeEl) {
                            return {
                                utime: utimeEl.getAttribute('data-utime'),
                                dateStr: '',
                                relativeTime: ''
                            };
                        }

                        // 2. aria-label full date or relative time
                        const dateRegex = /(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\\s*(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d+,?\\s+\\d{4}/i;
                        const relRegex = /^\\s*(\\d+)\\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|w|wk|week|weeks)\\b/i;

                        const els = doc.querySelectorAll(
                            'a[aria-label], abbr, [title]'
                        );
                        for (const el of els) {
                            const ariaLabel = el.getAttribute('aria-label')
                                || el.getAttribute('title') || '';
                            const text = (el.textContent || '').trim();

                            if (dateRegex.test(ariaLabel)) {
                                return {
                                    utime: '',
                                    dateStr: ariaLabel.match(dateRegex)[0],
                                    relativeTime: ''
                                };
                            }
                            const relM = text.match(relRegex);
                            if (relM) {
                                return {
                                    utime: '',
                                    dateStr: '',
                                    relativeTime: relM[0]
                                };
                            }
                            if (/^\\s*just now/i.test(text)) {
                                return {
                                    utime: '',
                                    dateStr: '',
                                    relativeTime: 'just now'
                                };
                            }
                        }

                        return null;
                    } catch (e) {
                        return null;
                    }
                }""",
                reel_id,
            )
            return result or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Phase 3: collect non-reel posts from the feed
    # ------------------------------------------------------------------

    async def _collect_timeline_posts(self, seen_shortcodes):
        """Collect non-reel posts (text, photos) from the feed.

        Uses timestamp-based detection. Skips reels (handled above).
        """
        all_posts = []
        stopped_early = False
        stale_scrolls = 0
        seen_ids = set()

        for scroll_round in range(FB_SCROLL_ROUNDS):
            posts_on_page = await self.page.evaluate("""() => {
                    const results = [];
                    const dateRegex = /(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday),\\s*(January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d+,?\\s+\\d{4}/i;
                    const relRegex = /^\\s*(\\d+)\\s*(s|sec|secs|m|min|mins|h|hr|hrs|d|day|days|w|wk|week|weeks)\\b/i;

                    const candSet = new Set();
                    document.querySelectorAll('a[aria-label]').forEach(
                        el => candSet.add(el)
                    );
                    document.querySelectorAll('[data-utime]').forEach(
                        el => candSet.add(el)
                    );

                    for (const link of candSet) {
                        const ariaLabel = link.getAttribute('aria-label')
                            || '';
                        let utime = link.getAttribute('data-utime') || '';
                        if (!utime) {
                            const inner = link.querySelector('[data-utime]');
                            if (inner) {
                                utime = inner.getAttribute('data-utime') || '';
                            }
                        }
                        const text = (link.innerText
                            || link.textContent || '').trim();

                        const fullDateMatch = dateRegex.test(ariaLabel)
                            ? ariaLabel.match(dateRegex) : null;
                        const hasUtime = !!utime;
                        const relMatch = relRegex.test(text)
                            ? text.match(relRegex) : null;
                        const justNow = /^\\s*just now/i.test(text)
                            || /^\\s*just now/i.test(ariaLabel);

                        if (!fullDateMatch && !hasUtime
                                && !relMatch && !justNow) continue;

                        let container = link.closest(
                            '[role="article"], [data-pagelet],'
                            + ' div[class*="feed"], div[class*="post"]'
                        );
                        if (!container) container = link.parentElement;
                        if (!container) continue;

                        // Skip reels — handled separately.
                        let postLink = container.querySelector(
                            'a[href*="/posts/"],'
                            + ' a[href*="/photo/?fbid"],'
                            + ' a[href*="story_fbid"]'
                        );
                        if (!postLink) {
                            const href = link.href || '';
                            if (href.includes('/posts/')
                                    || href.includes('fbid=')) {
                                postLink = link;
                            }
                        }
                        if (!postLink) continue;

                        const href = postLink.href;
                        let postId = null;
                        const postMatch = href.match(/\\/posts\\/(\\d+)/);
                        const fbidMatch = href.match(/fbid=(\\d+)/);
                        const storyMatch = href.match(
                            /story_fbid=(pfbid\\w+|\\d+)/
                        );
                        if (postMatch) postId = postMatch[1];
                        else if (fbidMatch) postId = fbidMatch[1];
                        else if (storyMatch) postId = storyMatch[1];
                        if (!postId) continue;
                        if (results.some(r => r.postId === postId))
                            continue;

                        results.push({
                            postId: postId,
                            href: href.split('?')[0],
                            dateStr: fullDateMatch
                                ? fullDateMatch[0] : '',
                            utime: utime || '',
                            relativeTime: justNow
                                ? 'just now'
                                : (relMatch ? relMatch[0] : text),
                        });
                    }
                    return results;
                }""")

            new_posts = []
            for p in posts_on_page:
                post_id = p.get("postId")
                if not post_id or post_id in seen_ids:
                    continue
                seen_ids.add(post_id)
                published_at = self._resolve_published_at(p)
                new_posts.append(
                    {
                        "post_id": post_id,
                        "url": p.get("href", self._page_url),
                        "published_at": published_at,
                    }
                )

            if new_posts:
                stale_scrolls = 0
                new_posts.sort(
                    key=lambda x: self._sort_key(x["published_at"]),
                    reverse=True,
                )
                for post in new_posts:
                    if is_older_than_days(post["published_at"], FIRST_SCAN_DAYS_LIMIT):
                        print(
                            f"  - Stopped early: hit post older than "
                            f"{FIRST_SCAN_DAYS_LIMIT} days "
                            f"({post['post_id']}) at position "
                            f"{len(all_posts)}"
                        )
                        stopped_early = True
                        break
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
            if stale_scrolls >= FB_STALE_SCROLLS:
                break

            await self.page.evaluate(
                "() => window.scrollTo({top: document.body.scrollHeight,"
                " behavior:'smooth'})"
            )
            await self.page.wait_for_timeout(FB_SCROLL_DELAY_MS)

        result = []
        dated = 0
        for post in all_posts:
            if post["published_at"]:
                dated += 1
            result.append(
                self.make_post(post["post_id"], "", post["url"], post["published_at"])
            )
        print(f"  - Timeline: {len(result)} kept, {dated} dated")
        return result

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    def _resolve_published_at(self, post):
        """Resolve a post's published_at ISO string.

        Preference order: data-utime, full date, relative time.
        Returns None if nothing parses (caller keeps the post).
        """
        utime = post.get("utime")
        if utime:
            try:
                iso = self._timestamp_to_iso(int(utime))
                if iso:
                    return iso
            except (ValueError, TypeError):
                pass

        date_str = post.get("dateStr", "")
        if date_str:
            iso = self._parse_fb_date(date_str)
            if iso:
                return iso

        rel = post.get("relativeTime", "")
        if rel:
            iso = self._parse_relative_time(rel)
            if iso:
                return iso

        return None

    @staticmethod
    def _sort_key(published_at):
        if published_at:
            try:
                return datetime.fromisoformat(published_at).timestamp()
            except (ValueError, TypeError):
                pass
        return float("inf")

    @staticmethod
    def _parse_fb_date(date_str):
        try:
            cleaned = re.sub(
                r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday" r"|Saturday),\s*",
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
                    return dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_relative_time(text):
        if not text:
            return None
        t = text.strip().lower()
        now = datetime.now(timezone.utc)
        try:
            if t.startswith("just now"):
                return now.isoformat()
            m = REL_TIME_RE.match(t)
            if not m:
                return None
            n = int(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith("s"):
                delta = timedelta(seconds=n)
            elif unit.startswith("m"):
                delta = timedelta(minutes=n)
            elif unit.startswith("h"):
                delta = timedelta(hours=n)
            elif unit.startswith("d"):
                delta = timedelta(days=n)
            elif unit.startswith("w"):
                delta = timedelta(weeks=n)
            else:
                return None
            return (now - delta).isoformat()
        except Exception:
            return None

    @staticmethod
    def _timestamp_to_iso(ts):
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OSError, TypeError):
            return None
