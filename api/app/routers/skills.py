"""Phase 4 + 5: typed-op skills, replay, and the drift state machine (§3,§4)."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import db, duck, llm
from ..fingerprint import classify_drift, fingerprint
from ..i18n import msg
from ..skills.ops import compile_steps, steps_from_mapping
from .ingest import ingest_files

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SaveReq(BaseModel):
    name: str
    source_ids: list[int]


def _confirmed_mapping(src: dict) -> dict:
    m = db.get_mapping(src["fingerprint"])
    if not m or not m["confirmed"]:
        raise HTTPException(409, "confirm the mapping before saving a skill")
    return m["mapping"]


@router.post("")
def save(req: SaveReq) -> dict:
    src = db.get_source(req.source_ids[0])
    if not src or "columns" not in src:
        raise HTTPException(404, "unknown source_id")
    canon = db.latest_canonical()
    if not canon:
        raise HTTPException(409, "no confirmed canonical schema")
    steps = steps_from_mapping(_confirmed_mapping(src), canon["fields"])
    skill = db.save_skill(
        req.name, src["fingerprint"], canon["version"], steps
    )
    return {"skill": {**skill, "steps": steps}, "message": msg("skill_saved")}


@router.get("")
def list_all() -> dict:
    return {"skills": db.list_skills()}


def _run_skill(skill: dict, new_sources: list[dict]) -> dict:
    parts = [
        f"({compile_steps(skill['steps'], duck.base_scan(s['raw_path']))})"
        for s in new_sources
    ]
    unified = "\nUNION ALL\n".join(parts)
    res = duck.preview(unified, 50)
    return {"columns": res["columns"], "rows": res["rows"]}


@router.post("/{skill_id}/apply")
async def apply(skill_id: int, files: list[UploadFile] = File(...)) -> dict:
    skill = db.get_skill(skill_id)
    if not skill:
        raise HTTPException(404, "unknown skill")
    new_sources = ingest_files(files)

    # Drift is decided per file against the skill's required source columns.
    reports = [
        {
            "source_id": s["id"],
            "filename": s["filename"],
            **classify_drift(skill["steps"], s["columns"]),
        }
        for s in new_sources
    ]
    worst = (
        "unmappable" if any(r["kind"] == "unmappable" for r in reports)
        else "mappable" if any(r["kind"] == "mappable" for r in reports)
        else "exact"
    )

    if worst == "exact":
        db.record_run(skill_id, [s["id"] for s in new_sources], "ok", None)
        return {
            "status": "ok",
            "result": _run_skill(skill, new_sources),
            "message": msg("drift_none"),
        }

    if worst == "unmappable":
        db.record_run(
            skill_id, [s["id"] for s in new_sources],
            "drift_unmappable", {"reports": reports},
        )
        return {
            "status": "drift_unmappable",
            "drift": reports,
            "message": msg("drift_unmappable"),
        }

    # Mappable: propose a remap for the new shape (LLM/heuristic), hand back
    # to the confirmation UI. Confirming closes the learning loop via /remap.
    files_schema = [
        {"filename": s["filename"], "columns": s["columns"]} for s in new_sources
    ]
    proposal = llm.propose_mapping(files_schema)
    db.record_run(
        skill_id, [s["id"] for s in new_sources],
        "drift_mappable", {"reports": reports},
    )
    return {
        "status": "drift_mappable",
        "drift": reports,
        "proposed_mapping": proposal["mapping"],
        "new_source_ids": [s["id"] for s in new_sources],
        "message": msg("drift_mappable"),
    }


class RemapReq(BaseModel):
    source_ids: list[int]
    mapping: dict  # {source_col: {to, ...}} confirmed by the human


@router.post("/{skill_id}/remap")
def remap(skill_id: int, req: RemapReq) -> dict:
    """Close the drift loop: re-record the skill's map_column steps for the
    new shape, keep downstream ops, re-fingerprint, and run."""
    skill = db.get_skill(skill_id)
    if not skill:
        raise HTTPException(404, "unknown skill")
    src = db.get_source(req.source_ids[0])
    if not src or "columns" not in src:
        raise HTTPException(404, "unknown source_id")

    non_map = [s for s in skill["steps"] if s["op"] != "map_column"]
    new_map = [
        {"op": "map_column", "from": col, "to": info["to"]}
        for col, info in req.mapping.items()
        if info.get("to")
    ]
    steps = new_map + non_map
    new_fp = fingerprint(src["columns"])
    db.update_skill_steps(skill_id, steps, new_fp)
    for s in (db.get_source(i) for i in req.source_ids):
        db.upsert_mapping(s["fingerprint"], req.mapping, confirmed=True)

    refreshed = db.get_skill(skill_id)
    new_sources = [db.get_source(i) for i in req.source_ids]
    db.record_run(skill_id, req.source_ids, "ok", {"remapped": True})
    return {
        "status": "ok",
        "skill": {"id": skill_id, "steps": steps},
        "result": _run_skill(refreshed, new_sources),  # type: ignore[arg-type]
        "message": msg("mapping_confirmed"),
    }
