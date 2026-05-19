"""OpenAI integration with a bilingual offline fallback.

Two LLM jobs only (DESIGN.md §1): (1) propose column→canonical mapping with
confidence, (2) NL→SQL. Both are wrapped by the §5 cache at the router
layer. Without OPENAI_API_KEY the app still works via a deterministic
bilingual heuristic — the China-first client's Chinese headers map offline.

OpenAI calls use strict structured outputs (response_format=json_schema,
strict=True) for guaranteed schema compliance. OpenAI auto-caches static
prompt prefixes ≥1024 tokens server-side, so the long system prompt with
CANON_META is amortized across calls — no explicit cache flag needed.

Strict-mode quirk: dict-of-unknown-keys (`additionalProperties: <subschema>`)
is not allowed. The mapping is therefore emitted as an array of records
and converted back to the dict shape the routers expect.
"""
from __future__ import annotations

import json

from .config import SETTINGS
from .fingerprint import normalize_name

# --- bilingual canonical synonym dictionary (offline brain) ----------------
# normalized source name (en + zh) -> canonical field. Drives the heuristic
# fallback and also seeds the prompt so the LLM stays consistent with it.
SYNONYMS: dict[str, str] = {
    # customer_name
    "customer": "customer_name", "customer_name": "customer_name",
    "cust": "customer_name", "cust_name": "customer_name",
    "client": "customer_name", "buyer": "customer_name",
    "客户": "customer_name", "客户名称": "customer_name",
    "客户姓名": "customer_name", "顾客": "customer_name", "买家": "customer_name",
    # order_date
    "order_date": "order_date", "date": "order_date",
    "purchased": "order_date", "date_purchased": "order_date",
    "purchase_date": "order_date", "order_time": "order_date",
    "日期": "order_date", "订单日期": "order_date", "下单日期": "order_date",
    "购买日期": "order_date", "成交日期": "order_date",
    # revenue
    "revenue": "revenue", "amount": "revenue", "amount_usd": "revenue",
    "revenue_usd": "revenue", "rev": "revenue", "sales": "revenue",
    "total": "revenue",
    "金额": "revenue", "收入": "revenue", "营收": "revenue",
    "销售额": "revenue", "成交金额": "revenue", "订单金额": "revenue",
    # email
    "email": "email", "e_mail": "email", "mail": "email",
    "邮箱": "email", "电子邮箱": "email", "电子邮件": "email",
    # phone
    "phone": "phone", "phone_number": "phone", "tel": "phone",
    "mobile": "phone", "电话": "phone", "手机": "phone",
    "手机号": "phone", "联系电话": "phone",
    # country
    "country": "country", "nation": "country", "region": "country",
    "国家": "country", "地区": "country", "国家地区": "country",
}

# canonical field -> bilingual description + type
CANON_META: dict[str, dict] = {
    "customer_name": {"type": "VARCHAR", "desc_en": "Customer / buyer name",
                       "desc_zh": "客户/买家名称"},
    "order_date": {"type": "DATE", "desc_en": "Date the order was placed",
                   "desc_zh": "下单日期"},
    "revenue": {"type": "DECIMAL", "desc_en": "Order revenue amount",
                "desc_zh": "订单收入金额"},
    "email": {"type": "VARCHAR", "desc_en": "Customer email",
              "desc_zh": "客户邮箱"},
    "phone": {"type": "VARCHAR", "desc_en": "Customer phone",
              "desc_zh": "客户电话"},
    "country": {"type": "VARCHAR", "desc_en": "Country / region",
                "desc_zh": "国家/地区"},
}


def _client():
    # Deferred import: matches the storage.py pattern, keeps module import
    # cheap even if the dep is missing in some downstream environment.
    from openai import OpenAI

    return OpenAI(api_key=SETTINGS.openai_api_key)


# --- mapping ---------------------------------------------------------------

def _heuristic_mapping(files: list[dict]) -> dict:
    mapping: dict[str, dict] = {}
    used_canon: set[str] = set()
    for f in files:
        for col in f["columns"]:
            norm = normalize_name(col["name"])
            canon = SYNONYMS.get(norm)
            if canon:
                mapping[col["name"]] = {
                    "to": canon,
                    "confidence": 0.95,
                    "rationale": "dictionary match (zh/en synonym)",
                }
                used_canon.add(canon)
            else:
                mapping[col["name"]] = {
                    "to": None,
                    "confidence": 0.0,
                    "rationale": "no synonym match — needs human confirm",
                }
    fields = [
        {"name": c, **CANON_META[c]} for c in sorted(used_canon)
    ]
    return {"canonical_schema": fields, "mapping": mapping}


# Strict JSON schema: every object lists every property in `required` and
# sets additionalProperties=false (OpenAI strict mode requirement).
_MAPPING_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["canonical_schema", "mappings"],
    "properties": {
        "canonical_schema": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "type", "desc_en", "desc_zh"],
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "desc_en": {"type": "string"},
                    "desc_zh": {"type": "string"},
                },
            },
        },
        # Array-of-records: strict mode forbids open-ended object keys, so
        # we ship `source` as a field and rebuild the dict in Python.
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "to", "confidence", "rationale"],
                "properties": {
                    "source": {"type": "string"},
                    "to": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}


_MAPPING_SYS = (
    "You normalize messy spreadsheet headers (English OR Chinese) into a "
    "canonical schema for a data-cleaning tool. Prefer reusing these "
    "canonical fields when applicable: "
    + json.dumps(CANON_META, ensure_ascii=False)
    + ". Every canonical field MUST have both desc_en and desc_zh. For each "
    "source column emit one record in `mappings` with the exact source name "
    "and either a canonical `to` or null when unclear; give a 0..1 "
    "confidence. Be conservative: low confidence when ambiguous so a human "
    "confirms."
)


def propose_mapping(files: list[dict]) -> dict:
    """files: [{filename, columns:[{name,type,samples}]}]."""
    if not SETTINGS.llm_enabled:
        return _heuristic_mapping(files)
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=SETTINGS.openai_model,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": _MAPPING_SYS},
                {
                    "role": "user",
                    "content": "Files:\n"
                    + json.dumps(files, ensure_ascii=False),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "Mapping",
                    "strict": True,
                    "schema": _MAPPING_SCHEMA,
                },
            },
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        # Rebuild the {source_col: {to, confidence, rationale}} dict the
        # routers expect from the strict-mode array form.
        mapping: dict[str, dict] = {
            rec["source"]: {
                "to": rec["to"],
                "confidence": rec["confidence"],
                "rationale": rec["rationale"],
            }
            for rec in data.get("mappings", [])
        }
        return {
            "canonical_schema": data.get("canonical_schema", []),
            "mapping": mapping,
        }
    except Exception:
        pass
    return _heuristic_mapping(files)  # never hard-fail the pipeline


# --- NL -> SQL -------------------------------------------------------------

_SQL_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sql"],
    "properties": {"sql": {"type": "string"}},
}


def nl_to_sql(question: str, canonical_fields: list[dict]) -> str | None:
    """Return SQL over the unified table `t`, or None if no key (caller
    falls back to a sample). Accepts Chinese or English questions."""
    if not SETTINGS.llm_enabled:
        return None
    schema_desc = json.dumps(canonical_fields, ensure_ascii=False)
    sys = (
        "Translate the user's question (Chinese or English) into ONE "
        "read-only DuckDB SELECT over a table named t with columns: "
        + schema_desc
        + ". No DDL/DML, no semicolons, no comments. Return only the SQL "
        "string in the `sql` field."
    )
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=SETTINGS.openai_model,
            max_tokens=600,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": question},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "Sql",
                    "strict": True,
                    "schema": _SQL_SCHEMA,
                },
            },
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        sql = data.get("sql")
        return sql if isinstance(sql, str) and sql.strip() else None
    except Exception:
        return None
