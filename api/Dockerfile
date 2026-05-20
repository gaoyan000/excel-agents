# FastAPI + native DuckDB image for Cloudflare Containers (or any VM/PaaS).
FROM python:3.12-slim

WORKDIR /app

# Match HOME to the runtime value so any later-added duckdb extension cache
# (or other tool caches) land in a stable location. /tmp is a regular
# directory in this image; files written here at build time are baked
# into the image layer and persist.
ENV HOME=/tmp

# DuckDB + boto3 (R2) wheels are prebuilt — no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
