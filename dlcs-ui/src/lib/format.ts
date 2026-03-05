export function fmtNumber(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

export function fmtPct(value?: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-Hant-HK", { hour12: false });
}

export function firstText(...values: Array<unknown>): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function extractExecutiveSummary(obj: Record<string, unknown>): string {
  const a = obj as Record<string, unknown>;
  const sec1 = (a.section_one || a.section_1 || a.summary || {}) as Record<string, unknown>;
  const meta = (a.meta || {}) as Record<string, unknown>;
  const candidates = [
    sec1.executive_summary,
    sec1.executiveSummary,
    sec1.summary,
    a.executive_summary,
    a.executiveSummary,
    a.summary,
    meta.executive_summary,
  ];
  const text = firstText(...candidates);
  return text || "暫無中文摘要（可重跑分析）";
}

export function getPostUrl(inputConfig: Record<string, unknown> | undefined): string {
  const url = inputConfig?.url;
  if (typeof url === "string") return url;
  const target = inputConfig?.target;
  if (typeof target === "string") return target;
  return "";
}

export function normalizeForDedupe(raw?: string | null): string {
  if (!raw) return "";
  return raw
    .toLowerCase()
    .replace(/replying to\s*@[\w._-]+/gi, " ")
    .replace(/https?:\/\/\S+/gi, " ")
    .replace(/[\u{1F300}-\u{1FAFF}]/gu, " ")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function compactErrorMessage(input: unknown): string {
  const raw = String(input || "").trim();
  if (!raw) return "Unknown error";
  if (/resource temporarily unavailable/i.test(raw)) {
    return "資料來源暫時不可用，請稍後重試。";
  }
  if (/api\/posts failed/i.test(raw)) {
    return "讀取貼文失敗，請重新整理或稍後重試。";
  }
  return raw.length > 220 ? `${raw.slice(0, 220)}...` : raw;
}
