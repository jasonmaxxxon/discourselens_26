import { normalizeForDedupe } from "./format";
import type { EvidenceItem } from "./types";

const EVIDENCE_PREVIEW_LIMIT = 10;

function normalizeClusterKey(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function sortEvidence(rows: EvidenceItem[]): EvidenceItem[] {
  return [...rows].sort((a, b) => {
    const likesA = Number(a.like_count || 0);
    const likesB = Number(b.like_count || 0);
    if (likesB !== likesA) return likesB - likesA;

    const tsA = a.created_at ? new Date(a.created_at).getTime() : 0;
    const tsB = b.created_at ? new Date(b.created_at).getTime() : 0;
    const safeA = Number.isFinite(tsA) ? tsA : 0;
    const safeB = Number.isFinite(tsB) ? tsB : 0;
    return safeB - safeA;
  });
}

export function evidenceStableKey(row: EvidenceItem): string {
  const locator = String(row.locator_key || "").trim();
  if (locator) return `locator:${locator}`;
  const evidenceType = String(row.evidence_type || "").trim().toLowerCase();
  const evidenceId = row.evidence_id === null || row.evidence_id === undefined ? "" : String(row.evidence_id).trim();
  if (evidenceType === "comment_id" && evidenceId) return `comment:${evidenceId}`;
  if (evidenceId) return `evidence:${evidenceType || "unknown"}:${evidenceId}`;
  if (row.id) return `id:${row.id}`;
  const textKey = normalizeForDedupe(row.text || "");
  return `fallback:${row.author_handle || ""}|${textKey}|${row.created_at || ""}`;
}

function dedupeEvidence(rows: EvidenceItem[]): EvidenceItem[] {
  const seen = new Set<string>();
  const out: EvidenceItem[] = [];

  for (const row of rows) {
    const key = evidenceStableKey(row);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }

  return out;
}

export function dedupeEvidenceWithStats(rows: EvidenceItem[]): { items: EvidenceItem[]; removed: number } {
  const deduped = dedupeEvidence(rows);
  return { items: deduped, removed: Math.max(0, rows.length - deduped.length) };
}

function clusterEvidence(evidence: EvidenceItem[], selectedClusterKey?: number): EvidenceItem[] {
  const target = normalizeClusterKey(selectedClusterKey);
  if (target === null) return [];
  return evidence.filter((ev) => normalizeClusterKey(ev.cluster_key) === target);
}

export function selectUniqueEvidenceForCluster(evidence: EvidenceItem[], selectedClusterKey?: number): EvidenceItem[] {
  return dedupeEvidence(sortEvidence(clusterEvidence(evidence, selectedClusterKey)));
}

export function selectEvidencePreview(evidence: EvidenceItem[], selectedClusterKey?: number): EvidenceItem[] {
  const sorted = sortEvidence(clusterEvidence(evidence, selectedClusterKey));
  if (sorted.length <= EVIDENCE_PREVIEW_LIMIT) return sorted;

  const uniqueTop = dedupeEvidence(sorted);
  if (uniqueTop.length >= EVIDENCE_PREVIEW_LIMIT) {
    return uniqueTop.slice(0, EVIDENCE_PREVIEW_LIMIT);
  }

  const used = new Set(uniqueTop.map((row) => evidenceStableKey(row)));
  const filled = [...uniqueTop];
  for (const row of sorted) {
    if (filled.length >= EVIDENCE_PREVIEW_LIMIT) break;
    const key = evidenceStableKey(row);
    if (!key || used.has(key)) continue;
    used.add(key);
    filled.push(row);
  }
  return filled.slice(0, EVIDENCE_PREVIEW_LIMIT);
}
