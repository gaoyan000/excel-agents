"use client";

import { useState } from "react";
import * as api from "@/lib/api";
import { Lang, t } from "@/lib/i18n";

type Step = api.Step;

type HistoryItem = {
  role: "user" | "assistant";
  content: string;
  ops?: string[];
  attempts?: number;
  isError?: boolean;
};

function snapshotToTable(snapshot: Record<string, string | null>[]) {
  if (!snapshot.length) return { columns: [] as string[], rows: [] as unknown[][] };
  const columns = Object.keys(snapshot[0]);
  const rows = snapshot.map((row) => columns.map((c) => row[c] ?? null));
  return { columns, rows };
}

const OP_COLOR: Record<string, string> = {
  map_column: "bg-blue-100 text-blue-700",
  cast: "bg-purple-100 text-purple-700",
  parse_date: "bg-orange-100 text-orange-700",
  normalize_phone: "bg-teal-100 text-teal-700",
  dedupe: "bg-amber-100 text-amber-700",
  filter: "bg-red-100 text-red-700",
  derive: "bg-emerald-100 text-emerald-700",
};

function stepDesc(s: Step): string {
  switch (s.op) {
    case "map_column":     return `"${s.from}" → ${s.to}`;
    case "cast":           return `${s.column} as ${s.type}`;
    case "parse_date":     return `${s.column} (${s.format || "auto"})`;
    case "normalize_phone":return s.column ?? "";
    case "dedupe":         return `[${s.keys?.join(", ")}]`;
    case "filter":         return s.predicate ?? "";
    case "derive":         return `${s.to} = ${s.expr}`;
    default:               return s.op;
  }
}

function StepsPanel({ steps, label }: { steps: Step[]; label: string }) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label} ({steps.length})
      </div>
      <div className="min-h-20 space-y-1 rounded border bg-slate-50 p-2">
        {steps.length === 0 ? (
          <span className="text-xs text-slate-400">—</span>
        ) : (
          steps.map((s, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs">
              <span
                className={`shrink-0 rounded px-1.5 py-0.5 font-mono ${
                  OP_COLOR[s.op] ?? "bg-slate-100 text-slate-600"
                }`}
              >
                {s.op}
              </span>
              <span className="break-all text-slate-600">{stepDesc(s)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function SnapshotPanel({
  tbl,
  label,
}: {
  tbl: ReturnType<typeof snapshotToTable>;
  label: string;
}) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      {tbl.columns.length === 0 ? (
        <div className="flex min-h-20 items-center justify-center rounded border bg-slate-50 text-xs text-slate-400">
          —
        </div>
      ) : (
        <div className="max-h-52 overflow-x-auto overflow-y-auto rounded border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-100 text-left">
              <tr>
                {tbl.columns.map((c) => (
                  <th key={c} className="whitespace-nowrap px-2 py-1">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tbl.rows.map((row, i) => (
                <tr key={i} className="border-t">
                  {row.map((cell, j) => (
                    <td key={j} className="max-w-[140px] truncate px-2 py-1">
                      {cell === null || cell === undefined ? "" : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function HistoryFeed({
  history,
  label,
  lang,
}: {
  history: HistoryItem[];
  label: string;
  lang: Lang;
}) {
  if (!history.length) return null;
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="max-h-44 space-y-2 overflow-y-auto rounded border bg-slate-50 p-2">
        {history.map((item, i) => (
          <div
            key={i}
            className={`flex gap-2 ${item.role === "user" ? "justify-end" : ""}`}
          >
            {item.role === "assistant" && (
              <span className="mt-0.5 shrink-0 text-xs text-slate-400">AI</span>
            )}
            <div
              className={`max-w-xs rounded px-2 py-1 text-xs ${
                item.role === "user"
                  ? "bg-slate-800 text-white"
                  : item.isError
                  ? "bg-red-50 text-red-700"
                  : "bg-blue-50 text-blue-800"
              }`}
            >
              {item.content}
              {item.attempts !== undefined && item.attempts > 1 && (
                <span className="ml-1 opacity-60 text-[10px]">
                  ({item.attempts}{" "}
                  {lang === "zh" ? "次尝试" : "attempts"})
                </span>
              )}
            </div>
            {item.role === "user" && (
              <span className="mt-0.5 shrink-0 text-xs text-slate-400">
                {lang === "zh" ? "你" : "You"}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function AgentSection({
  sourceIds,
  lang,
}: {
  sourceIds: number[];
  lang: Lang;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [schema, setSchema] = useState<api.CanonField[]>([]);
  const [steps, setSteps] = useState<Step[]>([]);
  const [snapshot, setSnapshot] = useState<Record<string, string | null>[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [planErr, setPlanErr] = useState("");
  const [skillName, setSkillName] = useState("");
  const [savedSkill, setSavedSkill] = useState("");

  async function startSession() {
    setBusy(true);
    setPlanErr("");
    try {
      const r = await api.agentStart(sourceIds);
      setSessionId(r.session_id);
      setSchema(r.schema);
      setSteps(r.current_steps);
      setSnapshot(r.snapshot);
    } catch (e) {
      setPlanErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runPlan() {
    if (!sessionId || !prompt.trim() || busy) return;
    setBusy(true);
    setPlanErr("");
    const submitted = prompt.trim();
    setPrompt("");
    try {
      const result = await api.agentPlan(sessionId, submitted);
      if (!result.ok) {
        setPlanErr(result.error);
        setHistory((h) => [
          ...h,
          { role: "user", content: submitted },
          { role: "assistant", content: result.error, isError: true, attempts: result.attempts },
        ]);
        return;
      }
      const { data } = result;
      setSteps(data.steps);
      setSnapshot(data.snapshot);
      setHistory((h) => [
        ...h,
        { role: "user", content: submitted },
        {
          role: "assistant",
          content: data.explanation || `${data.steps.length} steps`,
          ops: data.steps.map((s) => s.op),
          attempts: data.attempts,
        },
      ]);
    } catch (e) {
      setPlanErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmSkill() {
    if (!sessionId || !skillName.trim() || busy) return;
    setBusy(true);
    setPlanErr("");
    try {
      const r = await api.agentConfirm(sessionId, skillName, steps);
      setSavedSkill(`${r.skill.name} · #${r.skill.id} · ${r.skill.steps.length} ops`);
    } catch (e) {
      setPlanErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const tbl = snapshotToTable(snapshot);

  return (
    <section className="rounded-lg border bg-white p-4 space-y-4">
      <h2 className="font-semibold">{t(lang, "agentSection")}</h2>

      {/* ── Before session starts ── */}
      {!sessionId && (
        <div className="space-y-2">
          <p className="text-sm text-slate-500">{t(lang, "agentStartHint")}</p>
          <button
            onClick={startSession}
            disabled={busy}
            className="rounded bg-slate-800 px-3 py-1 text-sm text-white disabled:opacity-50"
          >
            {busy ? t(lang, "busy") : t(lang, "agentStart")}
          </button>
          {planErr && <p className="text-sm text-red-600">{planErr}</p>}
        </div>
      )}

      {/* ── Active session ── */}
      {sessionId && (
        <>
          {/* Schema chips */}
          <div className="flex flex-wrap gap-1.5">
            {schema.map((f) => (
              <span
                key={f.name}
                className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-xs"
              >
                {f.name}
                <span className="ml-1 text-slate-400">{f.type}</span>
              </span>
            ))}
          </div>

          {/* Steps (left 2/5) + Snapshot (right 3/5) */}
          <div className="grid grid-cols-5 gap-4">
            <div className="col-span-2">
              <StepsPanel steps={steps} label={t(lang, "agentStepsTitle")} />
            </div>
            <div className="col-span-3">
              <SnapshotPanel tbl={tbl} label={t(lang, "agentSnapshotTitle")} />
            </div>
          </div>

          {/* Conversation history */}
          <HistoryFeed
            history={history}
            label={t(lang, "agentHistory")}
            lang={lang}
          />

          {/* Prompt row */}
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !busy) runPlan();
                }}
                placeholder={t(lang, "agentPromptPlaceholder")}
                disabled={busy}
                className="flex-1 rounded border px-2 py-1 text-sm disabled:opacity-50"
              />
              <button
                onClick={runPlan}
                disabled={busy || !prompt.trim()}
                className="rounded bg-slate-800 px-3 py-1 text-sm text-white disabled:opacity-50"
              >
                {busy ? t(lang, "agentPlanning") : t(lang, "agentPlan")}
              </button>
            </div>
            {planErr && (
              <div className="rounded bg-red-50 p-2 text-xs text-red-700">
                {planErr}
              </div>
            )}
          </div>

          {/* Confirm row */}
          <div className="flex items-center gap-2 border-t pt-3">
            <input
              value={skillName}
              onChange={(e) => setSkillName(e.target.value)}
              placeholder={t(lang, "skillName")}
              disabled={busy}
              className="flex-1 rounded border px-2 py-1 text-sm disabled:opacity-50"
            />
            <button
              onClick={confirmSkill}
              disabled={busy || !skillName.trim() || steps.length === 0}
              className="rounded bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-50"
            >
              {t(lang, "agentConfirm")}
            </button>
          </div>

          {savedSkill && (
            <p className="text-sm text-emerald-700">✓ {savedSkill}</p>
          )}
        </>
      )}
    </section>
  );
}
