"""Base platform monitor. Add a new platform by subclassing this."""
import re
from abc import ABC, abstractmethod


def parse_count(text):
    """Convert '1.23M', '5,678', '12K' into an int."""
    if not text:
        return 0
    text = text.strip().lower().replace(",", "").replace(" ", "")
    m = re.match(r"([\d.]+)\s*([kmb]?)", text)
    if not m:
        return 0
    num = float(m.group(1))
    mult = {"k": 1e3, "m": 1e6, "b": 1e9, "": 1}.get(m.group(2), 1)
    return int(num * mult)


class BaseMonitor(ABC):
    platform = "base"
    metric_label = "followers"

    def __init__(self, page, profile):
        self.page = page
        self.profile = profile
        self.followers = 0

    @abstractmethod
    async def fetch_posts(self):
        """Scrape the profile page and return a list of post dicts."""
        ...

    async def fetch_stats(self):
        """Override to scrape follower/subscriber count. Returns int."""
        return 0

    def make_post(self, post_id, title, url, published_at=None):
        return {
            "post_id": str(post_id),
            "title": title or "",
            "url": url or "",
            "published_at": published_at,
        }