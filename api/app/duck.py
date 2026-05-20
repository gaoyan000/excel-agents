"""DuckDB: the single substrate for introspection, transform, and query
(DESIGN.md §2 — no pandas/polars, one set of type/null/date semantics).
"""
from __future__ import annotations

import re

import duckdb

from . import storage
from .config import SETTINGS

_ROW_CAP = 1000
_SAMPLE_N = 5
# A single read-only SELECT, no statement terminator, no DDL/DML keywords.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|"
    r"call|export|install|load|set)\b",
    re.IGNORECASE,
)


def _scan_expr(path: str) -> str:
    """DuckDB table function to read a file by extension.

    `path` is the stored locator: a local path, or an s3:// URI when
    STORAGE_BACKEND=r2. CSV/TSV stream directly from R2 via httpfs.
    Excel files (.xls / .xlsx) are converted to CSV in Python at ingest
    time (see app/excel.py) so DuckDB never sees them directly — that
    keeps header detection and subtotal-row filtering in one place.
    """
    p = path.lower()
    if p.endswith(".csv") or p.endswith(".tsv") or p.endswith(".txt"):
        return f"read_csv_auto('{path}', sample_size=-1, all_varchar=false)"
    raise ValueError(f"Unsupported file type: {path}")


def base_scan(path: str) -> str:
    """Public: a SELECT over one raw file, used as a skill's base relation."""
    return f"SELECT * FROM {_scan_expr(path)}"


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    # The Excel extension is no longer needed at the DuckDB layer —
    # Excel files are converted to CSV in Python at ingest time
    # (app/excel.py), so DuckDB only ever reads CSV here. CSV/TSV ingest
    # has no extension dependency.
    if SETTINGS.r2_enabled:
        # R2 is S3-compatible; httpfs lets DuckDB read s3:// CSV directly.
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(f"SET s3_endpoint='{SETTINGS.r2_endpoint}'")
        con.execute("SET s3_region='auto'")
        con.execute(f"SET s3_access_key_id='{SETTINGS.r2_access_key}'")
        con.execute(f"SET s3_secret_access_key='{SETTINGS.r2_secret}'")
        con.execute("SET s3_url_style='path'")
    return con


def introspect(path: str) -> list[dict]:
    """Return [{name, type, samples:[str,...]}] for a raw file."""
    con = _connect()
    try:
        rel = con.sql(f"SELECT * FROM {_scan_expr(path)}")
        col_names = rel.columns
        col_types = [str(t) for t in rel.types]
        sample_rows = con.sql(
            f"SELECT * FROM {_scan_expr(path)} LIMIT {_SAMPLE_N}"
        ).fetchall()
        out: list[dict] = []
        for i, (name, typ) in enumerate(zip(col_names, col_types)):
            samples = [
                "" if r[i] is None else str(r[i]) for r in sample_rows
            ]
            out.append({"name": name, "type": typ, "samples": samples})
        return out
    finally:
        con.close()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_unified_sql(sources: list[dict], mapping: dict) -> str:
    """UNION ALL of each source projected onto the canonical schema.

    `mapping` is {source_col: canonical_field}. Canonical fields not present
    in a given file are emitted as NULL so the union stays rectangular. A
    `source_file` column is always added for provenance.

    Every projected column is CAST to VARCHAR. Real-world spreadsheets
    routinely infer DIFFERENT types for the SAME canonical name across
    files — e.g. a column called 日期 may be parsed as TIMESTAMP in one
    file (clean dates) and DOUBLE in another (serial numbers wearing the
    same header). DuckDB's UNION ALL then fails with a ConversionException
    trying to merge incompatible types. VARCHAR is the universal lowest
    common denominator. Schema introspection (and skills) still see the
    inferred per-source types; only the cross-file VIEW is text. NL->SQL
    is told about this so it casts inside SUM/WHERE/ORDER BY.
    """
    canonical_fields = sorted(set(mapping.values()))
    selects: list[str] = []
    for src in sources:
        present = {c["name"] for c in src["columns"]}
        cols_sql = []
        for field in canonical_fields:
            src_col = next(
                (s for s, t in mapping.items() if t == field and s in present),
                None,
            )
            if src_col:
                cols_sql.append(
                    f"CAST({_quote_ident(src_col)} AS VARCHAR) "
                    f"AS {_quote_ident(field)}"
                )
            else:
                cols_sql.append(
                    f"CAST(NULL AS VARCHAR) AS {_quote_ident(field)}"
                )
        cols_sql.append(f"{_quote_str(src['filename'])} AS source_file")
        selects.append(
            f"SELECT {', '.join(cols_sql)} FROM {_scan_expr(src['raw_path'])}"
        )
    return "\nUNION ALL\n".join(selects)


def preview(unified_sql: str, limit: int = 50) -> dict:
    con = _connect()
    try:
        rel = con.sql(f"SELECT * FROM ({unified_sql}) LIMIT {int(limit)}")
        return {"columns": rel.columns, "rows": rel.fetchall()}
    finally:
        con.close()


class SqlRejected(Exception):
    pass


def validate_select(sql: str) -> str:
    """Guardrail (DESIGN.md §7): a single read-only query only.

    Accepts the DuckDB read-only statement heads — SELECT, WITH (CTE),
    PIVOT, UNPIVOT, and FROM-first SELECT. The _FORBIDDEN regex still
    blocks DML/DDL keywords inside the body, so a PIVOT that smuggles
    an INSERT inside a subquery is still rejected.
    """
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise SqlRejected("multiple statements")
    if not re.match(r"^(select|with|pivot|unpivot|from)\b", s, re.IGNORECASE):
        raise SqlRejected("not a SELECT/WITH/PIVOT/UNPIVOT/FROM")
    if _FORBIDDEN.search(s):
        raise SqlRejected("contains a write/DDL keyword")
    return s


def run_query(unified_sql: str, select_sql: str) -> dict:
    """Run `select_sql` against the unified table exposed as `t`."""
    safe = validate_select(select_sql)
    con = _connect()
    try:
        con.execute(f"CREATE TEMP VIEW t AS {unified_sql}")
        rel = con.sql(f"SELECT * FROM ({safe}) LIMIT {_ROW_CAP}")
        return {"columns": rel.columns, "rows": rel.fetchall(), "sql": safe}
    finally:
        con.close()
