"""LLM skill planner: schema + snapshot + prompt → steps[].

Uses json_object mode (not strict schema) because step shapes vary by op.
The closed-vocabulary enforcement and SQL safety are handled downstream by
compile_steps / validate_steps in skills/ops.py — those are the real guards.

Falls back to base_steps if OPENAI_API_KEY is absent, keeping the pipeline
runnable offline (same pattern as llm.py mapping + NL→SQL fallbacks).
"""
from __future__ import annotations

import json

from ..config import SETTINGS
from ..skills.ops import ALLOWED_OPS

_SYSTEM = """\
You are a data transformation planner for a spreadsheet ETL tool.
Produce a COMPLETE list of typed transformation ops for the given data.

AVAILABLE OPS — use ONLY these exact op names:
  map_column     : {"op":"map_column","from":"<src>","to":"<canonical>"}
  cast           : {"op":"cast","column":"<col>","type":"DECIMAL|DOUBLE|INTEGER|BIGINT|DATE|VARCHAR"}
  parse_date     : {"op":"parse_date","column":"<col>","to":"<col>","format":"auto|<strptime>"}
  normalize_phone: {"op":"normalize_phone","column":"<col>"}
  dedupe         : {"op":"dedupe","keys":["<col1>","<col2>"]}
  filter         : {"op":"filter","predicate":"<DuckDB boolean expression>"}
  derive         : {"op":"derive","expr":"<DuckDB expression>","to":"<new_col>"}

RULES:
1. Output the FULL steps[] — replaces the previous plan entirely.
2. Always keep all map_column steps unless the user explicitly removes one.
3. map_column steps must come first in the list.
4. filter/derive expressions: valid DuckDB SQL only; no semicolons, no DDL/DML.
5. dedupe/filter/derive reference CANONICAL column names (post-mapping).
6. Only include fields relevant to the op (e.g. no "column" on a dedupe step).

Respond with JSON: {"steps": [...], "explanation": "<one sentence>"}
"""


def _client():
    from openai import OpenAI
    return OpenAI(api_key=SETTINGS.openai_api_key)


def _format_history(history: list[dict]) -> list[dict]:
    """Convert stored history turns into OpenAI message format."""
    msgs = []
    for turn in history:
        msgs.append({"role": turn["role"], "content": turn["content"]})
    return msgs


def plan_steps(
    schema: list[dict],
    snapshot: list[dict],
    history: list[dict],
    prompt: str,
    base_steps: list[dict],
    error: str | None = None,
) -> tuple[list[dict], str]:
    """Call LLM to produce a revised steps[] plan.

    Returns (steps, explanation). Falls back to base_steps if LLM is off.
    On retry, error is appended to the user message so the LLM can fix it.
    """
    if not SETTINGS.llm_enabled:
        return base_steps, "LLM not configured — using base mapping steps"

    system = (
        _SYSTEM
        + f"\nCanonical schema: {json.dumps(schema, ensure_ascii=False)}"
        + f"\nCurrent data sample: {json.dumps(snapshot[:10], ensure_ascii=False)}"
        + f"\nBase map_column steps (always preserve these): "
        + json.dumps([s for s in base_steps if s['op'] == 'map_column'],
                     ensure_ascii=False)
    )

    messages: list[dict] = [{"role": "system", "content": system}]
    messages.extend(_format_history(history))

    user_content = prompt
    if error:
        user_content += (
            f"\n\n[AUTO-RETRY: previous plan failed — {error}. "
            "Fix the problematic step(s) and output the corrected full steps[].]"
        )
    messages.append({"role": "user", "content": user_content})

    try:
        client = _client()
        resp = client.chat.completions.create(
            model=SETTINGS.openai_model,
            max_tokens=1500,
            messages=messages,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        steps: list[dict] = data.get("steps", [])
        explanation: str = data.get("explanation", "")
        # Strip None/null values from each step so compile_steps doesn't
        # choke on {"op":"dedupe","from":null,"column":null,...}
        cleaned = [{k: v for k, v in s.items() if v is not None} for s in steps]
        # Validate op names; unknown ops will be caught later by validate_steps
        # but a quick check here avoids a confusing error message.
        for s in cleaned:
            if s.get("op") not in ALLOWED_OPS:
                raise ValueError(f"LLM emitted unknown op {s.get('op')!r}")
        return cleaned, explanation
    except Exception as exc:
        return base_steps, f"planner error ({exc}) — falling back to base steps"
