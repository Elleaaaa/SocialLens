"""Shared scan state: structured progress tracker + graceful cancel flag.

Imported by server.py (REST endpoints) and main.py (status updates
during the scan loop).

Resume behavior
--------------
When a scan is stopped (cancelled, timed out, or crashed) and then
re-triggered, init_progress() keeps the "completed" profiles and
re-queues the incomplete ones (pending / scanning / failed / cancelled)
so the scan resumes from the first non-completed profile instead of
starting over from index 0. Profiles are matched by profile_id; if the
profile set changed since the interrupted run, a fresh scan starts
automatically. Pass fresh=True to force a full re-scan.

DB persistence
--------------
The full snapshot is persisted to the scan_sessions table on every
status transition so that a backend crash/restart can still resume.
On startup, _load_latest_session() restores the last session and
resets any "scanning" status back to "pending" (interrupted mid-flight
can't be trusted).
"""

import copy
import json
import threading
import uuid
from datetime import datetime, timezone

# Graceful cancel flag (checked between profiles in run_scan)
scan_cancel = threading.Event()

_lock = threading.Lock()
_progress = {
    "running": False,
    "session_id": None,
    "created_at": None,
    "profiles": [],
}


def _now():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# DB persistence (best-effort; scans are slow so write overhead is
# negligible)
# ------------------------------------------------------------------


def _persist():
    """Write the current snapshot to the scan_sessions table."""
    try:
        from storage import get_conn

        with _lock:
            data = copy.deepcopy(_progress)

        if not data["session_id"]:
            return

        finished = (
            1
            if not data["running"]
            and data["profiles"]
            and all(p["status"] == "completed" for p in data["profiles"])
            else 0
        )

        conn = get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO scan_sessions
               (id, created_at, running, finished, snapshot)
               VALUES (?, ?, ?, ?, ?)""",
            (
                data["session_id"],
                data["created_at"] or _now(),
                1 if data["running"] else 0,
                finished,
                json.dumps(data),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"  [scan_state] persist failed: {exc}")


def _load_latest_session():
    """Restore the latest scan session from DB on startup.

    Resets any "scanning" status to "pending" — an interrupted
    mid-flight profile can't be trusted and should be retried.
    """
    global _progress
    try:
        from storage import get_conn

        conn = get_conn()
        row = conn.execute(
            "SELECT snapshot FROM scan_sessions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or not row["snapshot"]:
            return
        data = json.loads(row["snapshot"])
        # Reset scanning -> pending (crash recovery)
        for p in data.get("profiles", []):
            if p["status"] == "scanning":
                p["status"] = "pending"
        data["running"] = False  # server just started, nothing running
        _progress = data
        print(f"  [scan_state] Restored session {data.get('session_id')}")
    except Exception as exc:
        print(f"  [scan_state] load latest failed: {exc}")


# Load on import (startup)
_load_latest_session()


# ------------------------------------------------------------------
# Progress lifecycle
# ------------------------------------------------------------------


def init_progress(profiles, fresh=False):
    """Initialize or resume the progress snapshot.

    fresh=True → all profiles pending (full re-scan from index 0).
    fresh=False (resume) → if a previous interrupted session exists
    whose profile list matches by profile_id AND at least one profile
    is incomplete, KEEP the completed entries and re-queue the rest
    (pending / scanning / failed / cancelled) back to pending. This
    ensures resume starts from the last account that was being
    scanned (or failed) before the interruption — never skipping
    past it, and never re-scanning accounts already completed in
    this session.
    """
    with _lock:
        existing = _progress.get("profiles", [])
        can_resume = (
            not fresh
            and bool(existing)
            and len(existing) == len(profiles)
            and all(
                existing[j].get("profile_id") == profiles[j].get("id")
                for j in range(len(profiles))
            )
            and any(p["status"] != "completed" for p in existing)
        )

        if can_resume:
            # Resume: preserve completed, re-queue EVERY other status
            # (including 'scanning' left by a mid-flight interruption
            # and 'failed') back to pending so they are retried.
            for prev in existing:
                if prev["status"] != "completed":
                    prev["status"] = "pending"
                    prev["error"] = None
                    prev["new_posts"] = 0
                    # keep followers snapshot if previously captured
            _progress["profiles"] = existing
            # Keep existing session_id + created_at
        else:
            # Fresh session
            _progress["session_id"] = uuid.uuid4().hex[:12]
            _progress["created_at"] = _now()
            _progress["profiles"] = [
                {
                    "profile_id": p.get("id"),
                    "name": p["name"],
                    "platform": p["platform"],
                    "company": p.get("company", ""),
                    "status": "pending",
                    "error": None,
                    "new_posts": 0,
                    "followers": None,
                }
                for p in profiles
            ]

        _progress["running"] = True

    _persist()


def get_resume_index():
    """Return the index of the first non-completed profile.

    This is the profile that was being scanned (or failed, or queued)
    at the moment of interruption — resume starts HERE, re-scanning
    this profile, not the one after it. Completed profiles before it
    are skipped. Returns len(profiles) when every profile is completed.
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
    # NOTE: do not persist on 'scanning' — it's a transient state.
    # If the process dies here, the in-memory 'scanning' status is
    # recovered by _load_latest_session() on next startup (reset to
    # 'pending'), and the resume logic re-queues it.


def set_completed(index, new_posts=0, followers=None):
    with _lock:
        profs = _progress["profiles"]
        if 0 <= index < len(profs):
            profs[index]["status"] = "completed"
            profs[index]["new_posts"] = new_posts
            if followers is not None:
                profs[index]["followers"] = followers
    _persist()


def set_failed(index, error):
    with _lock:
        profs = _progress["profiles"]
        if 0 <= index < len(profs):
            profs[index]["status"] = "failed"
            profs[index]["error"] = str(error)[:200]
    _persist()


def set_cancelled_remaining(start_index):
    """Mark all profiles from start_index onward as cancelled.

    IMPORTANT: this marks EVERY profile from start_index to the end,
    INCLUDING the one currently in 'scanning' status. This is what
    captures the interrupted profile as the resume boundary — on the
    next resume, get_resume_index() returns this index and the
    profile is re-queued to 'pending' and re-scanned. Previously this
    only flipped 'pending' entries, which left a 'scanning' profile
    stranded and caused resume to start one profile too late.
    """
    with _lock:
        for p in _progress["profiles"][start_index:]:
            if p["status"] in ("pending", "scanning"):
                p["status"] = "cancelled"
    _persist()


def finish_progress():
    with _lock:
        _progress["running"] = False
    _persist()


def clear_progress():
    with _lock:
        _progress["running"] = False
        _progress["session_id"] = None
        _progress["created_at"] = None
        _progress["profiles"] = []
    _persist()


def get_progress():
    with _lock:
        return copy.deepcopy(_progress)


def request_cancel():
    scan_cancel.set()


def reset_cancel():
    scan_cancel.clear()


def is_cancelled():
    return scan_cancel.is_set()


def get_profile_status(index):
    """Return the status of the profile at the given index, or None."""
    with _lock:
        profs = _progress["profiles"]
        if 0 <= index < len(profs):
            return profs[index]["status"]
        return None
