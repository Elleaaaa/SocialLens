# main.py
"""Entry point for the social media monitor."""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from playwright.async_api import async_playwright

from config import (
    BROWSER_HEADLESS,
    BROWSER_SLOW_MO,
    PAGE_TIMEOUT_MS,
    DELAY_BETWEEN_PROFILES_SEC,
    USER_AGENT,
    INSTAGRAM_STATE_FILE,
    FB_SESSION_FILE,
    THREADS_STATE_FILE,
    SCAN_TIMEOUT_SEC,
)
from storage import get_profiles
from storage import get_conn, init_db, is_post_seen, save_post, save_metric, log_run
from monitors import get_monitor
import scan_state


async def interactive_login(platform):
    """Open a visible browser for manual login."""
    if platform == "instagram":
        login_url = "https://www.instagram.com/accounts/login/"
        state_file = INSTAGRAM_STATE_FILE
        print("=== Instagram Login ===")
    elif platform == "facebook":
        login_url = "https://www.facebook.com/login.php"
        state_file = FB_SESSION_FILE
        print("=== Facebook Login ===")
    elif platform == "threads":
        login_url = "https://www.threads.net/login"
        state_file = THREADS_STATE_FILE
        print("=== Threads Login ===")
        print("(Uses your Instagram account credentials)")
    else:
        print(f"Unknown platform: {platform}")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=BROWSER_SLOW_MO,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                locale="en-US",
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            await page.goto(login_url, wait_until="domcontentloaded")
            print(f"\nBrowser opened to {login_url}")
            print("Log in to your account in the browser window.")
            print("When you see your feed/home page, come back here and press Enter.")

            # input() is blocking — run in executor to avoid stalling
            # the event loop if other async tasks are running.
            await asyncio.get_event_loop().run_in_executor(None, input)

            state = await context.storage_state()
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
            print(f"\nSession saved to: {state_file}")
            print("You can now run: python main.py --once")
        finally:
            await browser.close()


# main.py
async def scan_profile(page, profile, conn):
    """Scan a single profile. Returns (new_count, followers)."""
    monitor = get_monitor(profile["platform"], page, profile)
    print(f"\n→ Scanning {profile['name']} ({profile['platform']}) — {profile['url']}")

    followers = await monitor.fetch_stats()
    print(f"  ◷ followers: {followers:,}")

    seen_shortcodes = set()
    try:
        rows = conn.execute(
            "SELECT post_id FROM posts WHERE profile_id=?",
            (profile["id"],),
        ).fetchall()
        seen_shortcodes = {row["post_id"] for row in rows}
        if seen_shortcodes:
            print(
                f"  - {len(seen_shortcodes)} posts already in database "
                f"(will stop early when hitting them)"
            )
    except Exception as exc:
        print(f"  ! Warning: could not load seen posts: {exc}")

    posts = await monitor.fetch_posts(seen_shortcodes=seen_shortcodes)

    new_count = 0
    for post in posts:
        post_id = post.get("post_id", post["url"])
        try:
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
        except Exception as exc:
            print(f"  ! Error saving post {post_id}: {exc}")

    try:
        save_metric(conn, profile["id"], profile["platform"], followers)
    except Exception as exc:
        print(f"  ! Error saving metric: {exc}")

    print(f"  • {len(posts)} posts found, {new_count} new")
    if new_count > 0:
        print(
            f"[ALERT] {profile['name']} ({profile['platform']}) — {new_count} new post(s)"
        )
    return new_count, followers


async def run_scan():
    profiles = get_profiles()
    conn = get_conn()
    print(f"=== Scan started at {datetime.now(timezone.utc).isoformat()} ===")

    # Initialize the structured progress snapshot (all profiles pending).
    scan_state.init_progress(profiles)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=BROWSER_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            for i, profile in enumerate(profiles):
                # Graceful cancel: stop before starting the next profile.
                if scan_state.is_cancelled():
                    print("  ! Scan cancelled by user — stopping.")
                    scan_state.set_cancelled_remaining(i)
                    break

                scan_state.set_scanning(i)

                # Select session file based on platform
                state_file = None
                if profile["platform"] == "instagram":
                    state_file = INSTAGRAM_STATE_FILE
                elif profile["platform"] == "facebook":
                    state_file = FB_SESSION_FILE
                elif profile["platform"] == "threads":
                    state_file = THREADS_STATE_FILE

                # Create a fresh context per profile with the correct
                # session loaded (cookies + localStorage via storage_state).
                context_kwargs = dict(
                    user_agent=USER_AGENT,
                    viewport={"width": 1280, "height": 800},
                )
                if state_file and os.path.exists(state_file):
                    context_kwargs["storage_state"] = state_file

                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                try:
                    new_count, followers = await asyncio.wait_for(
                        scan_profile(page, profile, conn),
                        timeout=SCAN_TIMEOUT_SEC,
                    )
                    scan_state.set_completed(
                        i, new_posts=new_count, followers=followers
                    )
                except asyncio.TimeoutError:
                    print(f"  ! Timeout scanning {profile['name']}")
                    scan_state.set_failed(i, Exception("scan timeout"))
                except Exception as exc:
                    print(f"  ! Error scanning {profile['name']}: {exc}")
                    scan_state.set_failed(i, exc)
                finally:
                    await page.close()
                    await context.close()

                await asyncio.sleep(DELAY_BETWEEN_PROFILES_SEC)
        finally:
            await browser.close()

    conn.close()
    print("=== Scan finished ===\n")


def main():
    parser = argparse.ArgumentParser(description="Social media monitor")
    parser.add_argument("--once", action="store_true", help="Run a single scan")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Interactive login for Instagram or Facebook",
    )
    parser.add_argument(
        "--platform",
        type=str,
        default="instagram",
        choices=["instagram", "facebook", "threads"],
        help="Platform to log in to (for --login)",
    )
    args = parser.parse_args()

    # Initialize database schema once at startup
    init_db()

    if args.login:
        asyncio.run(interactive_login(args.platform))
    elif args.once:
        asyncio.run(run_scan())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
