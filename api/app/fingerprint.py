"""Schema fingerprint + drift classification (DESIGN.md §3, §4).

Fingerprint = sha256 over the sorted set of (normalized_column_name,
inferred_type) pairs. Identical-shaped files collapse to the same hash,
which is what makes the mapping cache and skill auto-detect work.

Normalization is Unicode-aware so Chinese headers are handled: we lower-case,
strip, and collapse any run of non-alphanumeric (incl. CJK-adjacent
punctuation/space) into a single underscore, but keep CJK characters
themselves intact (客户名称 stays 客户名称).
"""
from __future__ import annotations

import hashlib
import re

_NON_WORD = re.compile(r"[^0-9a-z一-鿿]+")


def normalize_name(name: str) -> str:
    n = name.strip().lower()
    n = _NON_WORD.sub("_", n)
    return n.strip("_")


def fingerprint(columns: list[dict]) -> str:
    """columns: [{name, type, ...}] -> stable hash string 'h:<12hex>'."""
    pairs = sorted(
        f"{normalize_name(c['name'])}:{c.get('type', 'unknown')}" for c in columns
    )
    digest = hashlib.sha256("|".join(pairs).encode("utf-8")).hexdigest()
    return f"h:{digest[:12]}"


def classify_drift(
    skill_steps: list[dict], incoming_columns: list[dict]
) -> dict:
    """Compare a skill's required source columns against an incoming file.

    Returns one of:
      {"kind": "exact"}                       -> run silently
      {"kind": "mappable", "missing": [...],  -> needs LLM/heuristic remap
                            "extra": [...]}
      {"kind": "unmappable", "missing": [...]}-> hard stop, field-level diff

    A required source column is one referenced by a `map_column` step. A
    field is considered still satisfiable if an incoming column matches it
    by normalized name (exact rename detection handled upstream by the
    re-mapping step; here we only decide *which* bucket we are in).
    """
    required = [
        s["from"] for s in skill_steps if s.get("op") == "map_column"
    ]
    req_norm = {normalize_name(r): r for r in required}
    incoming_norm = {normalize_name(c["name"]): c["name"] for c in incoming_columns}

    missing = [orig for n, orig in req_norm.items() if n not in incoming_norm]
    extra = [
        orig for n, orig in incoming_norm.items() if n not in req_norm
    ]

    if not missing and not extra:
        return {"kind": "exact"}
    if not missing:
        # Only additive/renamed-away columns; safe to remap.
        return {"kind": "mappable", "missing": [], "extra": extra}

    # Some required columns vanished. If there are unclaimed incoming columns
    # they *might* be renames -> mappable (the remap step decides). If there
    # is nothing left to map a missing required field onto, it is unmappable.
    if len(extra) >= len(missing):
        return {"kind": "mappable", "missing": missing, "extra": extra}
    return {"kind": "unmappable", "missing": missing}
