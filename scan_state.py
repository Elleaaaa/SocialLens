"""Shared scan state: structured progress tracker + graceful cancel flag.

Imported by server.py (REST endpoints) and main.py (status updates
during the scan loop). No WebSocket or stdout capture needed —
main.py calls the update functions directly.

Resume behavior
--------------
When a scan is stopped (cancelled, timed out, or crashed) and then
re-triggered, init_progress() keeps the "completed" profiles and
re-queues the incomplete ones (pending / scanning / failed / cancelled)
so the scan resumes from the first non-completed profile instead of
starting over from index 0. Profiles are matched by (name, platform);
if the profile list changed since the interrupted run, a fresh scan
starts automatically. Pass fresh=True to force a full re-scan.
"""

import copy
import threading

# Graceful cancel flag (checked between profiles in run_scan)
scan_cancel = threading.Event()

_lock = threading.Lock()
_progress = {"running": False, "profiles": []}


def init_progress(profiles, fresh=False):
    """Initialize the progress snapshot.

    Resume mode (default, fresh=False): if a previous interrupted scan
    exists whose profile list matches (by name + platform) and at least
    one profile is not yet completed, keep the completed entries and
    re-queue every incomplete entry as "pending". This lets run_scan()
    skip already-completed profiles and resume from where it stopped.

    Fresh mode (fresh=True) or when no resumable state exists: reset
    every profile to "pending".
    """
    with _lock:
        existing = _progress.get("profiles", [])
        can_resume = (
            not fresh
            and bool(existing)
            and len(existing) == len(profiles)
            and all(
                existing[j]["name"] == profiles[j]["name"]
                and existing[j]["platform"] == profiles[j]["platform"]
                for j in range(len(profiles))
            )
            and any(p["status"] != "completed" for p in existing)
        )

        if can_resume:
            for prev in existing:
                if prev["status"] != "completed":
                    prev["status"] = "pending"
                    prev["error"] = None
                    prev["new_posts"] = 0
                    # keep followers snapshot if previously captured
            _progress["profiles"] = existing
        else:
            _progress["profiles"] = [
                {
                    "name": p["name"],
                    "platform": p["platform"],
                    "status": "pending",
                    "error": None,
                    "new_posts": 0,
                    "followers": None,
                }
                for p in profiles
            ]

        _progress["running"] = True


def get_resume_index():
    """Return the index of the first non-completed profile.

    run_scan() starts its loop at this index so already-completed
    profiles are skipped on resume. Returns len(profiles) when every
    profile is completed (nothing left to do).
    """
    with _lock:
        for i, p in enumerate(_progress["profiles"]):
            if p["status"] != "completed":
                return i
        return len(_progress["profiles"])


def set_scanning(index):
    with _lock:
        profs = _progress["profiles"]
        if 0 <= index < len(profs):
            profs[index]["status"] = "scanning"


def set_completed(index, new_posts=0, followers=None):
    with _lock:
        profs = _progress["profiles"]
        if 0 <= index < len(profs):
            profs[index]["status"] = "completed"
            profs[index]["new_posts"] = new_posts
            if followers is not None:
                profs[index]["followers"] = followers


def set_failed(index, error):
    with _lock:
        profs = _progress["profiles"]
        if 0 <= index < len(profs):
            profs[index]["status"] = "failed"
            profs[index]["error"] = str(error)[:200]


def set_cancelled_remaining(start_index):
    """Mark all still-pending profiles from start_index as cancelled."""
    with _lock:
        for p in _progress["profiles"][start_index:]:
            if p["status"] == "pending":
                p["status"] = "cancelled"


def finish_progress():
    with _lock:
        _progress["running"] = False


def clear_progress():
    with _lock:
        _progress["running"] = False
        _progress["profiles"] = []


def get_progress():
    with _lock:
        return copy.deepcopy(_progress)


def request_cancel():
    scan_cancel.set()


def reset_cancel():
    scan_cancel.clear()


def is_cancelled():
    return scan_cancel.is_set()
