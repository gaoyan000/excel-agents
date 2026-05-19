# FastAPI + native DuckDB image for Cloudflare Containers (or any VM/PaaS).
FROM python:3.12-slim

WORKDIR /app

# DuckDB + boto3 (R2) wheels are prebuilt — no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

COPY app ./app

# DuckDB downloads httpfs/excel extensions on first use; allow a writable home.
ENV HOME=/tmp
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
