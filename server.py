# server.py
"""FastAPI web server for the Social Monitor dashboard."""

import asyncio
import csv
import io
import os
import threading
import httpx
from fastapi.responses import Response
from config import NOVNC_PORT
from datetime import datetime as dt
from pathlib import Path
from contextlib import asynccontextmanager
from starlette.middleware.base import BaseHTTPMiddleware

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import scan_state
from storage import (
    get_conn,
    init_db,
    get_overall_stats,
    get_profile_stats,
    get_profiles,
    get_latest_metric,
    get_posts_filtered,
    get_posts_count,
    get_distinct_platforms,
    add_profile,
    delete_profile,
)

from login_server import (
    start_login_session,
    complete_login_session,
    cancel_login_session,
    get_session_status,
)

from config import (
    AUTH_USERNAME,
    AUTH_PASSWORD,
    SESSION_SECRET,
    SESSION_EXPIRY_DAYS,
    MONITOR_API_KEY,
    SCAN_TIMEOUT_SEC,
)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

PLATFORM_METRIC_LABEL = {
    "youtube": "subscribers",
    "facebook": "followers",
    "instagram": "followers",
    "tiktok": "followers",
    "threads": "followers",
}


# ------------------------------------------------------------------
# Lifespan (replaces deprecated on_event)
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run schema migration once at startup."""
    init_db()
    assert not (STATIC_DIR / "ig_state.json").exists(), "Session file in static dir!"
    assert not (STATIC_DIR / "monitor.db").exists(), "DB in static dir!"
    yield


# ------------------------------------------------------------------
# App initialization (must happen ONCE)
# ------------------------------------------------------------------

app = FastAPI(title="Social Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

scan_running = threading.Event()

# API key for programmatic/script access (kept alongside session auth)
API_KEY = MONITOR_API_KEY
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ------------------------------------------------------------------
# Auth Middleware
# ------------------------------------------------------------------


class AuthMiddleware(BaseHTTPMiddleware):
    """Require authentication on all routes except /login, /health, and /static."""

    PUBLIC_PATHS = {"/login", "/health"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Public routes — no auth required
        if path in self.PUBLIC_PATHS or path.startswith("/static"):
            return await call_next(request)

        # Check session cookie (populated by SessionMiddleware which runs first)
        if request.session.get("authenticated"):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

        # Check API key (for programmatic access)
        api_key = request.headers.get("X-API-Key", "")
        if API_KEY and api_key == API_KEY:
            return await call_next(request)

        # Not authenticated — redirect browser, 401 for API
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/login", status_code=303)
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


# Order matters: SessionMiddleware must be added AFTER AuthMiddleware.
# add_middleware() inserts at position 0, so the LAST one added becomes
# the outermost layer and runs FIRST on incoming requests.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_EXPIRY_DAYS * 86400,
    same_site="lax",
    https_only=os.environ.get("HTTPS_ONLY", "false").lower() == "true",
)


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


@app.get("/health")
def health():
    """Health check endpoint for load balancers and monitoring."""
    try:
        conn = get_conn()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {
        "status": "ok",
        "scan_running": scan_running.is_set(),
        "db": db_status,
    }


# ------------------------------------------------------------------
# Login / Logout
# ------------------------------------------------------------------


@app.get("/login")
def login_page():
    """Serve the login page (public route)."""
    response = FileResponse(STATIC_DIR / "login.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Validate credentials and establish a session."""
    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url="/login?error=1", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    """Clear the session and redirect to login."""
    request.session.clear()
    response = RedirectResponse(url="/login", status_code=303)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ------------------------------------------------------------------
# Remote Browser Login (noVNC)
# ------------------------------------------------------------------


@app.get("/api/login/start/{platform}")
async def api_login_start(platform: str):
    """Start a remote browser login session."""
    allowed = {"instagram", "facebook", "threads", "tiktok"}
    if platform not in allowed:
        raise HTTPException(
            status_code=422, detail=f"Platform must be one of {allowed}"
        )
    try:
        result = await start_login_session(platform)
        return {"status": "started", **result}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/login/done")
async def api_login_done():
    """Save the session from the remote browser and clean up."""
    result = await complete_login_session()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return {"status": "saved", **result}


@app.post("/api/login/cancel")
async def api_login_cancel():
    """Cancel the remote login session without saving."""
    result = await cancel_login_session()
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/login/status")
def api_login_status():
    """Check if a remote login session is active."""
    return get_session_status()


# ------------------------------------------------------------------
# Dashboard + API
# ------------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def validate_date(d: str):
    """Validate date format (YYYY-MM-DD). Returns None if empty."""
    if not d:
        return None
    try:
        dt.strptime(d, "%Y-%m-%d")
        return d
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date format: {d}")


@app.get("/api/stats")
def api_stats(from_date: str = None, to_date: str = None):
    from_date = validate_date(from_date)
    to_date = validate_date(to_date)
    conn = get_conn()
    data = get_overall_stats(conn, from_date, to_date)
    conn.close()
    return data


@app.get("/api/profiles")
def api_profiles(from_date: str = None, to_date: str = None):
    from_date = validate_date(from_date)
    to_date = validate_date(to_date)
    profiles = get_profiles()
    conn = get_conn()
    stats = get_profile_stats(conn, from_date, to_date)
    result = []
    for p in profiles:
        s = stats.get(p["id"], {"total": 0, "last_run": None})
        result.append(
            {
                "id": p["id"],
                "platform": p["platform"],
                "name": p["name"],
                "url": p["url"],
                "company": p.get("company") or "",
                "total_posts": s["total"],
                "followers": get_latest_metric(conn, p["id"]),
                "metric_label": PLATFORM_METRIC_LABEL.get(p["platform"], "followers"),
                "last_run": s["last_run"],
            }
        )
    conn.close()
    return result


@app.get("/api/platforms")
def api_platforms():
    conn = get_conn()
    platforms = get_distinct_platforms(conn)
    conn.close()
    return platforms


@app.get("/api/posts")
def api_posts(
    from_date: str = None,
    to_date: str = None,
    platform: str = None,
    profile_id: str = None,
    search: str = None,
    sort_by: str = "published_at",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int = 20,
    since: str = None,
):
    from_date = validate_date(from_date)
    to_date = validate_date(to_date)
    if search and len(search) > 200:
        raise HTTPException(status_code=422, detail="Search query too long")

    # Allow large pages for the scan-results fetch (which needs all
    # newly-detected posts in one request). Normal browsing uses the
    # default per_page=20.
    per_page = min(per_page, 100000)
    page = max(page, 1)
    offset = (page - 1) * per_page

    conn = get_conn()
    posts = get_posts_filtered(
        conn,
        from_date=from_date,
        to_date=to_date,
        platform=platform,
        profile_id=profile_id,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=per_page,
        offset=offset,
        detected_since=since,
    )
    total = get_posts_count(
        conn,
        from_date=from_date,
        to_date=to_date,
        platform=platform,
        profile_id=profile_id,
        search=search,
        detected_since=since,
    )
    conn.close()

    for post in posts:
        if "profile_name" not in post or not post["profile_name"]:
            post["profile_name"] = post.get("profile_id", "unknown")
        if "profile_company" not in post:
            post["profile_company"] = ""

    return {
        "posts": posts,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page if total > 0 else 0,
    }


@app.get("/api/posts/export")
def api_posts_export(
    from_date: str = None,
    to_date: str = None,
    platform: str = None,
    profile_id: str = None,
    search: str = None,
    sort_by: str = "published_at",
    sort_dir: str = "desc",
):
    from_date = validate_date(from_date)
    to_date = validate_date(to_date)
    if search and len(search) > 200:
        raise HTTPException(status_code=422, detail="Search query too long")

    conn = get_conn()
    posts = get_posts_filtered(
        conn,
        from_date=from_date,
        to_date=to_date,
        platform=platform,
        profile_id=profile_id,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=5000,
        offset=0,
    )
    conn.close()

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["Platform", "Account", "Company", "Title", "URL", "Published", "Detected"]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        for post in posts:
            writer.writerow(
                [
                    post.get("platform", ""),
                    post.get("profile_name", post.get("profile_id", "")),
                    post.get("profile_company", "") or "",
                    post.get("title", ""),
                    post.get("url", ""),
                    post.get("published_at", ""),
                    post.get("detected_at", ""),
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=posts_export.csv"},
    )


class ProfileIn(BaseModel):
    platform: str
    name: str
    url: str
    company: str = ""

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        allowed = {"instagram", "youtube", "tiktok", "facebook", "threads"}
        if v not in allowed:
            raise ValueError(f"Platform must be one of {allowed}")
        return v

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        allowed_prefixes = (
            "https://www.instagram.com/",
            "https://www.youtube.com/",
            "https://www.tiktok.com/",
            "https://www.facebook.com/",
            "https://www.threads.net/",
            "https://www.threads.com/",
        )
        if not v.startswith(allowed_prefixes):
            raise ValueError("URL must be a valid social media profile URL")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if len(v) > 100:
            raise ValueError("Name too long")
        return v

    @field_validator("company")
    @classmethod
    def validate_company(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("Company too long")
        return v

    @field_validator("company")
    @classmethod
    def validate_company(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("Company too long")
        return v


@app.post("/api/profiles")
def api_add_profile(p: ProfileIn):
    conn = get_conn()
    new_id = add_profile(conn, p.platform, p.name, p.url, p.company)
    conn.close()
    return {"status": "added", "id": new_id}


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: str):
    conn = get_conn()
    delete_profile(conn, profile_id)
    conn.close()
    return {"status": "deleted"}


@app.get("/api/scan/status")
def api_scan_status():
    return {"running": scan_running.is_set()}


@app.get("/api/scan/progress")
def api_scan_progress():
    """Return the structured scan progress snapshot for the dashboard."""
    return scan_state.get_progress()


@app.get("/api/scan/session")
def api_scan_session():
    """Return the latest scan session for resume detection on app load.

    The frontend calls this on page load to check whether an incomplete
    session exists. If running=true the scan is still in progress
    (reattach the modal). If running=false but profiles are incomplete,
    offer the user a resume.
    """
    prog = scan_state.get_progress()
    has_incomplete = any(p["status"] != "completed" for p in prog["profiles"])
    return {
        "session_id": prog.get("session_id"),
        "created_at": prog.get("created_at"),
        "running": prog["running"],
        "has_incomplete": has_incomplete,
        "total": len(prog["profiles"]),
        "completed": sum(1 for p in prog["profiles"] if p["status"] == "completed"),
    }


@app.post("/api/scan/cancel")
def api_scan_cancel():
    """Request a graceful cancel (stops after the current profile)."""
    if not scan_running.is_set():
        return {"status": "not_running"}
    scan_state.request_cancel()
    return {"status": "cancel_requested"}


class ScanRunBody(BaseModel):
    """Optional JSON body for filtered scan execution."""

    profile_ids: list[str] | None = None
    fresh: bool = False


@app.post("/api/scan/run")
def api_trigger_scan(body: ScanRunBody | None = None):
    """Trigger a scan. Accepts an optional JSON body:

        {"profile_ids": ["ig-abc123", ...], "fresh": false}

    profile_ids filters which accounts to scan (company filter).
    fresh=true forces a full re-scan ignoring prior session state.
    """
    if scan_running.is_set():
        return {"status": "already_running"}

    # Resolve body (also support query-param fallback for backward compat)
    if body is None:
        body = ScanRunBody()

    # Reset cancel flag. Do NOT clear_progress() here — init_progress()
    # inside run_scan() will resume from the previous interrupted scan
    # (keeping completed profiles) or start fresh when appropriate.
    scan_state.reset_cancel()

    # Set the flag IMMEDIATELY so the frontend's first status poll sees
    # running=True (avoids a false "scan complete").
    scan_running.set()

    profile_ids = body.profile_ids
    fresh = body.fresh

    def _do_scan():
        from main import run_scan

        try:
            # No whole-scan timeout — the per-profile timeout in
            # run_scan() (asyncio.wait_for on each scan_profile call)
            # already prevents individual profiles from hanging.
            # The cancel button handles user-initiated stops.
            asyncio.run(run_scan(profile_ids=profile_ids, fresh=fresh))
        except Exception as exc:
            print(f"  ! Scan thread crashed: {exc}")
        finally:
            scan_running.clear()
            scan_state.reset_cancel()
            scan_state.finish_progress()

    threading.Thread(target=_do_scan, daemon=True).start()
    return {"status": "started", "fresh": fresh}


# Proxy /vnc/ requests to the websockify/noVNC server
@app.api_route("/vnc/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def vnc_proxy(path: str, request: Request):
    """Proxy noVNC web client requests to the websockify server."""
    target_url = f"http://localhost:{NOVNC_PORT}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    async with httpx.AsyncClient() as client:
        headers = dict(request.headers)
        headers.pop("host", None)
        resp = await client.request(
            request.method,
            target_url,
            headers=headers,
            content=await request.body() if request.method in ("POST", "PUT") else None,
        )

        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=resp.headers.get("content-type"),
        )
        return response


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
