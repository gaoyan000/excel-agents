"""Phase 1: ingestion + DuckDB introspection + fingerprint."""
from __future__ import annotations

import csv
import hashlib
import io

from fastapi import APIRouter, File, UploadFile

from .. import db, duck, storage
from ..fingerprint import fingerprint
from ..i18n import msg

router = APIRouter(prefix="/api", tags=["ingest"])


def _xls_cell_to_str(book, cell) -> str:
    """Render an xlrd cell as the string that ends up in our CSV.

    xlrd returns dates as Excel serial floats (e.g. 44197.0 for 2021-01-01)
    and integers as floats (e.g. 100.0). Without explicit handling our CSV
    would carry those raw values and downstream type inference / canonical
    mapping would see garbage.
    """
    import xlrd

    ct = cell.ctype
    if ct in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return ""
    if ct == xlrd.XL_CELL_TEXT:
        return cell.value
    if ct == xlrd.XL_CELL_NUMBER:
        v = cell.value
        return str(int(v)) if v.is_integer() else str(v)
    if ct == xlrd.XL_CELL_DATE:
        return xlrd.xldate_as_datetime(cell.value, book.datemode).isoformat()
    if ct == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    return ""


def _convert_xls_to_csv(filename: str, raw: bytes) -> tuple[str, bytes]:
    """Legacy .xls (BIFF) -> CSV bytes so DuckDB can ingest via read_csv_auto.

    DuckDB's excel extension only understands the .xlsx (OOXML) format;
    BIFF requires a Python decoder. We keep this helper as the single
    conversion point and leave .csv / .tsv / .txt / .xlsx untouched.
    """
    if not filename.lower().endswith(".xls"):
        return filename, raw
    import xlrd  # lazy: only the .xls path needs it

    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)  # first sheet, mirroring the xlsx path
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row_idx in range(sheet.nrows):
        writer.writerow(
            _xls_cell_to_str(book, sheet.cell(row_idx, c))
            for c in range(sheet.ncols)
        )
    # Suffix .csv so _scan_expr routes to read_csv_auto. The user-visible
    # filename in the DB / API response stays the original .xls.
    return filename + ".csv", buf.getvalue().encode("utf-8")


def ingest_files(files: list[UploadFile]) -> list[dict]:
    """Store raw bytes, introspect via DuckDB, persist source + schema.

    Shared by /api/ingest and skill-apply so re-runs use the identical path.
    """
    out: list[dict] = []
    for up in files:
        raw = up.file.read()
        # Content hash is over the ORIGINAL bytes so identical .xls uploads
        # still dedup correctly even though we store the converted CSV.
        content_hash = hashlib.sha256(raw).hexdigest()[:16]
        stored_filename, stored_raw = _convert_xls_to_csv(up.filename, raw)
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
