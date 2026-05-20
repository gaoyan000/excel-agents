"""Opt-in production smoke test — exercises a DEPLOYED backend over HTTP.

Unlike tests/smoke_test.py (in-process TestClient), this hits a real base
URL with httpx, so it proves the live Fly deployment + durable volume work.

Critical path (DESIGN.md §7), same shape as the offline smoke test:
  ingest (EN + ZH) -> propose -> confirm -> preview -> query fallback
  -> save skill -> apply same-shape -> apply drift -> remap.

The saved skill name is timestamped and printed. After the run, restart
the app and re-check that the skill survived (DEPLOY.md §4 durability):

  python -m tests.prod_smoke https://api.yourdomain.com
  fly apps restart spreadsheet-agent-api          # in api/
  python -m tests.prod_smoke https://api.yourdomain.com --check-skill prod-smoke-1747...

Sample data is read from the LOCAL repo and uploaded over the wire (the
Docker image excludes sample_data/), so run this from the `api/` dir.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parents[1] / "sample_data"
PASS, FAIL = "PASS", "FAIL"
_failures = 0


def check(label: str, cond: bool) -> None:
    global _failures
    print(f"  [{PASS if cond else FAIL}] {label}")
    if not cond:
        _failures += 1


def _files(*names: str):
    return [("files", (n, (DATA / n).read_bytes(), "text/csv")) for n in names]


def _check_skill_only(client: httpx.Client, name: str) -> int:
    """Durability probe: assert a previously-saved skill still exists."""
    print(f"durability check: skill '{name}' survived restart?")
    skills = client.get("/api/skills").json()["skills"]
    found = any(s["name"] == name for s in skills)
    check(f"skill '{name}' present on live backend", found)
    return 1 if not found else 0


def run(base_url: str, check_skill: str | None) -> int:
    base_url = base_url.rstrip("/")
    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        if check_skill:
            return _check_skill_only(client, check_skill)

        print(f"target: {base_url}")
        print("health")
        h = client.get("/health").json()
        check("server up", h.get("status") == "ok")

        print("ingest EN + ZH")
        j = client.post(
            "/api/ingest", files=_files("sales_jan.csv", "sales_feb_zh.csv")
        ).json()
        ids = [s["id"] for s in j["sources"]]
        fps = {s["fingerprint"] for s in j["sources"]}
        check("two sources ingested", len(ids) == 2)
        check("distinct fingerprints", len(fps) == 2)

        print("propose mapping (heuristic, bilingual)")
        p = client.post(
            "/api/mapping/propose", json={"source_ids": ids}
        ).json()
        m = p["mapping"]
        check("EN 'Cust Name' -> customer_name",
              m["Cust Name"]["to"] == "customer_name")
        check("ZH '客户名称' -> customer_name",
              m["客户名称"]["to"] == "customer_name")

        print("confirm mapping")
        c = client.post(
            "/api/mapping/confirm",
            json={"source_ids": ids, "mapping": m,
                  "canonical_schema": p["canonical_schema"]},
        ).json()
        check("canonical schema v1", c["canonical_schema_version"] == 1)

        print("unified preview")
        pv = client.post(
            "/api/table/preview", json={"source_ids": ids, "limit": 100}
        ).json()
        check("canonical columns present", "customer_name" in pv["columns"])
        check("rows from both files", len(pv["rows"]) == 10)

        print("query (LLM if OPENAI_API_KEY set, else heuristic fallback)")
        q = client.post(
            "/api/query",
            json={"source_ids": ids, "question": "按客户统计总收入"},
        ).json()
        # Two healthy shapes: LLM success returns `sql` + rows; offline
        # fallback returns `sql=None` + a bilingual `message`.
        if q.get("sql"):
            check("LLM path: produced SQL + rows",
                  isinstance(q["sql"], str) and len(q.get("rows", [])) > 0)
        else:
            check("fallback path: bilingual message present",
                  bool(q.get("message")))

        skill_name = check_skill or f"prod-smoke-{int(time.time())}"
        print(f"save skill '{skill_name}'")
        sk = client.post(
            "/api/skills", json={"name": skill_name, "source_ids": [ids[0]]}
        ).json()
        skill_id = sk["skill"]["id"]
        check("skill has typed map_column ops",
              any(s["op"] == "map_column" for s in sk["skill"]["steps"]))

        print("apply skill to SAME-shape file -> exact replay")
        a = client.post(
            f"/api/skills/{skill_id}/apply", files=_files("sales_jan.csv")
        ).json()
        check("status ok", a["status"] == "ok")
        check("canonical columns produced",
              "customer_name" in a["result"]["columns"]
              and "revenue" in a["result"]["columns"])

        print("apply skill to DRIFTED file -> asks for remap")
        d = client.post(
            f"/api/skills/{skill_id}/apply",
            files=_files("sales_mar_drift.csv"),
        ).json()
        check("status drift_mappable", d["status"] == "drift_mappable")
        check("proposed remap maps renamed cols",
              d["proposed_mapping"]["Customer"]["to"] == "customer_name")

        print("confirm remap -> learning loop closes, skill re-runs")
        rm = client.post(
            f"/api/skills/{skill_id}/remap",
            json={"source_ids": d["new_source_ids"],
                  "mapping": d["proposed_mapping"]},
        ).json()
        check("remap status ok", rm["status"] == "ok")
        check("re-run produced rows", len(rm["result"]["rows"]) > 0)

    print()
    if _failures:
        print(f"PROD SMOKE: {_failures} FAILED")
        return 1
    print("PROD SMOKE: ALL PASSED")
    print(
        f"\nDurability: restart the app, then re-run with\n"
        f"  python -m tests.prod_smoke {base_url} --check-skill {skill_name}"
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(
            "usage: python -m tests.prod_smoke <BASE_URL> "
            "[--check-skill <name>]"
        )
        return 2
    base_url = args[0]
    check_skill = None
    if "--check-skill" in args:
        i = args.index("--check-skill")
        if i + 1 >= len(args):
            print("error: --check-skill needs a skill name")
            return 2
        check_skill = args[i + 1]
    return run(base_url, check_skill)


if __name__ == "__main__":
    raise SystemExit(main())
