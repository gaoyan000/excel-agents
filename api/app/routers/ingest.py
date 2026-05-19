"""Phase 1: ingestion + DuckDB introspection + fingerprint."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, File, UploadFile

from .. import db, duck, storage
from ..fingerprint import fingerprint
from ..i18n import msg

router = APIRouter(prefix="/api", tags=["ingest"])


def ingest_files(files: list[UploadFile]) -> list[dict]:
    """Store raw bytes, introspect via DuckDB, persist source + schema.

    Shared by /api/ingest and skill-apply so re-runs use the identical path.
    """
    out: list[dict] = []
    for up in files:
        raw = up.file.read()
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        raw_path = storage.put_raw(content_hash, up.filename, raw)
        columns = duck.introspect(raw_path)
        fp = fingerprint(columns)
        sid = db.insert_source(up.filename, content_hash, raw_path)
        db.save_source_schema(sid, columns, fp)
        out.append(
            {
                "id": sid,
                "filename": up.filename,
                "fingerprint": fp,
                "columns": columns,
                "raw_path": raw_path,
            }
        )
    return out


@router.post("/ingest")
async def ingest(files: list[UploadFile] = File(...)) -> dict:
    sources = ingest_files(files)
    known = all(db.get_mapping(s["fingerprint"]) for s in sources)
    return {
        "sources": [
            {k: v for k, v in s.items() if k != "raw_path"} for s in sources
        ],
        "known_fingerprint": bool(sources) and known,
        "message": msg("ingest_ok"),
    }
