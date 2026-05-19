# Spreadsheet Agent / 表格智能体

AI-native spreadsheet ETL + semantic query. Messy Excel/CSV in → AI column
alignment → unified table → natural-language query → reusable **skills** that
replay deterministically and survive schema drift.

Architecture and rationale: see [DESIGN.md](./DESIGN.md). This MVP implements
the full critical path (Phases 1–5) from the design.

- **Bilingual (中文 / English)** end to end — Chinese headers, Chinese NL
  queries, bilingual canonical descriptions, and a zh/en UI toggle. The first
  client is China-based.
- **Runs fully offline without an API key** via a deterministic bilingual
  heuristic. A key only upgrades mapping / NL→SQL quality.

## Layout

```
api/   FastAPI + DuckDB + SQLite (the critical path; verified by smoke test)
web/   Next.js + Tailwind, zh/en toggle (read-only preview + mapping table)
```

## Run the backend

```bash
cd api
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # optional: add OPENAI_API_KEY for better mapping/NL-SQL
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Verify end to end (no API key needed):

```bash
cd api && .venv/bin/python -m tests.smoke_test
```

The smoke test exercises: bilingual ingest (EN + 中文) → heuristic mapping →
confirm → cache reuse → unified preview → query fallback → save skill →
exact replay → **drift detection (mappable) → remap learning loop** → the
typed-op compiler (dedupe).

## Run the frontend

```bash
cd web
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev                        # http://localhost:3000
```

## Key endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/ingest` | Upload N files → introspect + fingerprint |
| POST | `/api/mapping/propose` | Bilingual mapping (cache → 0 LLM calls if known) |
| POST | `/api/mapping/confirm` | Persist canonical schema vN + confirmed mapping |
| POST | `/api/table/preview` | UNION view → unified rows |
| POST | `/api/query` | NL (zh/en) → validated read-only SQL → result |
| POST | `/api/skills` | Save skill (typed ops) from confirmed mapping |
| POST | `/api/skills/{id}/apply` | Re-run + drift state machine |
| POST | `/api/skills/{id}/remap` | Confirm remap → close the learning loop |

## Design invariants enforced in code

- **AI plans, code executes, humans confirm** — LLM only proposes mapping /
  SQL; transforms are deterministic typed ops compiled to DuckDB SQL.
- **Idempotent re-runs under drift** — `exact` runs silently, `mappable`
  reopens the confirm UI then auto-updates the skill, `unmappable` hard-stops
  with a field-level diff (`api/app/fingerprint.py`, `routers/skills.py`).
- **Determinism = caching** — schema/mapping/NL-SQL decisions are hash-cached
  (`api/app/cache.py`); a same-shape re-run costs $0 in tokens.
- **Closed op vocabulary** — no saved freeform code
  (`api/app/skills/ops.py`).

## Deploying on Cloudflare (DuckDB stays native)

DuckDB cannot run in Workers (native lib). Pattern: **Pages** (frontend) +
**Containers** or external VM (the `api/` image, DuckDB native) + **R2**
(raw files) + SQLite-on-volume or **D1** (metadata).

Flip storage to R2 with no code change — just env:

```bash
STORAGE_BACKEND=r2
R2_ENDPOINT=<accountid>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=spreadsheet-agent
pip install boto3
```

`local` (default) and `r2` are the only branch points — `STORAGE_DIR` vs an
R2 bucket. CSV streams from R2 via DuckDB `httpfs`; xlsx is cached locally on
first read (`api/app/storage.py`).

## Not in this MVP (deferred per DESIGN.md §2)

Queue/worker, vector search, graph orchestration, editable spreadsheet grid,
multi-tenant auth. Added back only when a concrete limit forces it.
