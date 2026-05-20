# FastAPI + native DuckDB image for Cloudflare Containers (or any VM/PaaS).
FROM python:3.12-slim

WORKDIR /app

# Match HOME to the runtime value BEFORE installing the duckdb extension,
# so the extension cache (HOME/.duckdb/extensions/...) lands in a location
# that exists at runtime. /tmp is a regular directory in this image; files
# written here at build time are baked into the image layer and persist.
ENV HOME=/tmp

# DuckDB + boto3 (R2) wheels are prebuilt — no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3

# Pre-install the duckdb `excel` extension so the first xlsx upload at
# runtime doesn't depend on Fly egress to extensions.duckdb.org being
# reachable, and doesn't pay the per-cold-start download latency.
RUN python -c "import duckdb; c=duckdb.connect(); c.execute('INSTALL excel'); c.execute('LOAD excel'); c.close()"

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
