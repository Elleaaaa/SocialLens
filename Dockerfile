# Use bookworm (Debian 12) — Playwright officially supports it.
# The default python:3.12.13 is now Trixie (Debian 13), which is too new
# and causes `playwright install --with-deps` to fail on font packages.
FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser + all required system libraries.
# Bookworm is officially supported, so --with-deps resolves correctly.
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Runtime data directory (maps to Fly.io persistent volume)
RUN mkdir -p /data
ENV DATA_DIR=/data

# Use uvicorn directly. Port 8080 matches fly.toml internal_port.
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]