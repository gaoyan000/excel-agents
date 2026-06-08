"""SQLite metadata store (DESIGN.md §6).

v0 uses SQLite — zero setup, off the data hot path (DuckDB does the data
work). The Postgres swap is a connection-string change later. JSON columns
keep the schema thin; structured access lives in the router layer.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import SETTINGS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    uploaded_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS source_schemas (
    source_id INTEGER PRIMARY KEY,
    columns_json TEXT NOT NULL,   -- [{name, type, samples:[...]}]
    fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canonical_schema (
    project_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    fields_json TEXT NOT NULL,    -- [{name, type, desc_en, desc_zh, synonyms:[...]}]
    created_at REAL NOT NULL,
    PRIMARY KEY (project_id, version)
);
CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    mapping_json TEXT NOT NULL,   -- {source_col: {to, confidence}}
    confirmed INTEGER NOT NULL DEFAULT 0,
    confirmed_at REAL,
    UNIQUE (project_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    applies_to_fingerprint TEXT NOT NULL,
    canonical_schema_version INTEGER NOT NULL,
    steps_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL,
    input_source_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,         -- ok | drift_mappable | drift_unmappable
    drift_report_json TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL,
    source_ids_json TEXT NOT NULL,
    schema_json TEXT NOT NULL,
    canonical_version INTEGER NOT NULL,
    source_paths_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    base_steps_json TEXT NOT NULL DEFAULT '[]',
    current_steps_json TEXT NOT NULL DEFAULT '[]',
    current_snapshot_json TEXT NOT NULL DEFAULT '[]',
    history_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
"""

DEFAULT_PROJECT_ID = 1


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(SETTINGS.metadata_db)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(_SCHEMA)
        row = c.execute(
            "SELECT 1 FROM projects WHERE id=?", (DEFAULT_PROJECT_ID,)
        ).fetchone()
        if not row:
            c.execute(
                "INSERT INTO projects (id, name, created_at) VALUES (?,?,?)",
                (DEFAULT_PROJECT_ID, "default", time.time()),
            )


# --- small typed helpers (avoid sprinkling SQL across routers) -------------

def insert_source(filename: str, content_hash: str, raw_path: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO sources (project_id, filename, content_hash, raw_path,"
            " uploaded_at) VALUES (?,?,?,?,?)",
            (DEFAULT_PROJECT_ID, filename, content_hash, raw_path, time.time()),
        )
        return int(cur.lastrowid)


def save_source_schema(source_id: int, columns: list[dict], fingerprint: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO source_schemas (source_id, columns_json,"
            " fingerprint) VALUES (?,?,?)",
            (source_id, json.dumps(columns, ensure_ascii=False), fingerprint),
        )


def get_source(source_id: int) -> dict[str, Any] | None:
    with conn() as c:
        s = c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not s:
            return None
        sc = c.execute(
            "SELECT * FROM source_schemas WHERE source_id=?", (source_id,)
        ).fetchone()
        out = dict(s)
        if sc:
            out["columns"] = json.loads(sc["columns_json"])
            out["fingerprint"] = sc["fingerprint"]
        return out


def latest_canonical(project_id: int = DEFAULT_PROJECT_ID) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT * FROM canonical_schema WHERE project_id=? "
            "ORDER BY version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if not r:
            return None
        return {"version": r["version"], "fields": json.loads(r["fields_json"])}


def save_canonical(fields: list[dict], project_id: int = DEFAULT_PROJECT_ID) -> int:
    cur = latest_canonical(project_id)
    version = (cur["version"] + 1) if cur else 1
    with conn() as c:
        c.execute(
            "INSERT INTO canonical_schema (project_id, version, fields_json,"
            " created_at) VALUES (?,?,?,?)",
            (project_id, version, json.dumps(fields, ensure_ascii=False), time.time()),
        )
    return version


def get_mapping(fingerprint: str, project_id: int = DEFAULT_PROJECT_ID) -> dict | None:
    with conn() as c:
        r = c.execute(
            "SELECT * FROM mappings WHERE project_id=? AND fingerprint=?",
            (project_id, fingerprint),
        ).fetchone()
        if not r:
            return None
        return {
            "mapping": json.loads(r["mapping_json"]),
            "confirmed": bool(r["confirmed"]),
        }


def upsert_mapping(
    fingerprint: str,
    mapping: dict,
    confirmed: bool,
    project_id: int = DEFAULT_PROJECT_ID,
) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO mappings (project_id, fingerprint, mapping_json,"
            " confirmed, confirmed_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(project_id, fingerprint) DO UPDATE SET "
            "mapping_json=excluded.mapping_json, confirmed=excluded.confirmed,"
            " confirmed_at=excluded.confirmed_at",
            (
                project_id,
                fingerprint,
                json.dumps(mapping, ensure_ascii=False),
                int(confirmed),
                time.time() if confirmed else None,
            ),
        )


def save_skill(
    name: str,
    applies_to_fingerprint: str,
    canonical_schema_version: int,
    steps: list[dict],
    project_id: int = DEFAULT_PROJECT_ID,
) -> dict:
    with conn() as c:
        prev = c.execute(
            "SELECT MAX(version) v FROM skills WHERE project_id=? AND name=?",
            (project_id, name),
        ).fetchone()
        version = (prev["v"] + 1) if prev and prev["v"] else 1
        cur = c.execute(
            "INSERT INTO skills (project_id, name, version,"
            " applies_to_fingerprint, canonical_schema_version, steps_json,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (
                project_id,
                name,
                version,
                applies_to_fingerprint,
                canonical_schema_version,
                json.dumps(steps, ensure_ascii=False),
                time.time(),
            ),
        )
        return {"id": int(cur.lastrowid), "name": name, "version": version}


def list_skills(project_id: int = DEFAULT_PROJECT_ID) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, name, version, applies_to_fingerprint,"
            " canonical_schema_version, created_at FROM skills "
            "WHERE project_id=? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_skill(skill_id: int) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        if not r:
            return None
        out = dict(r)
        out["steps"] = json.loads(r["steps_json"])
        return out


def update_skill_steps(skill_id: int, steps: list[dict], fingerprint: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE skills SET steps_json=?, applies_to_fingerprint=? WHERE id=?",
            (json.dumps(steps, ensure_ascii=False), fingerprint, skill_id),
        )


def record_run(
    skill_id: int, input_source_ids: list[int], status: str, drift: dict | None
) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO runs (skill_id, input_source_ids_json, status,"
            " drift_report_json, created_at) VALUES (?,?,?,?,?)",
            (
                skill_id,
                json.dumps(input_source_ids),
                status,
                json.dumps(drift, ensure_ascii=False) if drift else None,
                time.time(),
            ),
        )
        return int(cur.lastrowid)


# --- agent session helpers -------------------------------------------------

def create_agent_session(
    session_id: str,
    source_ids: list[int],
    schema: list[dict],
    canonical_version: int,
    source_paths: list[str],
    fingerprint: str,
    base_steps: list[dict],
    current_steps: list[dict],
    current_snapshot: list[dict],
    project_id: int = DEFAULT_PROJECT_ID,
) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO agent_sessions (id, project_id, source_ids_json,"
            " schema_json, canonical_version, source_paths_json, fingerprint,"
            " base_steps_json, current_steps_json, current_snapshot_json,"
            " history_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                project_id,
                json.dumps(source_ids),
                json.dumps(schema, ensure_ascii=False),
                canonical_version,
                json.dumps(source_paths),
                fingerprint,
                json.dumps(base_steps, ensure_ascii=False),
                json.dumps(current_steps, ensure_ascii=False),
                json.dumps(current_snapshot, ensure_ascii=False),
                "[]",
                time.time(),
            ),
        )


def get_agent_session(session_id: str) -> dict[str, Any] | None:
    with conn() as c:
        r = c.execute(
            "SELECT * FROM agent_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not r:
            return None
        return {
            "session_id": r["id"],
            "schema": json.loads(r["schema_json"]),
            "canonical_version": r["canonical_version"],
            "source_paths": json.loads(r["source_paths_json"]),
            "fingerprint": r["fingerprint"],
            "base_steps": json.loads(r["base_steps_json"]),
            "current_steps": json.loads(r["current_steps_json"]),
            "current_snapshot": json.loads(r["current_snapshot_json"]),
            "history": json.loads(r["history_json"]),
        }


def update_agent_session(
    session_id: str,
    current_steps: list[dict],
    current_snapshot: list[dict],
    history: list[dict],
) -> None:
    with conn() as c:
        c.execute(
            "UPDATE agent_sessions SET current_steps_json=?,"
            " current_snapshot_json=?, history_json=? WHERE id=?",
            (
                json.dumps(current_steps, ensure_ascii=False),
                json.dumps(current_snapshot, ensure_ascii=False),
                json.dumps(history, ensure_ascii=False),
                session_id,
            ),
        )
