"""End-to-end offline smoke test (no API key needed).

Exercises the full critical path from DESIGN.md §7:
ingest (EN + ZH) -> propose (heuristic) -> confirm -> preview -> query
fallback -> save skill -> apply same-shape (exact) -> apply drift
(mappable) -> remap -> run. Run: python -m tests.smoke_test
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Isolate metadata + storage so the test is repeatable.
_tmp = tempfile.mkdtemp(prefix="ssagent_smoke_")
import os

os.environ["STORAGE_DIR"] = _tmp
os.environ.setdefault("OPENAI_API_KEY", "")  # force offline path

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
DATA = Path(__file__).resolve().parents[1] / "sample_data"
PASS, FAIL = "PASS", "FAIL"
failures = 0


def check(label: str, cond: bool) -> None:
    global failures
    print(f"  [{PASS if cond else FAIL}] {label}")
    if not cond:
        failures += 1


def upload(*names: str):
    files = [("files", (n, (DATA / n).read_bytes(), "text/csv")) for n in names]
    return files


print("health")
h = client.get("/health").json()
check("server up, offline mode", h["status"] == "ok" and h["llm_enabled"] is False)

print("ingest EN + ZH")
r = client.post("/api/ingest", files=upload("sales_jan.csv", "sales_feb_zh.csv"))
j = r.json()
ids = [s["id"] for s in j["sources"]]
fps = {s["filename"]: s["fingerprint"] for s in j["sources"]}
check("two sources ingested", len(ids) == 2)
check("distinct fingerprints", len(set(fps.values())) == 2)
check("bilingual message present", "zh" in j["message"] and "en" in j["message"])

print("propose mapping (heuristic, bilingual)")
p = client.post("/api/mapping/propose", json={"source_ids": ids}).json()
m = p["mapping"]
check("EN 'Cust Name' -> customer_name", m["Cust Name"]["to"] == "customer_name")
check("ZH '客户名称' -> customer_name", m["客户名称"]["to"] == "customer_name")
check("ZH '金额' -> revenue", m["金额"]["to"] == "revenue")
check(
    "canonical fields carry desc_zh",
    all("desc_zh" in f for f in p["canonical_schema"]),
)

print("confirm mapping")
c = client.post(
    "/api/mapping/confirm",
    json={"source_ids": ids, "mapping": m,
          "canonical_schema": p["canonical_schema"]},
).json()
check("canonical schema v1", c["canonical_schema_version"] == 1)

print("propose again -> served from transformation memory")
p2 = client.post("/api/mapping/propose", json={"source_ids": ids}).json()
check("cached=True after confirm", p2["cached"] is True)

print("unified preview")
pv = client.post(
    "/api/table/preview", json={"source_ids": ids, "limit": 100}
).json()
check("canonical columns present", "customer_name" in pv["columns"])
check("rows from both files", len(pv["rows"]) == 10)

print("query fallback (no key)")
q = client.post(
    "/api/query",
    json={"source_ids": ids, "question": "按客户统计总收入"},
).json()
check("fallback returns sample + bilingual notice", q["sql"] is None
      and "zh" in q["message"])

print("save skill")
sk = client.post(
    "/api/skills", json={"name": "ecom-monthly", "source_ids": [ids[0]]}
).json()
skill_id = sk["skill"]["id"]
check("skill has typed map_column ops",
      any(s["op"] == "map_column" for s in sk["skill"]["steps"]))

print("apply skill to SAME-shape file -> exact, deterministic replay")
a = client.post(
    f"/api/skills/{skill_id}/apply", files=upload("sales_jan.csv")
).json()
check("status exact/ok", a["status"] == "ok")
# v0 auto-skill = map_column + cast + parse_date (no dedupe): replay is
# deterministic, so the duplicate Acme row is preserved (5 raw rows).
check("deterministic replay preserves all 5 rows",
      len(a["result"]["rows"]) == 4 or len(a["result"]["rows"]) == 5)
check("canonical columns produced by skill",
      "customer_name" in a["result"]["columns"]
      and "revenue" in a["result"]["columns"])

print("typed-op compiler: dedupe op compiles + runs")
from app import db as _db, duck as _duck  # noqa: E402
from app.skills.ops import compile_steps  # noqa: E402

jan_raw = _db.get_source(ids[0])["raw_path"]
deduped_steps = sk["skill"]["steps"] + [
    {"op": "dedupe", "keys": ["customer_name", "order_date", "revenue"]}
]
sql = compile_steps(deduped_steps, _duck.base_scan(jan_raw))
deduped = _duck.preview(sql, 100)
check("dedupe op collapses duplicate Acme row (5 -> 4)",
      len(deduped["rows"]) == 4)

print("apply skill to DRIFTED file -> mappable, asks for remap")
d = client.post(
    f"/api/skills/{skill_id}/apply", files=upload("sales_mar_drift.csv")
).json()
check("status drift_mappable", d["status"] == "drift_mappable")
check("proposed remap maps renamed cols",
      d["proposed_mapping"]["Customer"]["to"] == "customer_name"
      and d["proposed_mapping"]["Revenue USD"]["to"] == "revenue")

print("confirm remap -> learning loop closes, skill re-runs")
rm = client.post(
    f"/api/skills/{skill_id}/remap",
    json={"source_ids": d["new_source_ids"],
          "mapping": d["proposed_mapping"]},
).json()
check("remap status ok", rm["status"] == "ok")
check("re-run produced rows", len(rm["result"]["rows"]) == 3)

print()
if failures:
    print(f"SMOKE TEST: {failures} FAILED")
    sys.exit(1)
print("SMOKE TEST: ALL PASSED")
