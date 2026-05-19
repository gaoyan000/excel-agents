"use client";

import { useState } from "react";
import * as api from "@/lib/api";
import { Lang, t } from "@/lib/i18n";

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
  const [skillName, setSkillName] = useState("");
  const [savedSkill, setSavedSkill] = useState<string>("");

  const ids = sources.map((s) => s.id);
  const fieldNames = canon.map((c) => c.name);

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
          accept=".csv,.tsv,.xlsx"
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
                const r = await run(() => api.propose(ids));
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
                      <td className="py-1 font-mono">{col}</td>
                      <td>
                        <select
                          value={m.to ?? ""}
                          onChange={(e) =>
                            setMapping({
                              ...mapping,
                              [col]: { ...m, to: e.target.value || null },
                            })
                          }
                          className="rounded border px-1 py-0.5"
                        >
                          <option value="">—</option>
                          {fieldNames.map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
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
                  if (r) setNote(r.message[lang]);
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
          {qres && <DataTable tbl={qres} />}
        </section>
      )}

      {/* 5. Save skill */}
      {canon.length > 0 && (
        <section className="rounded-lg border bg-white p-4 space-y-3">
          <h2 className="font-semibold">{t(lang, "saveSkill")}</h2>
          <div className="flex gap-2">
            <input
              value={skillName}
              onChange={(e) => setSkillName(e.target.value)}
              placeholder={t(lang, "skillName")}
              className="flex-1 rounded border px-2 py-1 text-sm"
            />
            <button
              onClick={async () => {
                const r = await run(() =>
                  api.saveSkill(skillName, ids)
                );
                if (r)
                  setSavedSkill(
                    `${r.skill.name} v? · #${r.skill.id} · ${r.skill.steps.length} ops`
                  );
              }}
              className="rounded bg-emerald-600 px-3 py-1 text-sm text-white"
            >
              {t(lang, "save")}
            </button>
          </div>
          {savedSkill && (
            <p className="text-sm text-emerald-700">✓ {savedSkill}</p>
          )}
        </section>
      )}

      <footer className="pt-4 text-xs text-slate-400">
        {t(lang, "llmOff")}
      </footer>
    </main>
  );
}

function DataTable({ tbl }: { tbl: api.Table }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="bg-slate-100 text-left">
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
