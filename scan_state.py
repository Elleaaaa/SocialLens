"""Shared scan state: structured progress tracker + graceful cancel flag.

Imported by server.py (REST endpoints) and main.py (status updates
during the scan loop). No WebSocket or stdout capture needed —
main.py calls the update functions directly.
"""

import copy
import threading

# Graceful cancel flag (checked between profiles in run_scan)
scan_cancel = threading.Event()

_lock = threading.Lock()
_progress = {"running": False, "profiles": []}


def init_progress(profiles):
    """Initialize the progress snapshot: all profiles pending (in queue)."""
    with _lock:
        _progress["running"] = True
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
