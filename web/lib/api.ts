const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json() as Promise<T>;
}

export type Bi = { zh: string; en: string };
export type Col = { name: string; type: string; samples: string[] };
export type Source = { id: number; filename: string; fingerprint: string; columns: Col[] };
export type MapEntry = { to: string | null; confidence: number; rationale: string };
export type Mapping = Record<string, MapEntry>;
export type CanonField = { name: string; type: string; desc_en: string; desc_zh: string };
export type Table = { columns: string[]; rows: unknown[][] };

export async function health(): Promise<{
  status: string; llm_enabled: boolean; model: string | null;
}> {
  return j(await fetch(`${BASE}/health`));
}

export async function ingest(files: FileList): Promise<{
  sources: Source[]; known_fingerprint: boolean; message: Bi;
}> {
  const fd = new FormData();
  Array.from(files).forEach((f) => fd.append("files", f));
  return j(await fetch(`${BASE}/api/ingest`, { method: "POST", body: fd }));
}

export type MapMode = "smart" | "raw";

export async function propose(
  sourceIds: number[], mode: MapMode = "smart"
): Promise<{
  mapping: Mapping; canonical_schema: CanonField[]; cached: boolean; message: Bi;
}> {
  return j(
    await fetch(`${BASE}/api/mapping/propose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_ids: sourceIds, mode }),
    })
  );
}

export async function confirm(
  sourceIds: number[], mapping: Mapping, canonical: CanonField[]
): Promise<{ canonical_schema_version: number; message: Bi }> {
  return j(
    await fetch(`${BASE}/api/mapping/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_ids: sourceIds, mapping, canonical_schema: canonical,
      }),
    })
  );
}

export async function preview(sourceIds: number[]): Promise<Table> {
  return j(
    await fetch(`${BASE}/api/table/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_ids: sourceIds, limit: 50 }),
    })
  );
}

export async function query(
  sourceIds: number[], question: string
): Promise<Table & { sql: string | null; message?: Bi }> {
  return j(
    await fetch(`${BASE}/api/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_ids: sourceIds, question }),
    })
  );
}

export async function exportXlsx(
  columns: string[], rows: unknown[][], filename = "查询结果.xlsx"
): Promise<void> {
  const r = await fetch(`${BASE}/api/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ columns, rows, filename }),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function saveSkill(
  name: string, sourceIds: number[]
): Promise<{ skill: { id: number; name: string; steps: unknown[] }; message: Bi }> {
  return j(
    await fetch(`${BASE}/api/skills`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, source_ids: sourceIds }),
    })
  );
}

// --- agent skill planner ---------------------------------------------------

export type Step = {
  op: string;
  from?: string;
  to?: string;
  column?: string;
  type?: string;
  format?: string;
  region?: string;
  keys?: string[];
  predicate?: string;
  expr?: string;
};

export type AgentSession = {
  session_id: string;
  schema: CanonField[];
  snapshot: Record<string, string | null>[];
  current_steps: Step[];
};

export type PlanOk = {
  status: "ok";
  steps: Step[];
  explanation: string;
  snapshot: Record<string, string | null>[];
  attempts: number;
};

export type PlanResult =
  | { ok: true; data: PlanOk }
  | { ok: false; error: string; partialSteps: Step[]; attempts: number };

export async function agentStart(sourceIds: number[]): Promise<AgentSession> {
  return j(
    await fetch(`${BASE}/api/agent/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_ids: sourceIds }),
    })
  );
}

export async function agentPlan(
  sessionId: string,
  prompt: string,
): Promise<PlanResult> {
  const r = await fetch(`${BASE}/api/agent/session/${sessionId}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  if (r.status === 422) {
    const body = await r.json();
    const detail = body.detail ?? {};
    return {
      ok: false,
      error: detail.error ?? "planning failed",
      partialSteps: detail.partial_steps ?? [],
      attempts: detail.attempts ?? 0,
    };
  }
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return { ok: true, data: await r.json() };
}

export async function agentConfirm(
  sessionId: string,
  name: string,
  steps?: Step[],
): Promise<{ skill: { id: number; name: string; steps: Step[] } }> {
  return j(
    await fetch(`${BASE}/api/agent/session/${sessionId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, steps }),
    })
  );
}
