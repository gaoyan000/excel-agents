"""FastAPI entrypoint — one synchronous process (DESIGN.md §2)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import SETTINGS
from .db import init_db
from .routers import ingest, mapping, query, skills

app = FastAPI(title="Spreadsheet Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=SETTINGS.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Idempotent (CREATE TABLE IF NOT EXISTS) — run at import so the app is
# usable under TestClient/serverless where the startup event may not fire.
init_db()


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_enabled": SETTINGS.llm_enabled,
        "model": SETTINGS.openai_model if SETTINGS.llm_enabled else None,
    }


app.include_router(ingest.router)
app.include_router(mapping.router)
app.include_router(query.router)
app.include_router(skills.router)
