// Bilingual UI strings. China-first client => zh is the default locale.
export type Lang = "zh" | "en";

export const STRINGS = {
  title: { zh: "表格智能体", en: "Spreadsheet Agent" },
  subtitle: {
    zh: "上传混乱的 Excel/CSV → AI 对齐字段 → 统一表 → 自然语言查询 → 保存技能",
    en: "Messy Excel/CSV → AI column alignment → unified table → NL query → save skill",
  },
  upload: { zh: "1. 上传文件（CSV/XLSX，可多选）", en: "1. Upload files (CSV/XLSX, multiple)" },
  ingest: { zh: "导入并识别结构", en: "Ingest & introspect" },
  propose: { zh: "2. 生成字段映射", en: "2. Propose mapping" },
  mapModeLabel: { zh: "映射方式", en: "Mapping mode" },
  mapModeSmart: { zh: "智能映射", en: "Smart (LLM)" },
  mapModeRaw: { zh: "原始列名", en: "Raw column names" },
  mapModeSmartHint: {
    zh: "用 LLM 跨文件聚合相似列（如 日期 与 下单日期 合并）。",
    en: "LLM clusters similar columns across files (e.g. 日期 + Order Date).",
  },
  mapModeRawHint: {
    zh: "每个源列即标准字段；同名列自动合并，其他保持独立。",
    en: "Each source column is its own canonical; identical names merge.",
  },
  source: { zh: "源列", en: "Source column" },
  canonical: { zh: "标准字段", en: "Canonical field" },
  customField: { zh: "➕ 自定义字段…", en: "➕ Custom field…" },
  customFieldPrompt: {
    zh: "输入标准字段名称（用于该源列）：",
    en: "Enter a canonical field name for this source column:",
  },
  confidence: { zh: "置信度", en: "Confidence" },
  confirm: { zh: "确认映射", en: "Confirm mapping" },
  preview: { zh: "3. 统一表预览", en: "3. Unified table preview" },
  loadPreview: { zh: "加载预览", en: "Load preview" },
  query: { zh: "4. 自然语言查询", en: "4. Natural-language query" },
  exportXlsx: { zh: "导出 Excel", en: "Export to Excel" },
  exporting: { zh: "导出中…", en: "Exporting…" },
  ask: { zh: "查询", en: "Ask" },
  queryPlaceholder: {
    zh: "例如：按客户统计总收入",
    en: "e.g. total revenue by customer",
  },
  saveSkill: { zh: "5. 保存为技能", en: "5. Save as skill" },
  skillName: { zh: "技能名称", en: "Skill name" },
  save: { zh: "保存技能", en: "Save skill" },
  drift: { zh: "结构漂移", en: "Schema drift" },
  busy: { zh: "处理中…", en: "Working…" },
  llmOff: {
    zh: "未配置 OPENAI_API_KEY：映射走双语启发式，查询返回样例。",
    en: "No OPENAI_API_KEY: heuristic mapping; query returns a sample.",
  },
};

export function t(lang: Lang, key: keyof typeof STRINGS): string {
  return STRINGS[key][lang];
}
