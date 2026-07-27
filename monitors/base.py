"""Base monitor class and shared utilities."""

import re
from datetime import datetime, timezone, timedelta


def parse_count(text):
    """Parse a count string like '1.2K' or '3,456' into an int."""
    if isinstance(text, (int, float)):
        return int(text)
    if not text:
        return 0
    s = str(text).strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([KMBkmb]?)", s)
    if not m:
        return 0
    num = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    if suffix in multipliers:
        num *= multipliers[suffix]
    return int(num)


def is_older_than_days(iso_date, days=7):
    """Check if a post date is older than N days from now.

    Returns False if the date is missing or unparseable (so we keep
    collecting rather than stopping on an unknown date).
    """
    if not iso_date:
        return False
    try:
        s = iso_date
        if "T" not in s and " " in s:
            s = s.replace(" ", "T")
        if not s.endswith("Z") and "+" not in s:
            s += "+00:00"
        dt = datetime.fromisoformat(s)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return dt < cutoff
    except (ValueError, TypeError):
        return False


class BaseMonitor:
    """Base class for all platform monitors."""

    platform = "unknown"
    metric_label = "followers"

    def __init__(self, page, profile):
        self.page = page
        self.profile = profile
        self.followers = 0
        self._logged_in = False

    def make_post(self, post_id, title, url, published_at):
        """Create a post dict in the standard format."""
        return {
            "post_id": post_id,
            "title": title,
            "url": url,
            "published_at": published_at,
        }

    async def fetch_stats(self):
        """Override in subclass. Returns follower count."""
        return 0

    async def fetch_posts(self, seen_shortcodes=None):
        """Override in subclass. Returns list of post dicts."""
        return []
