"""Build the initial context for a skill-planning session.

Called once after step 3 (mapping confirmed). Produces:
  schema    — canonical fields (types + descriptions)
  snapshot  — 20 sample rows after applying the base mapping steps
  base_steps — map_column + inferred cast/parse_date from confirmed mapping

The snapshot is what the LLM sees as "current state of the data" on the
first planning call. After each successful plan it advances to the new output.
"""
from __future__ import annotations

from .. import db, duck
from ..skills.ops import compile_steps, steps_from_mapping


def _rows_to_dicts(columns: list[str], rows: list) -> list[dict]:
    return [
        {col: (str(val) if val is not None else None) for col, val in zip(columns, row)}
        for row in rows
    ]


def compute_snapshot(steps: list[dict], source_paths: list[str]) -> list[dict]:
    """Apply steps to each source file, UNION ALL, return 20 sample rows."""
    parts = [
        f"({compile_steps(steps, duck.base_scan(path))})"
        for path in source_paths
    ]
    unified = "\nUNION ALL\n".join(parts)
    result = duck.preview(unified, limit=20)
    return _rows_to_dicts(result["columns"], result["rows"])


def build_context(source_ids: list[int]) -> dict:
    """Return session context for source_ids (all must share confirmed mapping).

    Raises ValueError if sources or mapping are not ready.
    """
    sources = [db.get_source(sid) for sid in source_ids]
    sources = [s for s in sources if s and "columns" in s]
    if not sources:
        raise ValueError("no valid sources found")

    canon = db.latest_canonical()
    if not canon:
        raise ValueError("no confirmed canonical schema — complete step 3 first")

    src = sources[0]
    mapping_rec = db.get_mapping(src["fingerprint"])
    if not mapping_rec or not mapping_rec["confirmed"]:
        raise ValueError("mapping not confirmed — complete step 3 first")

    confirmed_mapping = mapping_rec["mapping"]  # {source_col: {to, ...}}
    base_steps = steps_from_mapping(confirmed_mapping, canon["fields"])

    source_paths = [s["raw_path"] for s in sources]
    snapshot = compute_snapshot(base_steps, source_paths)

    return {
        "schema": canon["fields"],
        "canonical_version": canon["version"],
        "snapshot": snapshot,
        "base_steps": base_steps,
        "source_paths": source_paths,
        "fingerprint": src["fingerprint"],
    }
