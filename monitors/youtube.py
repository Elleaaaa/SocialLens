# monitors/youtube.py
"""YouTube channel monitor.

No login or browser required. Uses YouTube's internal (InnerTube) API
with the shared public web key — no personal API key, no quota, no
Google Cloud setup.

Scanning logic (applies on EVERY scan):
  - Walk videos newest-first via paginated InnerTube browse.
  - Stop as soon as a post is older than FIRST_SCAN_DAYS_LIMIT (7) days
    (7-day cutoff), OR at the first already-seen video (incremental
    stop). Whichever triggers first wins.
  - is_older_than_days() returns False for missing/unparseable dates,
    so Shorts (which expose no date in the API) are kept until the
    incremental stop catches them.

Publish dates:
  - The RSS feed supplies exact dates for the latest 15 videos.
  - Regular videos expose relative publishedTimeText ("2 days ago"),
    parsed into an approximate date used to evaluate the 7-day stop.
  - Exact dates for the collected in-window subset are fetched from
    each video page; detected_at is the fallback when no date is found.

Subscriber count comes from the internal browse API.

Channel handle resolution (@handle -> UC... channel ID) uses httpx
with a page-based fallback (YouTube redirects non-browser clients
to a consent page).
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

from monitors.base import BaseMonitor, parse_count, is_older_than_days
from config import DELAY_BETWEEN_POSTS_SEC, FIRST_SCAN_DAYS_LIMIT

# Shared public InnerTube web key — the same key YouTube's own web
# player uses. Free, no signup, no quota. Used for all browse calls.
_INNERTUBE_WEB_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

YT_BROWSE_URL = "https://www.youtube.com/youtubei/v1/browse"
YT_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

CHANNEL_ID_RE = re.compile(r"(UC[\w-]{22})")
CANONICAL_RE = re.compile(
    r'<link\s+rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[\w-]+)"'
)
META_CHANNEL_RE = re.compile(
    r'<meta\s+itemprop=["\']channelId["\']\s+content="(UC[\w-]+)"'
)
SUBSCRIBER_RE = re.compile(r'"content"\s*:\s*"([\d.,]+\s*[KMBkmb]?\s*subscribers)"')

# "2 days ago", "3 weeks ago", "1 month ago", "Streamed 2 days ago", etc.
RELATIVE_DATE_RE = re.compile(
    r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago",
    re.IGNORECASE,
)
_RELATIVE_DAYS = {
    "second": 0,
    "minute": 0,
    "hour": 0,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}

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

    # ------------------------------------------------------------------
    # Channel ID resolution (httpx with browser fallback)
    # ------------------------------------------------------------------

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

                for rx in (CANONICAL_RE, META_CHANNEL_RE, CHANNEL_ID_RE):
                    m = rx.search(html)
                    if m:
                        self._channel_id = m.group(1)
                        print(f"  - Channel ID: {self._channel_id}")
                        return self._channel_id
        except Exception as exc:
            print(f"  - httpx channel resolution failed: {exc}")

        # Fallback: browser page (YouTube may redirect non-browser
        # clients to a consent page)
        if self.page:
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await self.page.wait_for_timeout(3000)
                html = await self.page.content()

                for rx in (CANONICAL_RE, META_CHANNEL_RE, CHANNEL_ID_RE):
                    m = rx.search(html)
                    if m:
                        self._channel_id = m.group(1)
                        print(f"  - Channel ID: {self._channel_id}")
                        return self._channel_id
            except Exception as exc:
                print(f"  ! Error resolving channel ID via browser: {exc}")

        print("  ! Could not resolve YouTube channel ID")
        return None

    # ------------------------------------------------------------------
    # InnerTube browse API
    # ------------------------------------------------------------------

    async def _api_browse(self, client, browse_id=None, continuation=None):
        """Call YouTube internal (InnerTube) browse API.

        Uses the shared public web key — no personal API key required.
        """
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
            params={"key": _INNERTUBE_WEB_KEY},
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
        """Extract contents list from API response (first page or continuation)."""
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

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

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
                    params={"key": _INNERTUBE_WEB_KEY},
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

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def fetch_posts(self, seen_shortcodes=None):
        """Fetch videos via the InnerTube API with pagination.

        Walks videos newest-first and stops as soon as it hits either:
          - a post older than FIRST_SCAN_DAYS_LIMIT (7) days (7-day cutoff), or
          - an already-seen video (incremental stop).
        Whichever triggers first wins. This applies on EVERY scan.

        RSS supplies exact dates for the latest 15 videos; relative
        publishedTimeText is parsed into an approximate date used only to
        evaluate the 7-day stop. Exact dates for the collected in-window
        subset are then fetched from the video pages. Shorts expose no
        date in the API, so is_older_than_days() returns False for them
        and they are kept until the incremental stop catches them.
        """
        if seen_shortcodes is None:
            seen_shortcodes = set()
        channel_id = await self._resolve_channel_id()
        if not channel_id:
            return []

        async with httpx.AsyncClient(timeout=30, headers=DEFAULT_HEADERS) as client:
            # RSS: exact dates for the latest 15 videos (no key, one request)
            rss_dates = await self._fetch_rss_dates(client, channel_id)
            # Paginated collection with both stop rules
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
                print(f"  - RSS: got exact dates for {len(rss_dates)} latest videos")
        except Exception as exc:
            print(f"  ! YouTube RSS error: {exc}")
        return rss_dates

    async def _fetch_all_via_api(self, client, channel_id, seen_shortcodes, rss_dates):
        """Fetch videos via the InnerTube API, newest first.

        Stops as soon as it hits either:
          - a post older than FIRST_SCAN_DAYS_LIMIT days (7-day cutoff), or
          - an already-seen video (incremental stop).
        Whichever triggers first wins. A post with a missing/unparseable
        date is kept (is_older_than_days() returns False) rather than
        discarded.
        """
        collected = []
        page = 0
        cont_token = None
        stop_reason = None
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
                    vid = v["video_id"]
                    if not vid:
                        continue

                    # Best available date for the 7-day check: exact RSS
                    # date first, else approximate from publishedTimeText.
                    # Shorts have no text, so this stays None for them.
                    published_at = rss_dates.get(vid) or self._approx_date_from_text(
                        v.get("published_text", "")
                    )

                    # Incremental stop: already in the database.
                    if vid in seen_shortcodes:
                        print(
                            f"  - Stopped early: hit already-seen video "
                            f"({vid}) on page {page}"
                        )
                        stop_reason = "seen"
                        break

                    # 7-day cutoff: older than the scan window.
                    if is_older_than_days(published_at, FIRST_SCAN_DAYS_LIMIT):
                        print(
                            f"  - Stopped early: video {vid} older than "
                            f"{FIRST_SCAN_DAYS_LIMIT} days (page {page})"
                        )
                        stop_reason = "age"
                        break

                    v["published_at"] = published_at
                    collected.append(v)

                if stop_reason:
                    break

                print(
                    f"  - Page {page}: {len(videos)} videos "
                    f"(collected: {len(collected)})"
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
        for v in collected:
            if v["video_id"] and v["video_id"] not in seen:
                seen.add(v["video_id"])
                unique.append(v)
        print(f"  - Unique in-window videos: {len(unique)}")

        # Enrich: exact publish dates for the collected in-window subset
        # only. RSS dates are already exact; the approximate
        # publishedTimeText date was used solely for the stop decision.
        print("  - Fetching exact publish dates for collected videos...")
        posts = []
        dated_count = 0
        for i, v in enumerate(unique):
            video_id = v["video_id"]
            title = v["title"]
            published_at = rss_dates.get(video_id)
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

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _approx_date_from_text(text):
        """Parse relative text like '2 days ago' / '3 weeks ago' into an
        approximate ISO datetime in UTC.

        Used ONLY to evaluate the 7-day stop cutoff for regular videos not
        covered by the RSS feed. Returns None if unparseable, in which case
        is_older_than_days() returns False and the post is kept.
        """
        if not text:
            return None
        m = RELATIVE_DATE_RE.search(text)
        if not m:
            return None
        n = int(m.group(1))
        unit = m.group(2).lower()
        days = n * _RELATIVE_DAYS.get(unit, 0)
        dt = datetime.now(timezone.utc) - timedelta(days=days)
        return dt.isoformat()

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
                    return YouTubeMonitor._parse_date_text(m.group(1))
                # Fallback: look for "uploadDate" in JSON-LD
                m = re.search(r'"uploadDate"\s*:\s*"([^"]+)"', html)
                if m:
                    return YouTubeMonitor._normalize_datetime(m.group(1))
                return None
            except Exception as exc:
                if attempt == 2:
                    print(f"    ! error on {video_id}: {exc}")
                await asyncio.sleep(2)
        return None

    @staticmethod
    def _parse_date_text(text):
        """Parse YouTube date text like 'Jun 23, 2026' to ISO format."""
        for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"):
            try:
                dt = datetime.strptime(text.strip(), fmt)
                return dt.replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                continue
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
