"use client";

import { useEffect, useState } from "react";
import * as api from "@/lib/api";
import { Lang, t } from "@/lib/i18n";
import { AgentSection } from "./agent-section";

export default function Page() {
  const [lang, setLang] = useState<Lang>("zh");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>("");
  const [sources, setSources] = useState<api.Source[]>([]);
  const [mapping, setMapping] = useState<api.Mapping>({});
  const [canon, setCanon] = useState<api.CanonField[]>([]);
  const [note, setNote] = useState<string>("");
  const [tbl, setTbl] = useState<api.Table | null>(null);
  const [question, setQuestion] = useState("");
  const [qres, setQres] = useState<(api.Table & { sql: string | null }) | null>(null);
  const [mapMode, setMapMode] = useState<api.MapMode>("smart");
  const [mappingConfirmed, setMappingConfirmed] = useState(false);
  // Tri-state: undefined while /health hasn't returned, then true|false.
  // Starts undefined so we don't flash the "key missing" banner before
  // the check completes.
  const [llmEnabled, setLlmEnabled] = useState<boolean | undefined>(undefined);

  useEffect(() => {
    api.health().then((h) => setLlmEnabled(h.llm_enabled)).catch(() => {
      // Network/CORS failure -> treat as offline so the banner is honest.
      setLlmEnabled(false);
    });
  }, []);

  const ids = sources.map((s) => s.id);
  // Bilingual label for a canonical field — Chinese desc in zh mode,
  // English desc in en mode, falling back to the canonical name.
  const fieldLabel = (c: api.CanonField) =>
    (lang === "zh" ? c.desc_zh : c.desc_en) || c.name;
  // Which uploaded file(s) each source column came from — lets the user
  // trace an auto-named column ("column20") back to its origin file.
  const colSources: Record<string, string[]> = {};
  for (const s of sources) {
    for (const c of s.columns) {
      (colSources[c.name] ||= []).push(s.filename);
    }
  }

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setErr("");
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-5xl p-6 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{t(lang, "title")}</h1>
          <p className="text-sm text-slate-500">{t(lang, "subtitle")}</p>
        </div>
        <button
          onClick={() => setLang(lang === "zh" ? "en" : "zh")}
          className="rounded border px-3 py-1 text-sm hover:bg-slate-100"
        >
          {lang === "zh" ? "EN" : "中文"}
        </button>
      </header>

      {err && (
        <div className="rounded bg-red-50 p-3 text-sm text-red-700">{err}</div>
      )}
      {busy && <div className="text-sm text-slate-500">{t(lang, "busy")}</div>}

      {/* 1. Upload */}
      <section className="rounded-lg border bg-white p-4 space-y-3">
        <h2 className="font-semibold">{t(lang, "upload")}</h2>
        <input
          type="file"
          multiple
          accept=".csv,.tsv,.xlsx,.xls"
          onChange={async (e) => {
            const files = e.target.files;
            if (!files?.length) return;
            const r = await run(() => api.ingest(files));
            if (r) {
              setSources(r.sources);
              setNote(r.message[lang]);
              if (!r.known_fingerprint) setNote(r.message[lang]);
            }
          }}
        />
        {sources.length > 0 && (
          <ul className="text-sm text-slate-600">
            {sources.map((s) => (
              <li key={s.id}>
                {s.filename} — {s.columns.length} cols ·{" "}
                <code>{s.fingerprint}</code>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 2. Mapping */}
      {sources.length > 0 && (
        <section className="rounded-lg border bg-white p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{t(lang, "propose")}</h2>
            <button
              onClick={async () => {
                const r = await run(() => api.propose(ids, mapMode));
                if (r) {
                  setMapping(r.mapping);
                  setCanon(r.canonical_schema);
                  setNote(r.message[lang]);
                }
              }}
              className="rounded bg-slate-800 px-3 py-1 text-sm text-white"
            >
              {t(lang, "propose")}
            </button>
          </div>
          {/* Mode toggle: smart (LLM clusters) vs raw (identity). */}
          <div className="text-sm">
            <div className="font-medium text-slate-600">
              {t(lang, "mapModeLabel")}
            </div>
            <div className="mt-1 inline-flex rounded border bg-slate-50 p-0.5">
              {(["smart", "raw"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMapMode(m)}
                  className={
                    "rounded px-3 py-1 text-xs " +
                    (mapMode === m
                      ? "bg-white shadow-sm text-slate-900"
                      : "text-slate-500 hover:text-slate-700")
                  }
                >
                  {t(lang, m === "smart" ? "mapModeSmart" : "mapModeRaw")}
                </button>
              ))}
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {t(
                lang,
                mapMode === "smart" ? "mapModeSmartHint" : "mapModeRawHint",
              )}
            </p>
          </div>
          {note && <p className="text-xs text-slate-500">{note}</p>}
          {Object.keys(mapping).length > 0 && (
            <>
              <table className="w-full text-sm">
                <thead className="text-left text-slate-500">
                  <tr>
                    <th className="py-1">{t(lang, "source")}</th>
                    <th>{t(lang, "canonical")}</th>
                    <th>{t(lang, "confidence")}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(mapping).map(([col, m]) => (
                    <tr
                      key={col}
                      className={m.confidence < 0.6 ? "bg-amber-50" : ""}
                    >
                      <td className="py-1 font-mono">
                        {col}
                        {colSources[col]?.length > 0 && (
                          <div className="font-sans text-[10px] text-slate-400">
                            {colSources[col].length === 1
                              ? colSources[col][0]
                              : `${colSources[col].length}${
                                  lang === "zh" ? " 个文件" : " files"
                                }`}
                          </div>
                        )}
                      </td>
                      <td>
                        <select
                          value={m.to ?? ""}
                          onChange={(e) => {
                            const v = e.target.value;
                            if (v === "__custom__") {
                              // Source columns with no usable header (e.g.
                              // the auto-named "column20" from a ragged
                              // sheet) often need a name typed by hand.
                              const name = window
                                .prompt(t(lang, "customFieldPrompt"), col)
                                ?.trim();
                              if (!name) return; // cancelled / empty
                              // Register the new canonical field so it shows
                              // in the unified table and every dropdown.
                              if (!canon.some((c) => c.name === name)) {
                                setCanon([
                                  ...canon,
                                  {
                                    name,
                                    type: "VARCHAR",
                                    desc_en: name,
                                    desc_zh: name,
                                  },
                                ]);
                              }
                              setMapping({
                                ...mapping,
                                [col]: { ...m, to: name },
                              });
                              return;
                            }
                            setMapping({
                              ...mapping,
                              [col]: { ...m, to: v || null },
                            });
                          }}
                          className="rounded border px-1 py-0.5"
                        >
                          <option value="">—</option>
                          {canon.map((c) => (
                            <option key={c.name} value={c.name}>
                              {fieldLabel(c)}
                            </option>
                          ))}
                          <option value="__custom__">
                            {t(lang, "customField")}
                          </option>
                        </select>
                      </td>
                      <td>{(m.confidence * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button
                onClick={async () => {
                  const r = await run(() =>
                    api.confirm(ids, mapping, canon)
                  );
                  if (r) {
                    setNote(r.message[lang]);
                    setMappingConfirmed(true);
                  }
                }}
                className="rounded bg-emerald-600 px-3 py-1 text-sm text-white"
              >
                {t(lang, "confirm")}
              </button>
            </>
          )}
        </section>
      )}

      {/* 3. Preview */}
      {canon.length > 0 && (
        <section className="rounded-lg border bg-white p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{t(lang, "preview")}</h2>
            <button
              onClick={async () => {
                const r = await run(() => api.preview(ids));
                if (r) setTbl(r);
              }}
              className="rounded bg-slate-800 px-3 py-1 text-sm text-white"
            >
              {t(lang, "loadPreview")}
            </button>
          </div>
          {tbl && <DataTable tbl={tbl} />}
        </section>
      )}

      {/* 4. Query */}
      {canon.length > 0 && (
        <section className="rounded-lg border bg-white p-4 space-y-3">
          <h2 className="font-semibold">{t(lang, "query")}</h2>
          <div className="flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={t(lang, "queryPlaceholder")}
              className="flex-1 rounded border px-2 py-1 text-sm"
            />
            <button
              onClick={async () => {
                const r = await run(() => api.query(ids, question));
                if (r) {
                  setQres(r);
                  if (r.message) setNote(r.message[lang]);
                }
              }}
              className="rounded bg-slate-800 px-3 py-1 text-sm text-white"
            >
              {t(lang, "ask")}
            </button>
          </div>
          {qres?.sql && (
            <pre className="overflow-x-auto rounded bg-slate-900 p-2 text-xs text-emerald-300">
              {qres.sql}
            </pre>
          )}
          {qres && (
            <>
              <div className="flex justify-end">
                <button
                  type="button"
                  disabled={busy || qres.rows.length === 0}
                  onClick={async () => {
                    if (!qres) return;
                    const ts = new Date()
                      .toISOString()
                      .replace(/[:.]/g, "-")
                      .slice(0, 19);
                    const fname = `${
                      lang === "zh" ? "查询结果" : "query_result"
                    }_${ts}.xlsx`;
                    await run(() =>
                      api.exportXlsx(
                        qres.columns,
                        qres.rows as unknown[][],
                        fname,
                      ),
                    );
                  }}
                  className="rounded bg-emerald-600 px-3 py-1 text-sm text-white disabled:opacity-50"
                >
                  {t(lang, busy ? "exporting" : "exportXlsx")}
                </button>
              </div>
              <DataTable tbl={qres} />
            </>
          )}
        </section>
      )}

      {/* 5. AI Skill Planner */}
      {mappingConfirmed && (
        <AgentSection sourceIds={ids} lang={lang} />
      )}

      {llmEnabled === false && (
        <footer className="pt-4 text-xs text-slate-400">
          {t(lang, "llmOff")}
        </footer>
      )}
    </main>
  );
}

function DataTable({ tbl }: { tbl: api.Table }) {
  // Cap the vertical viewport at ~10 rows so wide tables (§3 preview with
  // 15+ columns) don't push the rest of the page off-screen. The header
  // sticks so it stays visible while the user scrolls within the panel.
  const scrollable = tbl.rows.length > 10;
  return (
    <div
      className={
        "overflow-x-auto " +
        (scrollable ? "max-h-96 overflow-y-auto border rounded" : "")
      }
    >
      <table className="w-full text-xs">
        <thead
          className={
            "bg-slate-100 text-left " +
            (scrollable ? "sticky top-0 z-10" : "")
          }
        >
          <tr>
            {tbl.columns.map((c) => (
              <th key={c} className="px-2 py-1">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tbl.rows.slice(0, 50).map((row, i) => (
            <tr key={i} className="border-t">
              {row.map((cell, j) => (
                <td key={j} className="px-2 py-1">
                  {cell === null ? "" : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
