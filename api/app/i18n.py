"""Bilingual (zh / en) message catalog for API-facing strings.

The first client is China-based, so every user-visible string the API
returns carries both languages; the frontend picks one. Drift diffs and
status messages flow through here.
"""
from __future__ import annotations

Lang = str  # "en" | "zh"

MESSAGES: dict[str, dict[Lang, str]] = {
    "ingest_ok": {"en": "Files ingested.", "zh": "文件已导入。"},
    "mapping_cached": {
        "en": "Reused confirmed mapping for this schema (0 LLM calls).",
        "zh": "已复用该结构的确认映射（0 次大模型调用）。",
    },
    "mapping_proposed": {
        "en": "Mapping proposed. Confirm low-confidence rows.",
        "zh": "已生成映射建议，请确认低置信度项。",
    },
    "mapping_confirmed": {"en": "Mapping confirmed.", "zh": "映射已确认。"},
    "skill_saved": {"en": "Skill saved.", "zh": "技能已保存。"},
    "drift_none": {
        "en": "No drift — skill ran silently.",
        "zh": "无结构漂移，技能已直接执行。",
    },
    "drift_mappable": {
        "en": "Schema drifted but is remappable. Confirm the proposed remap.",
        "zh": "结构发生漂移但可重新映射，请确认建议的重映射。",
    },
    "drift_unmappable": {
        "en": "Schema drift is unmappable: required fields are missing.",
        "zh": "结构漂移无法映射：缺少必需字段。",
    },
    "query_need_key": {
        "en": "Free-form natural-language query needs OPENAI_API_KEY. "
        "Showing a sample of the table instead.",
        "zh": "自由文本自然语言查询需要 OPENAI_API_KEY，"
        "现仅展示数据表样例。",
    },
    "sql_rejected": {
        "en": "Generated SQL was rejected (must be a single read-only SELECT).",
        "zh": "生成的 SQL 被拒绝（必须是单条只读 SELECT）。",
    },
}


def msg(key: str) -> dict[str, str]:
    """Return the {en, zh} pair so the frontend can render either."""
    return MESSAGES.get(key, {"en": key, "zh": key})
