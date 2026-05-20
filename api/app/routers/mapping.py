"""Phase 2: bilingual AI mapping + confirmation gate + cache (§2,§5)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import cache, db, llm
from ..i18n import msg

router = APIRouter(prefix="/api/mapping", tags=["mapping"])


class ProposeReq(BaseModel):
    source_ids: list[int]
    # "smart" (default) -> LLM discovers a canonical schema from the data
    # and clusters similar columns across files; falls back to the
    # bilingual dictionary heuristic without an OpenAI key.
    # "raw" -> identity mapping: each source column is its own canonical.
    mode: str = "smart"


class ConfirmReq(BaseModel):
    source_ids: list[int]
    mapping: dict          # {source_col: {to, confidence, rationale}}
    canonical_schema: list[dict]


def _load_sources(ids: list[int]) -> list[dict]:
    srcs = [db.get_source(i) for i in ids]
    if any(s is None or "columns" not in s for s in srcs):
        raise HTTPException(404, "unknown or un-introspected source_id")
    return srcs  # type: ignore[return-value]


@router.post("/propose")
def propose(req: ProposeReq) -> dict:
    srcs = _load_sources(req.source_ids)

    # Transformation memory: if every distinct schema already has a confirmed
    # mapping, reuse it with zero LLM calls (DESIGN.md §5).
    confirmed = [db.get_mapping(s["fingerprint"]) for s in srcs]
    if all(m and m["confirmed"] for m in confirmed):
        merged: dict = {}
        for m in confirmed:
            merged.update(m["mapping"])  # type: ignore[index]
        canon = db.latest_canonical() or {"fields": []}
        return {
            "mapping": merged,
            "canonical_schema": canon["fields"],
            "cached": True,
            "message": msg("mapping_cached"),
        }

    files = [{"filename": s["filename"], "columns": s["columns"]} for s in srcs]
    # Mode is part of the cache key so a raw-mode proposal doesn't serve
    # a smart-mode cached result (or vice versa).
    key = cache.cache_key("mapping", {"files": files, "mode": req.mode})
    hit = cache.cache_get(key)
    if hit:
        return {**hit, "cached": True, "message": msg("mapping_cached")}

    result = llm.propose_mapping(files, mode=req.mode)
    cache.cache_put(key, result)
    return {**result, "cached": False, "message": msg("mapping_proposed")}


@router.post("/confirm")
def confirm(req: ConfirmReq) -> dict:
    srcs = _load_sources(req.source_ids)
    version = db.save_canonical(req.canonical_schema)
    # Persist the confirmed mapping per distinct schema fingerprint so the
    # next same-shape upload is free (§5 transformation memory).
    for s in srcs:
        sub = {
            c["name"]: req.mapping[c["name"]]
            for c in s["columns"]
            if c["name"] in req.mapping
        }
        db.upsert_mapping(s["fingerprint"], sub, confirmed=True)
    return {
        "canonical_schema_version": version,
        "message": msg("mapping_confirmed"),
    }
