"""Retry orchestrator: planner → compile → execute → advance snapshot.

One call to run_plan drives one user-visible iteration:
  1. Call the LLM planner (with optional error context on retries).
  2. Compile the returned steps[] → DuckDB SQL.
  3. Execute on sample data → new snapshot.
  4. If compile or execute fails, feed the error back into the next LLM call.
  5. After MAX_RETRIES failures, surface the error to the caller.

The auto-retry loop is invisible to the UI — the caller just sees
{status, steps, snapshot, attempts} or {status:"error", error, partial_steps}.
"""
from __future__ import annotations

import uuid

from .. import db, duck
from ..skills.ops import compile_steps, SkillCompileError
from . import context as ctx_mod
from . import planner

MAX_RETRIES = 3


def _compile_and_preview(steps: list[dict], source_paths: list[str]) -> list[dict]:
    """Compile steps against each source, UNION ALL, return 20 rows as dicts."""
    parts = [
        f"({compile_steps(steps, duck.base_scan(path))})"
        for path in source_paths
    ]
    unified = "\nUNION ALL\n".join(parts)
    result = duck.preview(unified, limit=20)
    return [
        {col: (str(val) if val is not None else None)
         for col, val in zip(result["columns"], row)}
        for row in result["rows"]
    ]


def create_session(source_ids: list[int]) -> dict:
    """Build context from confirmed mapping and persist a new planning session."""
    ctx = ctx_mod.build_context(source_ids)
    session_id = str(uuid.uuid4())
    db.create_agent_session(
        session_id=session_id,
        source_ids=source_ids,
        schema=ctx["schema"],
        canonical_version=ctx["canonical_version"],
        source_paths=ctx["source_paths"],
        fingerprint=ctx["fingerprint"],
        base_steps=ctx["base_steps"],
        current_steps=ctx["base_steps"],
        current_snapshot=ctx["snapshot"],
    )
    return {
        "session_id": session_id,
        "schema": ctx["schema"],
        "snapshot": ctx["snapshot"],
        "current_steps": ctx["base_steps"],
    }


def run_plan(session_id: str, prompt: str) -> dict:
    """Drive one planning loop iteration with auto-retry.

    Returns a dict with status "ok" or "error". On "ok", current session
    state is advanced (snapshot + steps + history updated in DB).
    """
    session = db.get_agent_session(session_id)
    if not session:
        raise ValueError(f"session {session_id!r} not found")

    error: str | None = None
    last_steps: list[dict] = session["current_steps"]

    for attempt in range(MAX_RETRIES + 1):
        steps, explanation = planner.plan_steps(
            schema=session["schema"],
            snapshot=session["current_snapshot"],
            history=session["history"],
            prompt=prompt,
            base_steps=session["base_steps"],
            error=error,
        )
        try:
            new_snapshot = _compile_and_preview(steps, session["source_paths"])
        except (SkillCompileError, Exception) as exc:
            error = str(exc)
            last_steps = steps
            continue  # retry with error in context

        # Success — persist and return
        new_history = session["history"] + [
            {"role": "user", "content": prompt},
            {
                "role": "assistant",
                "content": explanation or f"Applied {len(steps)} steps.",
                # Store op names so the next call can refer to what was done
                "ops": [s["op"] for s in steps],
            },
        ]
        db.update_agent_session(
            session_id=session_id,
            current_steps=steps,
            current_snapshot=new_snapshot,
            history=new_history,
        )
        return {
            "status": "ok",
            "steps": steps,
            "explanation": explanation,
            "snapshot": new_snapshot,
            "attempts": attempt + 1,
        }

    # All retries exhausted
    return {
        "status": "error",
        "error": error,
        "partial_steps": last_steps,
        "attempts": MAX_RETRIES + 1,
    }
