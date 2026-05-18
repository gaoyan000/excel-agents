# Spreadsheet Agent — Design

AI-native spreadsheet ETL + semantic query system. Messy Excel/CSV in → clean
unified table out → natural-language querying → reusable, replayable "skills".

This document is an opinionated counter-design to the original product brief. It
keeps the product instincts that are correct and cuts/sharpens the rest.

---

## 1. Principles (non-negotiable)

1. **AI plans, deterministic code executes, humans confirm mappings.** The LLM
   never produces stored Python/SQL and never silently transforms data.
2. **The moat is transformation memory, not chat.** Skills + a project-scoped
   semantic layer that the workspace evolves over time.
3. **The wedge is "messy spreadsheet → clean unified table for non-analysts,"**
   not "AI analyst for everything." Stay narrow.
4. **Re-runs are pure functions of `(raw input, skill definition)`.** No manual
   cell edits ever survive into output; corrections are expressed as rules.
5. **Determinism and cost are caching properties, not prompting hopes.**

---

## 2. MVP stack (deliberately minimal)

One Next.js frontend + one FastAPI process + DuckDB + Postgres. No queue, no
broker, no graph framework, no vector DB. Each is added back only when a
concrete limit forces it.

| Layer          | Choice                                   | Note |
|----------------|------------------------------------------|------|
| Frontend       | Next.js + Tailwind                       | Read-only preview + mapping confirmation table only in v0 |
| Backend        | FastAPI, single process, synchronous     | Python because DuckDB / data tooling is Python-native |
| Data engine    | DuckDB                                    | Transform **and** query — one substrate |
| Transformation | DuckDB SQL compiled from typed ops        | No pandas/polars in MVP |
| AI             | One provider API, structured-output mode  | Planning + mapping + NL→SQL only |
| Metadata       | Postgres (SQLite acceptable for v0)       | Off the data hot path |
| Orchestration  | A function, not a framework               | Add a graph lib only when loops branch |

### Explicitly cut from the original brief

| Proposed              | Verdict      | Why |
|-----------------------|--------------|-----|
| Celery / Temporal / Redis | Cut       | Business spreadsheets are usually <50MB; process synchronously with a progress spinner. Add a queue when a real file forces it. |
| LangGraph             | Cut          | Orchestration is one planning call → structured plan → deterministic executor. That's a function, not a graph. |
| pgvector / vectors    | Cut          | Mapping needs the LLM to see column names + samples, not embeddings. Skill lookup is a fuzzy hash match, not ANN. |
| Pandas + Polars + DuckDB | Pick DuckDB only | Three engines = three null/type/date semantics = "demo works, re-run breaks." |
| S3                    | Optional     | Local disk / single bucket until multi-tenant. |

---

## 3. The hard problem: idempotent re-runs under schema drift

A skill is only valuable if applying it to next month's file is a pure function
of `(raw input, skill definition)`. This is the centerpiece, not an afterthought.

**Constraints it forces:**

- **No manual cell edits in output, ever.** A user "fixing a cell" must become a
  *rule* (a `derive`/`replace` step appended to the skill), not a stored value.
  This is a product rule, not just implementation.
- **Every skill binds to a schema fingerprint.** Re-run begins with drift
  detection (state machine below).
- **The canonical schema is versioned.** Skill steps reference canonical fields;
  schema migration is an explicit, reviewable event.

### Drift state machine (on skill re-run)

```
incoming file → compute fingerprint
  ├─ exact fingerprint match            → run silently
  ├─ mappable drift (rename / new col)  → reopen Mapping Confirmation UI,
  │                                        pre-filled with LLM proposals;
  │                                        on confirm, skill mapping auto-updates
  │                                        (learning loop closes)
  └─ unmappable (missing required field)→ hard stop + clear field-level diff,
                                           no garbage output
```

Shipping a skill without the mappable/unmappable fallback is worse than no
skill — it breaks silently on next month's file.

---

## 4. Skills = typed ops (closed vocabulary)

The LLM emits a constrained plan; it never emits saved code. Each op is a
deterministic compiler target → DuckDB SQL.

```json
{
  "skill_id": "amazon-monthly-cleanup",
  "version": 3,
  "canonical_schema_version": 2,
  "applies_to_fingerprint": "h:9f3a...",
  "steps": [
    {"op": "map_column",      "from": "Cust Name",      "to": "customer_name"},
    {"op": "parse_date",      "column": "Date Purchased","to": "order_date", "format": "auto"},
    {"op": "cast",            "column": "Amount USD",    "to": "revenue", "type": "decimal"},
    {"op": "normalize_phone", "column": "phone",         "region": "US"},
    {"op": "dedupe",          "keys": ["customer_name","order_date","revenue"]},
    {"op": "filter",          "predicate": "email_is_valid(email)"}
  ]
}
```

`applies_to_fingerprint` = hash over sorted `(normalized_colname, inferred_type)`
pairs. New uploads fuzzy-match against known fingerprints to suggest a skill.

Benefits: diffable, versionable, editable in UI without an LLM, cheap to re-run,
impossible to "go rogue" (closed op set). The LLM's only jobs are producing this
plan and the column mapping — both human-reviewable before execution.

---

## 5. Determinism + cost: hash-cache every LLM decision

Three content-hash–keyed caches:

| Cache            | Key                                   | Effect |
|------------------|---------------------------------------|--------|
| Schema inference | file content hash                     | Identical file never re-inferred |
| Column mapping   | schema fingerprint                    | Same-shaped files reuse confirmed mappings, **0 LLM calls** — this *is* the transformation memory |
| NL→SQL           | `(question, canonical_schema_version)`| Repeated questions are free and byte-identical |

Target property: re-running last month's skill on a same-shape file costs **$0
in tokens** and produces byte-identical output.

---

## 6. Data model

```
sources           (id, project_id, filename, content_hash, raw_path, uploaded_at)
source_schemas    (source_id, columns[], inferred_types[], samples[], fingerprint)
canonical_schema  (project_id, version, fields[: name, type, description, synonyms])
mappings          (project_id, fingerprint, source_col → canonical_field,
                   confidence, confirmed_by, confirmed_at)        ← reusable memory
skills            (id, project_id, version, applies_to_fingerprint,
                   canonical_schema_version, steps[])
runs              (id, skill_id, input_source_ids[], status,
                   drift_report, output_snapshot_path, created_at)
```

`mappings` and `skills` are the only tables that constitute the moat. Everything
else is bookkeeping. The canonical schema is **project-scoped and user-evolved**,
not a global ontology — that is both the lock-in and the simpler design.

---

## 7. Pipeline (MVP, synchronous)

```
upload N files
  → DuckDB introspect: read_csv_auto / st_read (xlsx), sample rows, infer types
  → compute per-file fingerprint
  → cache check: known fingerprint? load confirmed mapping, skip LLM
  → else: ONE LLM call → {proposed canonical schema, per-column mapping + confidence}
  → Mapping Confirmation UI (low-confidence rows forced visible)   ← human gate
  → build DuckDB view: UNION of mapped sources → canonical table
  → NL query box → LLM→SQL (read-only validated) → result table + shown SQL
  → "Save as skill": persist {canonical schema vN, mappings, typed steps}
  → "Apply skill to new upload": re-run → drift state machine (§3)
```

### Guardrails

- **NL→SQL safety:** parse generated SQL; reject anything that isn't a single
  read-only `SELECT`; cap result rows; execute on a read-only DuckDB connection.
  Always display the SQL (trust + editability).
- **Mapping UI scope:** confirm *column mappings*, not cells. An editable
  spreadsheet grid is a v2 rabbit hole — under re-run semantics, hand-edited
  cells are actively harmful (§3). v0 UI = read-only preview + mapping table.

---

## 8. v0 build order

1. Upload N CSV/XLSX → DuckDB introspection
2. One LLM call: propose canonical schema + column mapping with confidence
3. Mapping confirmation UI (mappings only)
4. UNION view → unified canonical table preview
5. NL query → validated read-only SQL → result table (SQL shown)
6. Save as skill (typed steps + fingerprint + mappings)
7. Apply skill to new upload → drift detection → fall back to step 3 on drift,
   then re-record

Steps 6–7 turn a notebook into a product. Do not ship without the drift
fallback in 7.

---

## 9. Summary of divergence from the original brief

1. **Radically cut the MVP stack** to match the brief's own "don't build
   everything" instruction (no queue/graph/vectors/multi-engine).
2. **Make idempotent re-run under schema drift the centerpiece**, with an
   explicit state machine, rather than an afterthought ("replayable").
3. **Constrain skills to a closed typed-op vocabulary** — no saved freeform
   code, ever.
4. **Make determinism and cost a caching property**, not a prompting hope.


MVP phase:

Phase 0 — Scaffold (½ day)

  - Repo layout: /api (FastAPI), /web (Next.js), /api/skills (typed-op compiler later).
  - Python env with duckdb, fastapi, uvicorn, one LLM SDK; Postgres (or SQLite) for metadata.
  - Health endpoint + Next.js page hitting it.
  - Done when: GET /health returns OK from the browser.

  Phase 1 — Ingestion + introspection (1 day)

  - Upload endpoint: accept N CSV/XLSX, store raw file, compute content_hash.
  - DuckDB introspection: read_csv_auto / st_read, sample ~20 rows, infer per-column type.
  - Compute the schema fingerprint (hash over sorted (normalized_colname, type)).
  - Persist sources + source_schemas.
  - Done when: upload 3 files → API returns per-column types, samples, and a fingerprint each.

  Phase 2 — AI mapping + confirmation gate (2 days)

  - One LLM call (structured output): input = each file's columns + samples; output = proposed canonical schema +
  per-column mapping + confidence.
  - Hash-cache keyed by fingerprint (same shape → 0 LLM calls).
  - Mapping Confirmation UI: table of source_col → canonical_field, low-confidence rows forced visible, user
  edits/confirms.
  - Persist canonical_schema (v1) + confirmed mappings.
  - Done when: confirming mappings for 3 files persists a versioned canonical schema; re-uploading an identical file
  makes zero LLM calls.

  Phase 3 — Unified table + NL query (2 days)
  
  - Build a DuckDB view = UNION of mapped sources → canonical table; render read-only preview.
  - NL query box → LLM→SQL against the canonical schema + data dictionary.
  - Guardrail: parse SQL, reject anything not a single read-only SELECT, cap rows, read-only connection. Show the
  generated SQL.
  - Hash-cache keyed by (question, schema_version).
  - Done when: "total revenue by customer" returns a correct result table with the SQL displayed; same question twice =
  identical, free.

  Phase 4 — Skills (2 days)

  - Typed-op vocabulary + compiler: each op (map_column, parse_date, cast, normalize_phone, dedupe, filter, derive) →
  DuckDB SQL.
  - "Save as skill": persist {canonical_schema_version, applies_to_fingerprint, steps[]}.
  - "Apply skill": recompile steps → SQL → execute on a fresh upload.
  - Done when: save a skill from one session, apply it to a same-shape file, get byte-identical output with no LLM
  calls.

  Phase 5 — Drift state machine (2 days) — the make-or-break phase

  - On apply: compare incoming fingerprint to applies_to_fingerprint.
    - Exact → run silently.
    - Mappable drift → reopen Phase 2 UI pre-filled with LLM proposals; on confirm, skill mapping auto-updates.
    - Unmappable (missing required field) → hard stop + field-level diff, no output.
  - Done when: rename a column in the input file → app detects drift, walks you through re-confirm, updates the skill,
  then produces correct output. Delete a required column → clean hard stop, no garbage.

  Phase 6 — Polish (1 day)
  
  - Progress spinner for synchronous processing, error states, empty states.
  - runs table: input sources, status, drift report, output snapshot.
  - Done when: the full loop (upload → map → query → save skill → apply to new file → handle drift) works end-to-end
  without manual DB pokes.