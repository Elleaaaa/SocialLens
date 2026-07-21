# monitors/youtube.py
"""YouTube channel monitor.

No login or browser required. Uses two approaches:

1. First run (empty database): YouTube internal API with pagination
   to scrape ALL video IDs. No exact dates (Shorts don't expose them
   in the API), so detected_at is used as fallback.

2. Subsequent runs: YouTube RSS feed returns the latest 15 videos
   with exact publish dates. Stops at first already-seen video.

Subscriber count comes from the internal browse API.

Channel handle resolution (@handle -> UC... channel ID) uses httpx
with a page-based fallback (YouTube redirects non-browser clients
to a consent page).
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone

import httpx

from monitors.base import BaseMonitor, parse_count
from config import DELAY_BETWEEN_POSTS_SEC, MAX_POSTS_TO_DATE

YT_API_KEY = os.environ.get("YT_API_KEY", "")
YT_BROWSE_URL = "https://www.youtube.com/youtubei/v1/browse"
YT_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

CHANNEL_ID_RE = re.compile(r"(UC[\w-]{22})")
HANDLE_RE = re.compile(r"@([\w.\-]+)")
CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[\w-]+)"'
)
META_CHANNEL_RE = re.compile(
    r'<meta\s+itemprop=["\']channelId["\']\s+content="(UC[\w-]+)"'
)
SUBSCRIBER_RE = re.compile(r'"content"\s*:\s*"([\d.,]+\s*[KMBkmb]?\s*subscribers)"')

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Videos tab params (gets all videos, not just shorts)
VIDEOS_TAB_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


class YouTubeMonitor(BaseMonitor):
    platform = "youtube"
    metric_label = "subscribers"

    def __init__(self, page, profile):
        super().__init__(page, profile)
        self._channel_id = None
        self._channel_url = profile["url"]

    async def _resolve_channel_id(self):
        """Resolve a YouTube URL to a channel ID.

        Uses httpx as the primary path (no browser needed). Falls back
        to the browser page if httpx gets a consent redirect.
        """
        if self._channel_id:
            return self._channel_id

        url = self._channel_url

        # Already a channel ID in the URL
        m = CHANNEL_ID_RE.search(url)
        if m:
            self._channel_id = m.group(1)
            return self._channel_id

        print(f"  - Resolving YouTube channel ID from {url}...")

        # Primary path: httpx (no browser required)
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=30, headers=DEFAULT_HEADERS
            ) as client:
                r = await client.get(url)
                html = r.text

                m = CANONICAL_RE.search(html)
                if m:
                    self._channel_id = m.group(1)
                    print(f"  - Channel ID: {self._channel_id}")
                    return self._channel_id

                m = META_CHANNEL_RE.search(html)
                if m:
                    self._channel_id = m.group(1)
                    print(f"  - Channel ID: {self._channel_id}")
                    return self._channel_id

                m = CHANNEL_ID_RE.search(html)
                if m:
                    self._channel_id = m.group(1)
                    print(f"  - Channel ID: {self._channel_id}")
                    return self._channel_id
        except Exception as exc:
            print(f"  - httpx channel resolution failed: {exc}")

        # Fallback: browser page (if available — YouTube may redirect
        # non-browser clients to a consent page)
        if self.page:
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await self.page.wait_for_timeout(3000)

                html = await self.page.content()

                m = CANONICAL_RE.search(html)
                if m:
                    self._channel_id = m.group(1)
                    print(f"  - Channel ID: {self._channel_id}")
                    return self._channel_id

                m = META_CHANNEL_RE.search(html)
                if m:
                    self._channel_id = m.group(1)
                    print(f"  - Channel ID: {self._channel_id}")
                    return self._channel_id

                m = CHANNEL_ID_RE.search(html)
                if m:
                    self._channel_id = m.group(1)
                    print(f"  - Channel ID: {self._channel_id}")
                    return self._channel_id
            except Exception as exc:
                print(f"  ! Error resolving channel ID via browser: {exc}")

        print("  ! Could not resolve YouTube channel ID")
        return None

    async def _api_browse(self, client, browse_id=None, continuation=None):
        """Call YouTube internal browse API."""
        if not YT_API_KEY:
            print("  ! YT_API_KEY not set in environment")
            return None

        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": "en",
                    "gl": "US",
                }
            },
        }
        if continuation:
            payload["continuation"] = continuation
        else:
            payload["browseId"] = browse_id
            payload["params"] = VIDEOS_TAB_PARAMS

        r = await client.post(
            YT_BROWSE_URL,
            params={"key": YT_API_KEY},
            json=payload,
        )
        return r.json() if r.status_code == 200 else None

    def _extract_videos_from_contents(self, contents):
        """Extract video IDs and titles from API response contents."""
        results = []
        for item in contents:
            ri = item.get("richItemRenderer", {})
            content = ri.get("content", {})

            # shortsLockupViewModel (Shorts)
            if "shortsLockupViewModel" in content:
                slvm = content["shortsLockupViewModel"]
                entity_id = slvm.get("entityId", "")
                vid = entity_id.replace("shorts-shelf-item-", "")
                if not vid:
                    tap = slvm.get("onTap", {})
                    cmd = tap.get("innertubeCommand", {})
                    rwe = cmd.get("reelWatchEndpoint", {})
                    vid = rwe.get("videoId", "")
                acc_text = slvm.get("accessibilityText", "")
                # Clean up accessibility text (remove view count suffix)
                title = acc_text.rsplit(", ", 1)[0] if ", " in acc_text else acc_text
                results.append(
                    {
                        "video_id": vid,
                        "title": title,
                        "published_text": "",
                        "is_short": True,
                    }
                )

            # videoRenderer (regular videos)
            elif "videoRenderer" in content:
                vr = content["videoRenderer"]
                vid = vr.get("videoId", "")
                title_runs = vr.get("title", {}).get("runs", [])
                title = "".join(r.get("text", "") for r in title_runs)
                pub = vr.get("publishedTimeText", {}).get("simpleText", "")
                results.append(
                    {
                        "video_id": vid,
                        "title": title,
                        "published_text": pub,
                        "is_short": False,
                    }
                )

        return results

    @staticmethod
    def _find_continuation_token(data):
        """Find continuation token for pagination."""

        def search(obj):
            if isinstance(obj, dict):
                if "continuationCommand" in obj:
                    return obj["continuationCommand"].get("token")
                for v in obj.values():
                    result = search(v)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = search(item)
                    if result:
                        return result
            return None

        return search(data)

    def _get_contents_from_response(self, data):
        """Extract contents list from API response (handles both first page and continuation)."""
        on_resp = data.get("onResponseReceivedActions", [])
        if on_resp:
            for action in on_resp:
                if "appendContinuationItemsAction" in action:
                    return action["appendContinuationItemsAction"].get(
                        "continuationItems", []
                    )
                elif "reloadContinuationItemsCommand" in action:
                    return action["reloadContinuationItemsCommand"].get(
                        "continuationItems", []
                    )
            return []

        return (
            data.get("contents", {})
            .get("twoColumnBrowseResultsRenderer", {})
            .get("tabs", [{}])[0]
            .get("tabRenderer", {})
            .get("content", {})
            .get("richGridRenderer", {})
            .get("contents", [])
        )

    async def fetch_stats(self):
        """Get subscriber count from the YouTube internal API."""
        channel_id = await self._resolve_channel_id()
        if not channel_id:
            self.followers = 0
            return self.followers

        try:
            payload = {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": "2.20240101.00.00",
                        "hl": "en",
                        "gl": "US",
                    }
                },
                "browseId": channel_id,
            }

            async with httpx.AsyncClient(timeout=30, headers=DEFAULT_HEADERS) as client:
                r = await client.post(
                    YT_BROWSE_URL,
                    params={"key": YT_API_KEY} if YT_API_KEY else {},
                    json=payload,
                )

                if r.status_code != 200:
                    print(f"  ! YouTube API returned {r.status_code}")
                    self.followers = 0
                    return self.followers

                text = r.text
                m = SUBSCRIBER_RE.search(text)
                if m:
                    raw = m.group(1)
                    count = parse_count(raw)
                    self.followers = count
                    print(f"  - Subscribers: {count:,} ({raw})")
                    return self.followers

                print("  ! YouTube: subscriber count not found")
                self.followers = 0
                return self.followers

        except Exception as exc:
            print(f"  ! YouTube stats error: {exc}")
            self.followers = 0
            return self.followers

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch videos via internal API with pagination.
        Always uses the API to get all videos (newest first), stopping
        early when hitting an already-seen video. Also fetches the RSS
        feed to get exact publish dates for the latest 15 videos.
        For Shorts, the API does not expose exact publish dates, so
        detected_at is used as fallback for older videos.
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()
        channel_id = await self._resolve_channel_id()
        if not channel_id:
            return []

        # Use a single httpx client for all requests in this scan
        async with httpx.AsyncClient(timeout=30, headers=DEFAULT_HEADERS) as client:
            # Fetch RSS to get exact dates for the latest 15 videos
            rss_dates = await self._fetch_rss_dates(client, channel_id)
            # Fetch all videos via API with pagination
            return await self._fetch_all_via_api(
                client, channel_id, seen_shortcodes, rss_dates
            )

    async def _fetch_rss_dates(self, client, channel_id):
        """Fetch RSS feed to get exact publish dates for latest 15 videos."""
        rss_dates = {}
        feed_url = YT_RSS_URL.format(channel_id=channel_id)
        try:
            r = await client.get(feed_url)
            if r.status_code == 200:
                xml = r.text
                entries = re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL)
                for entry in entries:
                    vid_m = re.search(r"<yt:videoId>([\w-]+)</yt:videoId>", entry)
                    pub_m = re.search(r"<published>([^<]+)</published>", entry)
                    if vid_m and pub_m:
                        rss_dates[vid_m.group(1)] = self._normalize_datetime(
                            pub_m.group(1)
                        )
                print(
                    f"  - RSS: got exact dates for " f"{len(rss_dates)} latest videos"
                )
        except Exception as exc:
            print(f"  ! YouTube RSS error: {exc}")
        return rss_dates

    async def _fetch_all_via_api(self, client, channel_id, seen_shortcodes, rss_dates):
        """Fetch all videos via internal API with pagination.
        Stops early when hitting an already-seen video (incremental).
        Fetches exact publish dates from each video page.
        """
        all_videos = []
        page = 0
        cont_token = None
        stopped_early = False
        try:
            while True:
                page += 1
                if page > 1:
                    await asyncio.sleep(1)
                data = await self._api_browse(client, channel_id, cont_token)
                if not data:
                    break
                contents = self._get_contents_from_response(data)
                videos = self._extract_videos_from_contents(contents)
                for v in videos:
                    if v["video_id"] in seen_shortcodes:
                        print(
                            f"  - Stopped early: hit already-seen "
                            f"video ({v['video_id']}) on page {page}"
                        )
                        stopped_early = True
                        break
                    all_videos.append(v)
                if stopped_early:
                    break
                print(
                    f"  - Page {page}: {len(videos)} videos "
                    f"(total: {len(all_videos)})"
                )
                cont_token = self._find_continuation_token(data)
                if not cont_token:
                    print("  - No more pages")
                    break
                if page >= 20:
                    print("  - Reached page limit (20)")
                    break
        except Exception as exc:
            print(f"  ! YouTube API error: {exc}")

        # Deduplicate
        seen = set()
        unique = []
        for v in all_videos:
            if v["video_id"] and v["video_id"] not in seen:
                seen.add(v["video_id"])
                unique.append(v)
        print(f"  - Total unique videos: {len(unique)}")

        # Apply MAX_POSTS_TO_DATE cap if set
        limit = MAX_POSTS_TO_DATE if MAX_POSTS_TO_DATE > 0 else len(unique)
        unique = unique[:limit]
        if MAX_POSTS_TO_DATE > 0 and len(unique) == MAX_POSTS_TO_DATE:
            print(f"  - Capped at {MAX_POSTS_TO_DATE} videos for date extraction")

        # Fetch exact publish dates from video pages
        print("  - Fetching publish dates from video pages...")
        posts = []
        dated_count = 0
        for i, v in enumerate(unique):
            video_id = v["video_id"]
            title = v["title"]
            # Use RSS date if available (saves a request)
            published_at = rss_dates.get(video_id)
            # Otherwise fetch from video page
            if not published_at:
                published_at = await self._fetch_video_date(client, video_id)
                await asyncio.sleep(0.5)  # Rate limit courtesy
            if published_at:
                dated_count += 1
                if i < 20 or (i + 1) % 50 == 0:
                    print(f"    OK {video_id} -> {published_at}")
            else:
                if i < 20 or (i + 1) % 50 == 0:
                    print(f"    -- {video_id} -> no date")
            # Use correct URL format based on video type
            if v.get("is_short"):
                url = f"https://www.youtube.com/shorts/{video_id}"
            else:
                url = f"https://www.youtube.com/watch?v={video_id}"
            posts.append(self.make_post(video_id, title, url, published_at))
        print(f"  - Dated: {dated_count}/{len(posts)}")
        print(f"  - Total: {len(posts)} videos collected")
        return posts

    async def _fetch_video_date(self, client, video_id):
        """Fetch exact publish date from a YouTube video page.
        YouTube embeds the date as:
        "dateText":{"simpleText":"Jun 23, 2026"}
        """
        url = f"https://www.youtube.com/watch?v={video_id}"
        for attempt in range(3):
            try:
                r = await client.get(url)
                if r.status_code == 429:
                    wait = 10 * (attempt + 1)
                    print(f"    ! 429 on {video_id}, backing off {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if r.status_code != 200:
                    return None
                html = r.text
                # Extract dateText simpleText
                m = re.search(
                    r'"dateText"\s*:\s*\{[^}]*"simpleText"\s*:\s*"([^"]+)"',
                    html,
                )
                if m:
                    return self._parse_date_text(m.group(1))
                # Fallback: look for "uploadDate" in JSON-LD
                m = re.search(r'"uploadDate"\s*:\s*"([^"]+)"', html)
                if m:
                    return self._normalize_datetime(m.group(1))
                return None
            except Exception as exc:
                if attempt == 2:
                    print(f"    ! error on {video_id}: {exc}")
                await asyncio.sleep(2)
        return None

    @staticmethod
    def _parse_date_text(text):
        """Parse YouTube date text like 'Jun 23, 2026' to ISO format."""
        try:
            # YouTube uses format like "Jun 23, 2026" or "23 Jun 2026"
            for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"):
                try:
                    dt = datetime.strptime(text.strip(), fmt)
                    return dt.replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    @staticmethod
    def _normalize_datetime(value):
        """Normalize a datetime string to ISO 8601 with UTC timezone."""
        try:
            cleaned = value.strip()
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except (ValueError, TypeError):
            return value
