"""Seed a fresh monitor.db with the 55 profiles. Run once: python seed_db.py"""

import sqlite3
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent / "monitor.db"

# Remove old db if exists (fresh start)
if DB_PATH.exists():
    DB_PATH.unlink()

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
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
    UNIQUE(profile_id, post_id)
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
    captured_at TEXT NOT NULL
);
"""

PROFILES = [
    ("instagram", "ASHAD", "https://www.instagram.com/wellness.with.zay/"),
    ("instagram", "ASHAD 1", "https://www.instagram.com/wellness.with.roman/"),
    ("facebook", "ASHAD 2", "https://www.facebook.com/profile.php?id=61590840391573"),
    ("facebook", "REED", "https://www.facebook.com/tang.wellness/"),
    ("instagram", "EDWA", "https://www.instagram.com/health.zora/"),
    ("facebook", "EDWA 1", "https://www.facebook.com/profile.php?id=61590624242714"),
    ("instagram", "IMRAN", "https://www.instagram.com/hollywood.protocol/"),
    ("facebook", "IMRAN 1", "https://www.facebook.com/profile.php?id=61590124923340"),
    ("instagram", "NASAR", "https://www.instagram.com/livelikenasir/"),
    ("instagram", "BROOKE HOCKETT", "https://www.instagram.com/motherrosewellness/"),
    (
        "facebook",
        "BROOKE HOCKETT 1",
        "https://www.facebook.com/profile.php?id=61589778833463",
    ),
    (
        "facebook",
        "BROOKE HOCKETT 2",
        "https://www.facebook.com/profile.php?id=61575589384048",
    ),
    (
        "facebook",
        "BROOKE HOCKETT 3",
        "https://www.facebook.com/profile.php?id=61573298739916",
    ),
    ("instagram", "KIAI KAWAI", "https://www.instagram.com/therealmamasachi/"),
    (
        "facebook",
        "KIAI KAWAI 1",
        "https://www.facebook.com/profile.php?id=61585647120727",
    ),
    ("facebook", "ANDREW", "https://www.facebook.com/Michaelparkwellness/"),
    ("instagram", "MAL", "https://www.instagram.com/harmoncoastal/"),
    ("facebook", "MAL 1", "https://www.facebook.com/harmoncoastal/"),
    ("instagram", "MIRANDA", "https://www.instagram.com/80yo_yogi/"),
    ("facebook", "MIRANDA 1", "https://www.facebook.com/profile.php?id=61590373750107"),
    ("tiktok", "STEFAN", "https://www.tiktok.com/@minjaehanmd"),
    ("instagram", "STEFAN 1", "https://www.instagram.com/thehanmethod/"),
    ("facebook", "STEFAN 2", "https://www.facebook.com/profile.php?id=61576422863379"),
    ("instagram", "DK", "https://www.instagram.com/donovanasaju/"),
    ("facebook", "DK 1", "https://www.facebook.com/profile.php?id=61590635937360"),
    ("facebook", "PAOLO", "https://www.facebook.com/profile.php?id=61590091424227"),
    ("instagram", "WILLYBOY", "https://www.instagram.com/yoder.wellness/"),
    ("facebook", "WILLYBOY 1", "https://www.facebook.com/Eli.Ruth.Wellness/"),
    ("instagram", "DYLAN H", "https://www.instagram.com/wellnesswithdemarcus/"),
    ("threads", "DYLAN H 1", "https://www.threads.net/@wellnesswithdemarcus"),
    ("facebook", "DYLAN H 2", "https://www.facebook.com/profile.php?id=61590876801483"),
    ("facebook", "GIORGIO", "https://www.facebook.com/profile.php?id=61590214143548"),
    ("tiktok", "SIMONGP", "https://www.tiktok.com/@hamilton.ye"),
    ("instagram", "SIMONGP 1", "https://www.instagram.com/haniltonye/"),
    ("facebook", "SIMONGP 2", "https://www.facebook.com/profile.php?id=61591973103190"),
    ("tiktok", "CHRISTIANCLARK", "https://www.tiktok.com/@auntie.nia.health"),
    ("instagram", "CHRISTIANCLARK 1", "https://www.instagram.com/auntie.nia.health/"),
    ("threads", "CHRISTIANCLARK 2", "https://www.threads.net/@auntie.nia.health"),
    (
        "facebook",
        "CHRISTIANCLARK 3",
        "https://www.facebook.com/profile.php?id=61590419322281",
    ),
    ("facebook", "BRENDEN", "https://www.facebook.com/profile.php?id=61590653731098"),
    ("tiktok", "ADAM", "https://www.tiktok.com/@naomi.wellness60"),
    ("instagram", "ADAM 1", "https://www.instagram.com/naomi.wellness60/"),
    ("facebook", "ADAM 2", "https://www.facebook.com/profile.php?id=61572140008596"),
    ("instagram", "MPMPMPMP", "https://www.instagram.com/wellnessgroveusa/"),
    (
        "facebook",
        "MPMPMPMP 1",
        "https://www.facebook.com/profile.php?id=61590712073332",
    ),
    ("tiktok", "CRIS", "https://www.tiktok.com/@lee.sinmed"),
    ("tiktok", "CRIS 1", "https://www.tiktok.com/@sinchanmed"),
    ("instagram", "CRIS 2", "https://www.instagram.com/lee.sinmed/"),
    ("instagram", "CRIS 3", "https://www.instagram.com/sinchan.med/"),
    ("facebook", "CRIS 4", "https://www.facebook.com/profile.php?id=61590665366018"),
    ("instagram", "EDUARDOD", "https://www.instagram.com/mike.navarro_/"),
    ("instagram", "EDUARDOD 1", "https://www.instagram.com/earlinejones_/"),
    (
        "facebook",
        "EDUARDOD 2",
        "https://www.facebook.com/profile.php?id=61590296025852",
    ),
    (
        "facebook",
        "EDUARDOD 3",
        "https://www.facebook.com/profile.php?id=61591164353728",
    ),
    (
        "facebook",
        "EDUARDOD 4",
        "https://www.facebook.com/profile.php?id=61590521559560",
    ),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    for i, (platform, name, url) in enumerate(PROFILES, 1):
        # ID format: {platform_prefix}-{index}-{name_prefix}
        profile_id = f"{platform[:2]}-{i}-{name[:3].lower().replace(' ', '')}"
        conn.execute(
            "INSERT INTO profiles (id, platform, name, url) VALUES (?, ?, ?, ?)",
            (profile_id, platform, name, url),
        )

    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    print(f"Created fresh monitor.db with {count} profiles")
    print("(posts, runs, profile_metrics tables are empty)")

    # Show summary
    for row in conn.execute(
        "SELECT platform, COUNT(*) as c FROM profiles GROUP BY platform ORDER BY platform"
    ):
        print(f"  {row['platform']}: {row['c']}")

    conn.close()


if __name__ == "__main__":
    main()
