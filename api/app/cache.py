"""Content-hash cache for every LLM decision (DESIGN.md §5).

Determinism + cost are a caching property, not a prompting hope. Keys are
sha256 over the exact inputs, so a repeated decision is free and identical.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .db import conn


def cache_key(kind: str, payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return f"{kind}:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cache_get(key: str) -> Any | None:
    with conn() as c:
        r = c.execute(
            "SELECT value_json FROM llm_cache WHERE key=?", (key,)
        ).fetchone()
        return json.loads(r["value_json"]) if r else None


def cache_put(key: str, value: Any) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO llm_cache (key, value_json, created_at)"
            " VALUES (?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )
