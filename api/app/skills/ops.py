"""Closed typed-op vocabulary, compiled to DuckDB SQL (DESIGN.md §4).

The LLM never emits saved code — only a plan of these ops. Each op is a
deterministic compiler target, so a skill is diffable, versionable, and
replayable for $0 on a same-shape file (§5).

A skill compiles to a chain of nested SELECTs over a base relation. v0
skills are mostly `map_column` (+ `cast`) derived from confirmed mappings;
the rest of the vocabulary is implemented so the plan can grow without a
schema change.
"""
from __future__ import annotations

import re

ALLOWED_OPS = {
    "map_column", "cast", "parse_date", "normalize_phone",
    "dedupe", "filter", "derive",
}
# Expressions in filter/derive are author-reviewed but still sanitized.
_EXPR_FORBIDDEN = re.compile(
    r"(;|--|/\*|\b(insert|update|delete|drop|alter|create|attach|copy|"
    r"pragma|load|install)\b)",
    re.IGNORECASE,
)


class SkillCompileError(Exception):
    pass


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _check_expr(expr: str) -> str:
    if _EXPR_FORBIDDEN.search(expr):
        raise SkillCompileError(f"unsafe expression: {expr!r}")
    return expr


def validate_steps(steps: list[dict]) -> None:
    for s in steps:
        op = s.get("op")
        if op not in ALLOWED_OPS:
            raise SkillCompileError(f"unknown op: {op!r}")


def steps_from_mapping(mapping: dict, canonical_fields: list[dict]) -> list[dict]:
    """Derive a v0 skill plan from a confirmed mapping.

    {source_col: {to, ...}} -> [map_column ...] + [cast ...] for typed fields.
    """
    type_by_field = {f["name"]: f.get("type", "VARCHAR") for f in canonical_fields}
    steps: list[dict] = []
    for src_col, m in mapping.items():
        to = m.get("to")
        if not to:
            continue
        steps.append({"op": "map_column", "from": src_col, "to": to})
    for f in canonical_fields:
        t = type_by_field.get(f["name"], "VARCHAR")
        if t.upper() == "DATE":
            steps.append({"op": "parse_date", "column": f["name"],
                          "to": f["name"], "format": "auto"})
        elif t.upper() in {"DECIMAL", "DOUBLE", "INTEGER", "BIGINT"}:
            steps.append({"op": "cast", "column": f["name"], "type": t})
    return steps


def compile_steps(steps: list[dict], base_sql: str) -> str:
    """Fold the op list into nested SELECTs over (base_sql)."""
    validate_steps(steps)
    cur = f"(SELECT * FROM ({base_sql}))"

    # 1. column renames first so later ops reference canonical names.
    renames = {s["from"]: s["to"] for s in steps if s["op"] == "map_column"}
    if renames:
        proj = ", ".join(
            f"{_ident(src)} AS {_ident(dst)}" for src, dst in renames.items()
        )
        # keep source_file provenance if present in base
        cur = f"(SELECT {proj}, * EXCLUDE ({', '.join(_ident(s) for s in renames)}) " \
              f"FROM {cur})"

    # 2. scalar column transforms.
    for s in steps:
        op = s["op"]
        if op == "cast":
            col = _ident(s["column"])
            cur = (f"(SELECT * REPLACE (TRY_CAST({col} AS {s['type']}) AS "
                   f"{col}) FROM {cur})")
        elif op == "parse_date":
            col = _ident(s["column"])
            dst = _ident(s.get("to", s["column"]))
            fmt = s.get("format", "auto")
            expr = (f"TRY_CAST({col} AS DATE)" if fmt == "auto"
                    else f"TRY_CAST(strptime(CAST({col} AS VARCHAR), "
                         f"'{fmt}') AS DATE)")
            cur = f"(SELECT * REPLACE ({expr} AS {dst}) FROM {cur})"
        elif op == "normalize_phone":
            col = _ident(s["column"])
            cur = (f"(SELECT * REPLACE (regexp_replace(CAST({col} AS VARCHAR),"
                   f" '[^0-9]', '', 'g') AS {col}) FROM {cur})")
        elif op == "derive":
            expr = _check_expr(s["expr"])
            cur = f"(SELECT *, ({expr}) AS {_ident(s['to'])} FROM {cur})"

    # 3. row-level ops last.
    for s in steps:
        if s["op"] == "filter":
            cur = f"(SELECT * FROM {cur} WHERE {_check_expr(s['predicate'])})"
        elif s["op"] == "dedupe":
            keys = ", ".join(_ident(k) for k in s["keys"])
            cur = (f"(SELECT * EXCLUDE (_rn) FROM (SELECT *, row_number() "
                   f"OVER (PARTITION BY {keys} ORDER BY 1) AS _rn FROM {cur}) "
                   f"WHERE _rn = 1)")
    return f"SELECT * FROM {cur}"
