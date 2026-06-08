"""Phase 4 agent: skill planning loop endpoints.

Three endpoints drive a stateful session:

  POST /api/agent/session
    Opens a session after step 3 (mapping confirmed). Returns the canonical
    schema and a data snapshot so the UI can show the starting state.

  POST /api/agent/session/{id}/plan
    One planning iteration: user sends a prompt, the orchestrator calls the
    LLM, auto-retries on compile/execute errors, and returns a new steps[]
    plus a preview of the transformed data. Call this repeatedly to refine.

  POST /api/agent/session/{id}/confirm
    Saves the current steps (or a hand-edited override) as a skill and
    returns the persisted skill record.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from . import orchestrator

router = APIRouter(prefix="/api/agent", tags=["agent"])


class StartReq(BaseModel):
    source_ids: list[int]


class PlanReq(BaseModel):
    prompt: str


class ConfirmReq(BaseModel):
    name: str
    steps: list[dict] | None = None  # if None, use session's current_steps


@router.post("/session")
def start_session(req: StartReq) -> dict:
    """Open a planning session. Call after mapping is confirmed (step 3)."""
    try:
        return orchestrator.create_session(req.source_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/session/{session_id}/plan")
def plan(session_id: str, req: PlanReq) -> dict:
    """Run one planning iteration. Auto-retries up to 3× on errors.

    Returns {status, steps, explanation, snapshot, attempts} on success.
    Returns 422 with {error, partial_steps} if all retries fail.
    """
    try:
        result = orchestrator.run_plan(session_id, req.prompt)
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    if result["status"] == "error":
        raise HTTPException(
            422,
            detail={
                "error": result["error"],
                "partial_steps": result["partial_steps"],
                "attempts": result["attempts"],
            },
        )
    return result


@router.post("/session/{session_id}/confirm")
def confirm(session_id: str, req: ConfirmReq) -> dict:
    """Persist the current plan (or a UI-edited override) as a named skill."""
    session = db.get_agent_session(session_id)
    if not session:
        raise HTTPException(404, "session not found")

    steps = req.steps if req.steps is not None else session["current_steps"]
    skill = db.save_skill(
        req.name,
        session["fingerprint"],
        session["canonical_version"],
        steps,
    )
    return {"skill": {**skill, "steps": steps}}
