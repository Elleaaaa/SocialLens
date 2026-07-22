FROM python:3.12-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps chromium

COPY . .

RUN mkdir -p /data
ENV DATA_DIR=/data

EXPOSE 8080
CMD ["xvfb-run", "--auto-servernum", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]