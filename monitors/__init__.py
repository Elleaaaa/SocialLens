# monitors/__init__.py
"""Platform monitor registry.

Each monitor is imported individually so that a broken import
(missing dependency, syntax error) does not disable all platforms.
"""

try:
    from monitors.instagram import InstagramMonitor
except ImportError as e:
    print(f"Warning: InstagramMonitor import failed: {e}")
    InstagramMonitor = None

try:
    from monitors.youtube import YouTubeMonitor
except ImportError as e:
    print(f"Warning: YouTubeMonitor import failed: {e}")
    YouTubeMonitor = None

try:
    from monitors.tiktok import TikTokMonitor
except ImportError as e:
    print(f"Warning: TikTokMonitor import failed: {e}")
    TikTokMonitor = None

try:
    from monitors.facebook import FacebookMonitor
except ImportError as e:
    print(f"Warning: FacebookMonitor import failed: {e}")
    FacebookMonitor = None

try:
    from monitors.threads import ThreadsMonitor
except ImportError as e:
    print(f"Warning: ThreadsMonitor import failed: {e}")
    ThreadsMonitor = None


_MONITOR_MAP = {}
for _name, _cls in [
    ("instagram", InstagramMonitor),
    ("youtube", YouTubeMonitor),
    ("tiktok", TikTokMonitor),
    ("facebook", FacebookMonitor),
    ("threads", ThreadsMonitor),
]:
    if _cls is not None:
        _MONITOR_MAP[_name] = _cls


def get_monitor(platform, page, profile):
    """Return a monitor instance for the given platform.

    Raises ValueError if the platform is not registered or its import
    failed at startup.
    """
    cls = _MONITOR_MAP.get(platform)
    if not cls:
        valid = ", ".join(sorted(_MONITOR_MAP.keys()))
        raise ValueError(
            f"No monitor registered for platform: {platform}. "
            f"Valid platforms: {valid}"
        )
    return cls(page, profile)
