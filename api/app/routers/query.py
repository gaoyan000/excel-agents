"""Phase 3: unified table preview + NL→SQL query (zh/en) + xlsx export."""
from __future__ import annotations

import io
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import cache, db, duck, llm
from ..i18n import msg

router = APIRouter(prefix="/api", tags=["query"])


def _flat_mapping(srcs: list[dict]) -> dict[str, str]:
    """{source_col: canonical_field} from confirmed mappings, dropping
    columns the user left unmapped."""
    flat: dict[str, str] = {}
    for s in srcs:
        m = db.get_mapping(s["fingerprint"])
        if not m or not m["confirmed"]:
            raise HTTPException(409, "mapping not confirmed for a source")
        for col, info in m["mapping"].items():
            if info.get("to"):
                flat[col] = info["to"]
    if not flat:
        raise HTTPException(409, "no confirmed column mappings")
    return flat


def _load(ids: list[int]) -> list[dict]:
    srcs = [db.get_source(i) for i in ids]
    if any(s is None or "columns" not in s for s in srcs):
        raise HTTPException(404, "unknown source_id")
    return srcs  # type: ignore[return-value]


class PreviewReq(BaseModel):
    source_ids: list[int]
    limit: int = 50


@router.post("/table/preview")
def table_preview(req: PreviewReq) -> dict:
    srcs = _load(req.source_ids)
    unified = duck.build_unified_sql(srcs, _flat_mapping(srcs))
    res = duck.preview(unified, req.limit)
    return {"columns": res["columns"], "rows": res["rows"]}


class QueryReq(BaseModel):
    source_ids: list[int]
    question: str


@router.post("/query")
def query(req: QueryReq) -> dict:
    srcs = _load(req.source_ids)
    unified = duck.build_unified_sql(srcs, _flat_mapping(srcs))
    canon = db.latest_canonical() or {"version": 0, "fields": []}

    # Cache namespace bumped to v2: the v1 namespace held SQL generated
    # before the DuckDB-specific prompt fix (date_format etc.), so those
    # cached strings would 500 forever otherwise.
    key = cache.cache_key("nlsql_v2", {"q": req.question, "v": canon["version"]})
    sql = cache.cache_get(key)
    if sql is None:
        sql = llm.nl_to_sql(req.question, canon["fields"])

    if not sql:  # no API key -> honest fallback to a sample
        res = duck.preview(unified, 50)
        return {
            "columns": res["columns"], "rows": res["rows"],
            "sql": None, "message": msg("query_need_key"),
        }

    # First attempt; on a DuckDB execution error ask the LLM to self-repair
    # once. We only cache SQL that actually executed cleanly, so a bad first
    # attempt never poisons the cache.
    try:
        res = duck.run_query(unified, sql)
    except duck.SqlRejected:
        raise HTTPException(400, msg("sql_rejected")["en"])
    except Exception as e:  # noqa: BLE001 - DuckDB raises a family of types
        fixed = llm.fix_sql(req.question, sql, str(e), canon["fields"])
        if not fixed:
            raise HTTPException(500, f"SQL execution failed: {e}")
        try:
            res = duck.run_query(unified, fixed)
            sql = fixed
        except duck.SqlRejected:
            raise HTTPException(400, msg("sql_rejected")["en"])
        except Exception as e2:  # noqa: BLE001
            raise HTTPException(500, f"SQL retry also failed: {e2}")

    cache.cache_put(key, sql)
    return {"columns": res["columns"], "rows": res["rows"], "sql": res["sql"]}


class ExportReq(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    # The browser uses this to name the downloaded file. UTF-8 safe — we
    # encode it via RFC 5987 in the Content-Disposition header below so
    # Chinese filenames like "查询结果.xlsx" survive the round-trip.
    filename: str = "查询结果.xlsx"
    # Optional worksheet name; Excel forbids the chars / \ ? * [ ] : and
    # caps the length at 31. We clamp on the server.
    sheet_name: str = "Sheet1"


def _clean_sheet_name(name: str) -> str:
    cleaned = "".join(c for c in name if c not in r"/\?*[]:")[:31].strip()
    return cleaned or "Sheet1"


@router.post("/export")
def export_xlsx(req: ExportReq) -> StreamingResponse:
    """Stream the (columns, rows) payload back as an .xlsx file.

    We accept the data from the client rather than re-running the SQL
    server-side so the export is always exactly what the user saw on
    screen — no re-cache, no race with later mappings.
    """
    # openpyxl is a regular dependency (used by app/excel.py for ingest),
    # so importing it here is free.
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = _clean_sheet_name(req.sheet_name)
    ws.append(req.columns)
    for row in req.rows:
        # openpyxl writes native types directly; map JSON `None` to a
        # blank cell. Lists/dicts in cells aren't legal — stringify them
        # defensively (this shouldn't happen with our table shape but
        # keeps the route robust to caller mistakes).
        ws.append([
            None if v is None
            else v if isinstance(v, (str, int, float, bool))
            else str(v)
            for v in row
        ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    # RFC 5987: filename* uses percent-encoded UTF-8 so Chinese filenames
    # work in every modern browser. We also supply an ASCII fallback for
    # extra-paranoid clients via plain `filename=`.
    safe_name = req.filename.replace('"', "").strip() or "export.xlsx"
    ascii_fallback = "export.xlsx"
    disposition = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(safe_name)}"
    )
    return StreamingResponse(
        buf,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": disposition},
    )
