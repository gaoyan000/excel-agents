"""Phase 3: unified table preview + NL→SQL query (zh/en)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
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

    key = cache.cache_key("nlsql", {"q": req.question, "v": canon["version"]})
    sql = cache.cache_get(key)
    if sql is None:
        sql = llm.nl_to_sql(req.question, canon["fields"])
        if sql:
            cache.cache_put(key, sql)

    if not sql:  # no API key -> honest fallback to a sample
        res = duck.preview(unified, 50)
        return {
            "columns": res["columns"], "rows": res["rows"],
            "sql": None, "message": msg("query_need_key"),
        }
    try:
        res = duck.run_query(unified, sql)
    except duck.SqlRejected:
        raise HTTPException(400, msg("sql_rejected")["en"])
    return {"columns": res["columns"], "rows": res["rows"], "sql": res["sql"]}
