FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser + all required system libraries
RUN playwright install --with-deps chromium

# Virtual framebuffer + auth for headed browser sessions (TikTok grid).
# xauth is required by xvfb-run but not always pulled in automatically.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

# Seed the database into /data (matches DATA_DIR=/data)
RUN mkdir -p /data
COPY monitor.db /data/monitor.db

ENV DATA_DIR=/data

# Wrap uvicorn in xvfb-run so headed Chromium launches work on a server.
EXPOSE 8080
CMD ["xvfb-run", "--auto-servernum", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]