# main.py
"""Entry point for the social media monitor."""

import argparse
import asyncio
import os
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from config import (
    BROWSER_HEADLESS,
    PAGE_TIMEOUT_MS,
    DELAY_BETWEEN_PROFILES_SEC,
    INSTAGRAM_STATE_FILE,
    FB_SESSION_FILE,
    THREADS_STATE_FILE,
    TIKTOK_STATE_FILE,
    SCAN_TIMEOUT_SEC,
    LOGIN_TIMEOUT_SEC,
    USER_AGENT,
)
from storage import (
    get_conn,
    get_profiles,
    is_post_seen,
    save_post,
    save_metric,
    log_run,
)
from monitors import get_monitor
import scan_state


async def interactive_login(playwright, platform):
    """Open a visible browser for one-time manual login."""
    if platform == "instagram":
        login_url = "https://www.instagram.com/accounts/login/"
    elif platform == "facebook":
        login_url = "https://www.facebook.com/login/"
    elif platform == "threads":
        login_url = "https://www.threads.net/login"
    elif platform == "tiktok":
        login_url = "https://www.tiktok.com/login"
    else:
        print(f"Unknown platform: {platform}")
        return

    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    try:
        context = await browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )

        # Stealth script to hide automation detection
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # Retry navigation (TikTok resets connections on first attempt)
        for attempt in range(3):
            try:
                await page.goto(login_url, wait_until="commit", timeout=30000)
                break
            except Exception as exc:
                if attempt < 2:
                    print(f"  - Connection retry {attempt + 1}...")
                    await asyncio.sleep(3)
                else:
                    print(f"  ! Could not load {login_url}: {exc}")
                    print("  ! Try opening the page manually in the browser window.")
                    # Navigate to a blank page so the browser stays open
                    await page.goto("about:blank")

        await page.wait_for_timeout(2000)
        print(f"Browser opened at {login_url}")
        print("Log in manually. Press Enter here when done.")

        import threading

        done = threading.Event()

        def _wait_input():
            input()
            done.set()

        threading.Thread(target=_wait_input, daemon=True).start()

        elapsed = 0
        while not done.is_set() and elapsed < LOGIN_TIMEOUT_SEC:
            await asyncio.sleep(1)
            elapsed += 1

        if done.is_set():
            if platform == "instagram":
                await context.storage_state(path=INSTAGRAM_STATE_FILE)
                print(f"Session saved to: {INSTAGRAM_STATE_FILE}")
            elif platform == "facebook":
                await context.storage_state(path=FB_SESSION_FILE)
                print(f"Session saved to: {FB_SESSION_FILE}")
            elif platform == "threads":
                await context.storage_state(path=THREADS_STATE_FILE)
                print(f"Session saved to: {THREADS_STATE_FILE}")
            elif platform == "tiktok":
                await context.storage_state(path=TIKTOK_STATE_FILE)
                print(f"Session saved to: {TIKTOK_STATE_FILE}")
            print("Login setup complete. You can now run: python main.py --once")
        else:
            print("Login timed out. Session was not saved.")
    finally:
        await browser.close()


async def scan_profile(page, profile, conn):
    """Scan a single profile for new posts and follower count."""
    monitor = get_monitor(profile["platform"], page, profile)
    print(f"\n→ Scanning {profile['name']} ({profile['platform']}) — {profile['url']}")

    # Fetch follower count
    followers = await monitor.fetch_stats()
    if followers:
        save_metric(conn, profile["id"], profile["platform"], followers)
        print(f"  ◷ {monitor.metric_label}: {followers:,}")

    # Get already-seen post IDs for incremental scan
    seen_shortcodes = set()
    try:
        rows = conn.execute(
            "SELECT post_id FROM posts WHERE profile_id=?",
            (profile["id"],),
        ).fetchall()
        seen_shortcodes = {row["post_id"] for row in rows if row["post_id"]}
    except Exception as exc:
        print(f"  ! Could not load seen posts: {exc}")

    if seen_shortcodes:
        print(
            f"  - {len(seen_shortcodes)} posts already in database (will stop early when hitting them)"
        )

    # Fetch new posts
    posts = await monitor.fetch_posts(seen_shortcodes=seen_shortcodes)

    # Save new posts
    new_count = 0
    for post in posts:
        post_id = post.get("post_id", post["url"])
        if not is_post_seen(conn, profile["id"], post_id):
            save_post(
                conn,
                profile["id"],
                profile["platform"],
                post_id,
                post.get("title", ""),
                post["url"],
                post.get("published_at"),
            )
            new_count += 1

    print(f"  • {len(posts)} posts found, {new_count} new")

    if new_count > 0:
        print(
            f"[ALERT] {profile['name']} ({profile['platform']}) — {new_count} new post(s)"
        )

    # Log the run
    log_run(conn, profile["id"], profile["platform"], "success", new_count)
    return new_count, followers


def _get_state_file(platform):
    """Return the session state file path for a platform, or None."""
    if platform == "instagram":
        return INSTAGRAM_STATE_FILE
    elif platform == "facebook":
        return FB_SESSION_FILE
    elif platform == "threads":
        return THREADS_STATE_FILE
    elif platform == "tiktok":
        return TIKTOK_STATE_FILE
    return None


async def run_scan(profile_ids=None, fresh=False):
    """Run a scan across profiles.

    profile_ids: optional list of profile IDs to scan (company filter).
                 If None, scans all profiles.
    fresh=False (default) resumes from the first non-completed profile:
    completed profiles are skipped, failed/cancelled/pending are retried.
    fresh=True forces a full re-scan from the beginning.
    """
    conn = get_conn()
    all_profiles = get_profiles()

    # Apply company filter: only scan the requested subset, preserving
    # the order they appear in the DB.
    if profile_ids is not None:
        id_set = set(profile_ids)
        profiles = [p for p in all_profiles if p["id"] in id_set]
    else:
        profiles = all_profiles

    print(f"=== Scan started at {datetime.now(timezone.utc).isoformat()} ===")
    print(f"  - {len(profiles)} profile(s) in this scan")
    scan_state.init_progress(profiles, fresh=fresh)

    start_index = scan_state.get_resume_index()
    if start_index > 0 and not fresh:
        print(
            f"  - Resuming from profile #{start_index + 1} "
            f"({start_index} already completed)"
        )
    elif start_index == len(profiles):
        print("  - All profiles already completed; nothing to do")

    for i in range(start_index, len(profiles)):
        profile = profiles[i]
        # Skip profiles already completed in this session (resume).
        # This is the critical fix: get_resume_index() returns only the
        # FIRST non-completed profile, so the loop must individually
        # skip any completed profiles that come after a failed/cancelled
        # one — otherwise accounts already scanned get re-scanned.
        if scan_state.get_profile_status(i) == "completed":
            continue
        # Check for cancel before starting each profile
        if scan_state.is_cancelled():
            scan_state.set_cancelled_remaining(i)
            print("  - Scan cancelled by user")
            break
        scan_state.set_scanning(i)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                # Select session file based on platform
                state_file = _get_state_file(profile["platform"])

                # Create context with session state if available
                context_kwargs = {
                    "user_agent": USER_AGENT,
                    "locale": "en-US",
                    "viewport": {"width": 1280, "height": 800},
                }
                if state_file and os.path.exists(state_file):
                    context_kwargs["storage_state"] = state_file
                    print(f"  - Loaded session for {profile['platform']}")
                else:
                    print(f"  - No session file for {profile['platform']}")

                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                try:
                    new_count, followers = await asyncio.wait_for(
                        scan_profile(page, profile, conn),
                        timeout=SCAN_TIMEOUT_SEC,
                    )
                    # If cancel arrived during this profile's scan,
                    # treat the profile as cancelled (not completed) so
                    # resume re-scans it from the top.
                    if scan_state.is_cancelled():
                        scan_state.set_cancelled_remaining(i)
                    else:
                        scan_state.set_completed(i, new_count, followers)
                except asyncio.TimeoutError:
                    print(f"  ! Timeout scanning {profile['name']}")
                    scan_state.set_failed(i, Exception("scan timeout"))
                except Exception as exc:
                    print(f"  ! Error scanning {profile['name']}: {exc}")
                    scan_state.set_failed(i, exc)
            finally:
                await context.close()
                await page.close()

        if i < len(profiles) - 1:
            await asyncio.sleep(DELAY_BETWEEN_PROFILES_SEC)

    conn.close()
    scan_state.finish_progress()
    print("=== Scan finished ===\n")


def main():
    parser = argparse.ArgumentParser(description="Social media monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Interactive login for a platform",
    )
    parser.add_argument(
        "--platform",
        type=str,
        default="instagram",
        choices=["instagram", "facebook", "threads", "tiktok"],
        help="Platform for login (default: instagram)",
    )
    args = parser.parse_args()

    if args.login:
        asyncio.run(_do_login(args.platform))
    else:
        asyncio.run(run_scan())


async def _do_login(platform):
    async with async_playwright() as p:
        await interactive_login(p, platform)


if __name__ == "__main__":
    main()
