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

export async function ingest(files: FileList): Promise<{
  sources: Source[]; known_fingerprint: boolean; message: Bi;
}> {
  const fd = new FormData();
  Array.from(files).forEach((f) => fd.append("files", f));
  return j(await fetch(`${BASE}/api/ingest`, { method: "POST", body: fd }));
}

export async function propose(sourceIds: number[]): Promise<{
  mapping: Mapping; canonical_schema: CanonField[]; cached: boolean; message: Bi;
}> {
  return j(
    await fetch(`${BASE}/api/mapping/propose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_ids: sourceIds }),
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
