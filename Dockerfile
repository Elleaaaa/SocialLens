FROM python:3.12.13

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser + all required system libraries
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Runtime data directory (maps to Fly.io persistent volume)
RUN mkdir -p /data
ENV DATA_DIR=/data

# Use uvicorn directly (not `fastapi` CLI — that needs fastapi-cli which isn't installed).
# Port 8080 matches fly.toml internal_port.
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]