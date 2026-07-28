# storage.py
"""SQLite storage layer with publish-date filtering and follower metrics.

All data (profiles, posts, metrics, runs) lives in a single SQLite file.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import DB_PATH, LOCAL_TIMEZONE_OFFSET

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    company TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    post_id TEXT NOT NULL,
    title TEXT,
    url TEXT,
    detected_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(profile_id, post_id),
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    profile_id TEXT,
    platform TEXT,
    status TEXT,
    new_posts INTEGER DEFAULT 0,
    message TEXT
);

CREATE TABLE IF NOT EXISTS profile_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    followers INTEGER DEFAULT 0,
    captured_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE
);
"""

_initialized = False


def init_db():
    """Run schema migration once at startup."""
    global _initialized
    if _initialized:
        return
    conn = get_conn()
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(posts)")]
    if "published_at" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN published_at TEXT")
    # Add company column to existing profiles tables (no-op if already present)
    profile_cols = [row["name"] for row in conn.execute("PRAGMA table_info(profiles)")]
    if "company" not in profile_cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN company TEXT")
    _migrate_profiles_from_json(conn)
    conn.commit()
    conn.close()
    _initialized = True


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Profile CRUD
# ----------------------------------------------------------------------


def _migrate_profiles_from_json(conn):
    """Import profiles from profiles.json if the DB table is empty."""
    json_path = Path(DB_PATH).parent / "profiles.json"
    if not json_path or not json_path.exists():
        return

    count = conn.execute("SELECT COUNT(*) as c FROM profiles").fetchone()["c"]
    if count > 0:
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for p in data.get("profiles", []):
            conn.execute(
                "INSERT OR IGNORE INTO profiles (id, platform, name, url, company, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (p["id"], p["platform"], p["name"], p["url"], p.get("company"), _now()),
            )
        print(
            f"  [migration] Imported {len(data.get('profiles', []))} profiles from JSON to DB"
        )
    except Exception as exc:
        print(f"  [migration] Could not import profiles from JSON: {exc}")


def get_profiles():
    """Return all profiles as a list of dicts."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM profiles ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_profile(conn, profile_id):
    """Return a single profile by ID."""
    row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return dict(row) if row else None


def add_profile(conn, platform, name, url, company=""):
    """Create a new profile with an auto-generated ID."""
    new_id = f"{platform[:2]}-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "INSERT INTO profiles (id, platform, name, url, company, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (new_id, platform, name, url, company, _now()),
    )
    conn.commit()
    return new_id


def delete_profile(conn, profile_id):
    """Delete a profile. CASCADE removes its posts and metrics."""
    conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    conn.commit()


# ----------------------------------------------------------------------
# Posts
# ----------------------------------------------------------------------


def is_post_seen(conn, profile_id, post_id):
    return (
        conn.execute(
            "SELECT 1 FROM posts WHERE profile_id=? AND post_id=?",
            (profile_id, post_id),
        ).fetchone()
        is not None
    )


def save_post(conn, profile_id, platform, post_id, title, url, published_at=None):
    conn.execute(
        """INSERT INTO posts
           (profile_id, platform, post_id, title, url, detected_at, published_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(profile_id, post_id) DO UPDATE SET
               published_at = COALESCE(excluded.published_at, posts.published_at)""",
        (profile_id, platform, post_id, title, url, _now(), published_at),
    )
    conn.commit()


def get_posts_filtered(
    conn,
    from_date=None,
    to_date=None,
    platform=None,
    profile_id=None,
    search=None,
    sort_by="published_at",
    sort_dir="desc",
    limit=20,
    offset=0,
    detected_since=None,
):
    """Get posts with full filtering, sorting, and pagination.

    detected_since: ISO 8601 timestamp; only posts whose detected_at
    is >= this value are returned. Used by the scan-results modal to
    fetch exactly the posts inserted during the current scan, instead
    of diffing a capped baseline snapshot.
    """
    conds, params = [], []

    offset_expr = f"+{LOCAL_TIMEZONE_OFFSET} hours"
    if from_date:
        conds.append(
            "date(datetime(substr(COALESCE(p.published_at, p.detected_at),1,19), ?)) >= ?"
        )
        params.extend([offset_expr, from_date])
    if to_date:
        conds.append(
            "date(datetime(substr(COALESCE(p.published_at, p.detected_at),1,19), ?)) <= ?"
        )
        params.extend([offset_expr, to_date])
    if platform:
        conds.append("pr.platform = ?")
        params.append(platform)
    if profile_id:
        conds.append("p.profile_id = ?")
        params.append(profile_id)
    if search:
        conds.append("(p.title LIKE ? OR p.url LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if detected_since:
        conds.append("p.detected_at >= ?")
        params.append(detected_since)

    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    valid_sorts = {"published_at", "detected_at", "title", "profile_id"}
    sort_col = sort_by if sort_by in valid_sorts else "published_at"
    sort_direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    if sort_col == "published_at":
        sort_expr = "COALESCE(p.published_at, p.detected_at)"
    elif sort_col == "profile_id":
        sort_expr = "pr.name"
    else:
        sort_expr = f"p.{sort_col}"

    limit = min(int(limit or 20), 100000)
    offset = max(int(offset or 0), 0)

    query = f"""
        SELECT p.*, pr.name as profile_name, pr.platform as profile_platform,
               pr.company as profile_company
        FROM posts p
        LEFT JOIN profiles pr ON p.profile_id = pr.id
        {where}
        ORDER BY {sort_expr} {sort_direction}
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(query, params + [limit, offset]).fetchall()
    return [dict(r) for r in rows]


def get_posts_count(
    conn,
    from_date=None,
    to_date=None,
    platform=None,
    profile_id=None,
    search=None,
    detected_since=None,
):
    """Get total count of posts matching the filter (for pagination)."""
    conds, params = [], []

    offset_expr = f"+{LOCAL_TIMEZONE_OFFSET} hours"
    if from_date:
        conds.append(
            "date(datetime(substr(COALESCE(p.published_at, p.detected_at),1,19), ?)) >= ?"
        )
        params.extend([offset_expr, from_date])
    if to_date:
        conds.append(
            "date(datetime(substr(COALESCE(p.published_at, p.detected_at),1,19), ?)) <= ?"
        )
        params.extend([offset_expr, to_date])
    if platform:
        conds.append("pr.platform = ?")
        params.append(platform)
    if profile_id:
        conds.append("p.profile_id = ?")
        params.append(profile_id)
    if search:
        conds.append("(p.title LIKE ? OR p.url LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if detected_since:
        conds.append("p.detected_at >= ?")
        params.append(detected_since)

    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    query = f"""
        SELECT COUNT(*) as cnt FROM posts p
        LEFT JOIN profiles pr ON p.profile_id = pr.id
        {where}
    """
    row = conn.execute(query, params).fetchone()
    return row["cnt"] if row else 0


def get_distinct_platforms(conn):
    """Get list of platforms that have profiles."""
    rows = conn.execute(
        "SELECT DISTINCT platform FROM profiles ORDER BY platform"
    ).fetchall()
    return [r["platform"] for r in rows]


def get_recent_posts(conn, from_date=None, to_date=None, limit=50):
    clause, params = _date_filter(from_date, to_date)
    rows = conn.execute(
        f"""SELECT platform, profile_id, title, url, detected_at, published_at
            FROM posts{clause}
            ORDER BY COALESCE(published_at, detected_at) DESC LIMIT ?""",
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# Metrics & Runs
# ----------------------------------------------------------------------


def save_metric(conn, profile_id, platform, followers):
    conn.execute(
        """INSERT INTO profile_metrics (profile_id, platform, followers, captured_at)
           VALUES (?, ?, ?, ?)""",
        (profile_id, platform, int(followers or 0), _now()),
    )
    conn.commit()


def get_latest_metric(conn, profile_id):
    row = conn.execute(
        "SELECT followers FROM profile_metrics WHERE profile_id=? ORDER BY captured_at DESC LIMIT 1",
        (profile_id,),
    ).fetchone()
    return row["followers"] if row else 0


def log_run(conn, profile_id, platform, status, new_posts=0, message=""):
    conn.execute(
        """INSERT INTO runs (started_at, profile_id, platform, status, new_posts, message)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (_now(), profile_id, platform, status, new_posts, message),
    )
    conn.commit()


def _date_filter(from_date, to_date):
    """Filter by publish date in local timezone."""
    offset_expr = f"+{LOCAL_TIMEZONE_OFFSET} hours"
    conds, params = [], []
    if from_date:
        conds.append(
            "date(datetime(substr(COALESCE(published_at, detected_at),1,19), ?)) >= ?"
        )
        params.extend([offset_expr, from_date])
    if to_date:
        conds.append(
            "date(datetime(substr(COALESCE(published_at, detected_at),1,19), ?)) <= ?"
        )
        params.extend([offset_expr, to_date])
    clause = (" WHERE " + " AND ".join(conds)) if conds else ""
    return clause, params


def get_profile_stats(conn, from_date=None, to_date=None):
    clause, params = _date_filter(from_date, to_date)
    stats = {}
    rows = conn.execute(
        f"SELECT profile_id, COUNT(*) AS total FROM posts{clause} GROUP BY profile_id",
        params,
    ).fetchall()
    for r in rows:
        stats[r["profile_id"]] = {"total": r["total"], "last_run": None}
    run_rows = conn.execute(
        "SELECT profile_id, MAX(finished_at) AS last_run FROM runs GROUP BY profile_id"
    ).fetchall()
    for r in run_rows:
        pid = r["profile_id"]
        stats.setdefault(pid, {"total": 0, "last_run": None})
        stats[pid]["last_run"] = r["last_run"]
    return stats


def get_overall_stats(conn, from_date=None, to_date=None):
    clause, params = _date_filter(from_date, to_date)
    total_posts = conn.execute(
        f"SELECT COUNT(*) AS c FROM posts{clause}", params
    ).fetchone()["c"]
    last_run = conn.execute("SELECT MAX(started_at) AS last FROM runs").fetchone()[
        "last"
    ]
    total_followers = conn.execute(
        """SELECT COALESCE(SUM(followers),0) AS c FROM profile_metrics
           WHERE id IN (SELECT MAX(id) FROM profile_metrics GROUP BY profile_id)"""
    ).fetchone()["c"]
    return {
        "total_posts": total_posts,
        "total_followers": total_followers,
        "last_run": last_run,
    }


def get_recent_runs(conn, limit=50):
    rows = conn.execute(
        """SELECT started_at, finished_at, profile_id, platform, status, new_posts, message
           FROM runs ORDER BY started_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
