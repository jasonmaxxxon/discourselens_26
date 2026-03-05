type FocusTermInput = {
  text: string;
  created_at?: string | null;
};

type FocusTermStat = {
  token: string;
  score: number;
  freq: number;
  firstSeen: number;
};

const STOP_WORDS = new Set([
  "我哋",
  "你哋",
  "佢哋",
  "嗰個",
  "呢個",
  "而家",
  "因為",
  "所以",
]);

const STOP_CHARS = new Set(["我", "你", "佢", "哋", "嘅", "喺", "係", "咁", "呀", "啦", "個", "呢", "嗰", "咗", "都", "就", "又", "同", "及", "或", "唔", "冇", "有"]);

function isLikelyNoise(token: string): boolean {
  const text = token.trim();
  if (!text || text.length < 2) return true;
  if (STOP_WORDS.has(text)) return true;
  if (/^([^\d])\1+$/.test(text)) return true;
  if (/[0-9]/.test(text)) return true;

  const chars = Array.from(text);
  const stopCharCount = chars.filter((char) => STOP_CHARS.has(char)).length;
  if (stopCharCount === chars.length) return true;
  if (text.length === 2 && (STOP_CHARS.has(chars[0]) || STOP_CHARS.has(chars[1]))) return true;
  if (text.length === 3 && stopCharCount >= 2) return true;
  return false;
}

function extractHanTerms(text: string): string[] {
  const out: string[] = [];
  const groups = text.match(/[\u4E00-\u9FFF]+/g) || [];
  for (const group of groups) {
    const chars = Array.from(group);
    for (const n of [2, 3]) {
      if (chars.length < n) continue;
      for (let i = 0; i <= chars.length - n; i += 1) {
        const token = chars.slice(i, i + n).join("");
        if (isLikelyNoise(token)) continue;
        out.push(token);
      }
    }
  }
  return out;
}

export function extractFocusTerms(input: FocusTermInput[], limit = 5): string[] {
  if (!input.length) return [];
  const sorted = [...input].sort((a, b) => {
    const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
    return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
  });

  const stats = new Map<string, FocusTermStat>();
  for (let i = 0; i < sorted.length; i += 1) {
    const row = sorted[i];
    const terms = extractHanTerms(String(row.text || ""));
    const recencyWeight = 1 + (sorted.length - i) / Math.max(1, sorted.length);
    for (const token of terms) {
      const prev = stats.get(token);
      if (!prev) {
        stats.set(token, { token, score: recencyWeight, freq: 1, firstSeen: i });
      } else {
        prev.freq += 1;
        prev.score += recencyWeight;
      }
    }
  }

  return [...stats.values()]
    .filter((row) => row.freq >= 1)
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      if (b.freq !== a.freq) return b.freq - a.freq;
      if (a.firstSeen !== b.firstSeen) return a.firstSeen - b.firstSeen;
      return a.token.localeCompare(b.token, "zh-Hant");
    })
    .slice(0, Math.max(1, limit))
    .map((row) => row.token);
}
