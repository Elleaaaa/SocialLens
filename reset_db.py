"""Wipe all detected posts, runs, and follower metrics. Keeps profiles.json intact."""

from config import DB_PATH
from storage import get_conn


def reset():
    conn = get_conn()
    conn.executescript("""
        DELETE FROM posts;
        DELETE FROM runs;
        DELETE FROM profile_metrics;
        DELETE FROM scan_sessions;
        DELETE FROM profiles;
        """)
    conn.commit()
    conn.close()
    print(f"✔ Cleared all data in {DB_PATH}")
    print("Run 'python server.py' and start a fresh scan.")


if __name__ == "__main__":
    reset()
