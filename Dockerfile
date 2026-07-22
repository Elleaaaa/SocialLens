FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser + all required system libraries
RUN playwright install --with-deps chromium

# Virtual framebuffer + VNC + noVNC for remote browser viewing.
# xauth is required by xvfb-run; x11vnc/websockify/novnc enable remote viewing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb \
        xauth \
        x11vnc \
        websockify \
        novnc \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

RUN mkdir -p /data

# Wrap uvicorn in xvfb-run so headed Chromium launches work on a server.
EXPOSE 8080
CMD ["sh", "-c", "for i in $(seq 1 10); do ls /data/monitor.db && break || sleep 2; done && xvfb-run --auto-servernum uvicorn server:app --host 0.0.0.0 --port 8080"]