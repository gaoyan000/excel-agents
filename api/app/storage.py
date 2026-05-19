"""Raw-file storage seam: local disk now, Cloudflare R2 later.

Selected by STORAGE_BACKEND (config.py). Everything else in the app stays
identical — only the locator stored as `sources.raw_path` changes:
  local -> absolute filesystem path
  r2    -> "s3://<bucket>/raw/<hash>_<name>" (DuckDB reads it via httpfs)

boto3 is imported lazily so local mode needs no extra dependency; R2 mode
fails loudly with an actionable message if it is missing.
"""
from __future__ import annotations

from .config import SETTINGS


def _safe_key_part(name: str) -> str:
    return name.replace("/", "_").replace("\\", "_")


def _r2_client():
    try:
        import boto3  # lazy: only the R2 backend needs it
    except ModuleNotFoundError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "STORAGE_BACKEND=r2 requires boto3 — `pip install boto3`."
        ) from e
    return boto3.client(
        "s3",
        endpoint_url=f"https://{SETTINGS.r2_endpoint}",
        aws_access_key_id=SETTINGS.r2_access_key,
        aws_secret_access_key=SETTINGS.r2_secret,
        region_name="auto",
    )


def put_raw(content_hash: str, filename: str, data: bytes) -> str:
    """Persist an uploaded file; return the locator stored in the DB."""
    key = f"raw/{content_hash}_{_safe_key_part(filename)}"
    if SETTINGS.r2_enabled:
        _r2_client().put_object(
            Bucket=SETTINGS.r2_bucket, Key=key, Body=data
        )
        return f"s3://{SETTINGS.r2_bucket}/{key}"
    path = SETTINGS.storage_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def local_copy(uri: str) -> str:
    """Return a real local path for readers that cannot stream from S3
    (DuckDB's xlsx reader). Local paths and non-s3 URIs pass through.

    The download is cached under storage_dir/cache so repeat reads of the
    same object are free.
    """
    if not uri.startswith("s3://"):
        return uri
    bucket, _, key = uri[len("s3://"):].partition("/")
    cache = SETTINGS.storage_dir / "cache" / key
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        _r2_client().download_file(bucket, key, str(cache))
    return str(cache)
