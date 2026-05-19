"""Runtime configuration, read once from the environment.

Deliberately minimal (DESIGN.md §2): no settings framework, just env vars
with sane local defaults so the app runs with zero setup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    storage_dir: Path
    metadata_db: Path
    cors_origins: list[str]
    # "local" (disk, default) | "r2" (Cloudflare R2 via DuckDB httpfs/S3).
    storage_backend: str
    r2_endpoint: str          # "<accountid>.r2.cloudflarestorage.com"
    r2_access_key: str
    r2_secret: str
    r2_bucket: str

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def r2_enabled(self) -> bool:
        return self.storage_backend == "r2"


def load_settings() -> Settings:
    storage_dir = Path(os.getenv("STORAGE_DIR", "./storage")).resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    (storage_dir / "raw").mkdir(exist_ok=True)
    metadata_db = Path(
        os.getenv("METADATA_DB", str(storage_dir / "metadata.db"))
    ).resolve()
    origins = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        storage_dir=storage_dir,
        metadata_db=metadata_db,
        cors_origins=origins,
        storage_backend=os.getenv("STORAGE_BACKEND", "local").strip().lower(),
        r2_endpoint=os.getenv("R2_ENDPOINT", "").strip(),
        r2_access_key=os.getenv("R2_ACCESS_KEY_ID", "").strip(),
        r2_secret=os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
        r2_bucket=os.getenv("R2_BUCKET", "").strip(),
    )


SETTINGS = load_settings()
