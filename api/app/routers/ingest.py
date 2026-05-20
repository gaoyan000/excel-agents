"""Phase 1: ingestion + DuckDB introspection + fingerprint.

Excel files (.xls + .xlsx) are pre-processed in Python (app/excel.py)
to detect the real header row and filter subtotal/total rows before
DuckDB touches them. CSV/TSV/TXT pass through unchanged.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, File, UploadFile

from .. import db, duck, excel, storage
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
        # Content hash is over the ORIGINAL bytes so identical Excel
        # re-uploads still dedup correctly even though we store the
        # converted CSV under raw_path.
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        converted = excel.excel_to_csv(up.filename, raw)
        if converted is not None:
            stored_filename, stored_raw = converted
        else:
            stored_filename, stored_raw = up.filename, raw
        raw_path = storage.put_raw(content_hash, stored_filename, stored_raw)
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
