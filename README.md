# Social Monitor

## Overview

This repository is a social media monitoring system that:
- scans Instagram, Facebook, Threads, TikTok, and YouTube profiles
- stores profile, post, and follower data in SQLite
- provides a FastAPI dashboard for viewing results and managing profiles
- uses Playwright browser automation for login and scraping

## Important Files

- `main.py` — runs the monitoring scan
- `server.py` — runs the web dashboard
- `login_server.py` — handles remote browser login sessions
- `config.py` — environment-driven configuration
- `storage.py` — SQLite database layer
- `requirements.txt` — Python dependencies
- `Dockerfile` — container image build recipe
- `fly.toml` — optional Fly.io deployment config

## Device Support

This system can run on:
1. Local machine (Windows, macOS, Linux)
2. Docker / container host
3. Remote server or cloud VM

## Prerequisites

- Python 3.14
- `pip`
- `git` (optional)
- `docker` if using Docker
- `flyctl` if using Fly.io deployment

## Setup (All Devices)

1. Clone or copy the repository.
2. Create a Python virtual environment:

```bash
python -m venv .venv
or
py -m venv .venv
```

3. Activate the environment:

- Windows:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- macOS / Linux:
  ```bash
  source .venv/bin/activate
  ```

4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Install Playwright browser support:

```bash
python -m playwright install chromium
py -m playwright install chromium
```

## Environment

The app reads optional environment variables from `.env` and the OS.

Example `.env` values:

Key variables:
- `DATA_DIR` — directory for the SQLite database (`monitor.db`)
- `BROWSER_HEADLESS` — `true` or `false`
- `AUTH_USERNAME` / `AUTH_PASSWORD` — dashboard login credentials
- `SESSION_SECRET` — session cookie secret

## Local Run

### 1. Prepare login state files

The system uses saved browser session state for social platforms. The default files are:
- `ig_state.json`
- `tiktok_state.json`
- `threads_state.json`
- `fb_state.json`

### 2. Interactive login

To perform a one-time login for a platform locally and save the session state:

```bash
py main.py --login --platform instagram
```

Replace `instagram` with `facebook`, `threads`, or `tiktok`.

Follow the browser window instructions and save the session.

### 3. Run a one-time scan

```bash
py main.py --once
```

This will scan all profiles saved in the database.

### 4. Run the dashboard

```bash
py server.py
```

Then open the browser at:

```
http://127.0.0.1:8000
```

If you set `HOST` or `PORT` in `.env`, the values will be used by `server.py`.

### 5. Log in to the dashboard

Use the credentials from `AUTH_USERNAME` and `AUTH_PASSWORD`.

## Docker Run

The `Dockerfile` builds an image with Playwright, Chromium, and the required system packages.

### 1. Build the image

```bash
docker build -t social-monitor .
```

### 2. Run the container

```bash
docker run -d \
  --name social-monitor \
  -p 8080:8080 \
  -v %CD%:/app \
  -v socialmonitor_data:/data \
  -e BROWSER_HEADLESS=true \
  -e DATA_DIR=/data \
  social-monitor
```

Note: On Windows, replace `%CD%` with the absolute repository path.

### 3. Access the app

Open:

```
http://127.0.0.1:8080
```

### 4. Data persistence

The container stores the database in `/data/monitor.db` and uses mounted volume `socialmonitor_data`.

## Remote Server / Cloud Run

### Option A: Docker on a remote host

Use the same Docker build and run steps above on the remote server.

### Option B: Deploy with Fly.io

This project includes `fly.toml` configuration.

1. Install and log in to Fly.io.
2. Configure the app name if needed.
3. Deploy:

```bash
flyctl deploy
```

This uses `DATA_DIR=/data` and exposes the app on port `8080`.

## Running on another device

For each new device:

1. Clone or copy the repository.
2. Install Python and dependencies.
3. Install Playwright browsers.
4. Create or copy `.env`.
5. Perform login for each platform using `python main.py --login --platform <platform>`.
6. Start the dashboard with `python server.py` or via Docker.

## Notes

- The system stores data in SQLite and session files in the repository folder.
- `server.py` automatically initializes the database schema at startup.
- If you need a visible browser, set `BROWSER_HEADLESS=false`.
- If using Docker or a headless host, the `Dockerfile` already installs the required browser and noVNC tools.

## Troubleshooting

- `playwright` errors: make sure `python -m playwright install chromium` has run.
- `login` issues: make sure the session state file exists and is readable.
- `permission denied` or file access issues: verify mounted volume permissions.
- `monitor.db` not found: make sure `DATA_DIR` is set and writable.

## Useful Commands

```bash
# Reset the SQLite database
python reset_db.py

# Seed data from examples
python seed_db.py
python seed_db_nf.py

# Run the server with explicit host/port
HOST=0.0.0.0 PORT=8080 python server.py
```
