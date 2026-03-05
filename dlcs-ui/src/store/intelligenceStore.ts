import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { JobStatus, JobSummary } from "../lib/types";

type ContextPost = {
  id?: string;
  snippet: string;
  url?: string | null;
};

type ContextPhenomenon = {
  id?: string;
  name: string;
  status?: string;
};

type LastRunContext = {
  id: string;
  status: string;
  updatedAt?: string | null;
  runningCount?: number;
  queuedCount?: number;
};

type JobSummaryLite = {
  status: string;
  processedCount: number;
  totalCount: number;
  failedCount: number;
  degraded: boolean;
  lastItemUpdatedAt?: string | null;
};

type IntelligenceStore = {
  currentPost: ContextPost | null;
  phenomenon: ContextPhenomenon | null;
  riskLevel: string;
  stabilityVerdict: string;
  lastRun: LastRunContext | null;
  jobs: JobStatus[];
  jobsSig: string;
  jobsDegraded: boolean;
  telemetryDegraded: boolean;
  jobSummary: JobSummary | null;
  jobSummaryLite: JobSummaryLite | null;
  setCurrentPost: (post: ContextPost | null) => void;
  setPhenomenon: (value: ContextPhenomenon | null) => void;
  setRiskLevel: (value: string) => void;
  setStabilityVerdict: (value: string) => void;
  setLastRun: (value: LastRunContext | null) => void;
  setJobsSnapshot: (jobs: JobStatus[], degraded: boolean) => void;
  setTelemetryDegraded: (value: boolean) => void;
  setJobSummary: (value: JobSummary | null) => void;
  setJobSummaryLite: (value: JobSummaryLite | null) => void;
  patch: (value: Partial<Pick<IntelligenceStore, "riskLevel" | "stabilityVerdict">>) => void;
};

function jobsSignature(rows: JobStatus[]): string {
  return rows
    .map((job) => `${job.id}:${job.status}:${job.processed_count}/${job.total_count}:${job.updated_at || ""}`)
    .join("|");
}

function jobSummarySignature(summary: JobSummary | null): string {
  if (!summary) return "null";
  return [
    summary.status || "",
    `${summary.processed_count}/${summary.total_count}`,
    summary.failed_count || 0,
    summary.degraded ? 1 : 0,
    summary.last_item_updated_at || "",
    summary.last_heartbeat_at || "",
  ].join("|");
}

function toSummaryLite(summary: JobSummary | null): JobSummaryLite | null {
  if (!summary) return null;
  return {
    status: String(summary.status || "-"),
    processedCount: Number(summary.processed_count || 0),
    totalCount: Number(summary.total_count || 0),
    failedCount: Number(summary.failed_count || 0),
    degraded: Boolean(summary.degraded),
    lastItemUpdatedAt: summary.last_item_updated_at || null,
  };
}

function clippedText(value: unknown, max: number): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  return raw.length > max ? `${raw.slice(0, max)}...` : raw;
}

export const useIntelligenceStore = create<IntelligenceStore>()(
  persist(
    (set, get) => ({
      currentPost: null,
      phenomenon: null,
      riskLevel: "-",
      stabilityVerdict: "-",
      lastRun: null,
      jobs: [],
      jobsSig: "",
      jobsDegraded: false,
      telemetryDegraded: false,
      jobSummary: null,
      jobSummaryLite: null,
      setCurrentPost: (currentPost) => set({ currentPost }),
      setPhenomenon: (phenomenon) => set({ phenomenon }),
      setRiskLevel: (riskLevel) => set({ riskLevel: riskLevel || "-" }),
      setStabilityVerdict: (stabilityVerdict) => set({ stabilityVerdict: stabilityVerdict || "-" }),
      setLastRun: (lastRun) => {
        const prev = get().lastRun;
        const nextSig = lastRun ? `${lastRun.id}|${lastRun.status}|${lastRun.updatedAt || ""}` : "null";
        const prevSig = prev ? `${prev.id}|${prev.status}|${prev.updatedAt || ""}` : "null";
        if (nextSig === prevSig) return;
        set({ lastRun });
      },
      setJobsSnapshot: (jobs, degraded) => {
        const nextSig = jobsSignature(jobs);
        const prevSig = get().jobsSig;
        const prevDegraded = get().jobsDegraded;
        if (nextSig === prevSig && degraded === prevDegraded) return;
        set({ jobs, jobsSig: nextSig, jobsDegraded: degraded });
      },
      setTelemetryDegraded: (value) => {
        if (get().telemetryDegraded === value) return;
        set({ telemetryDegraded: value });
      },
      setJobSummary: (jobSummary) => {
        const prev = get().jobSummary;
        if (jobSummarySignature(prev) === jobSummarySignature(jobSummary)) return;
        set({ jobSummary, jobSummaryLite: toSummaryLite(jobSummary) });
      },
      setJobSummaryLite: (jobSummaryLite) => {
        const prev = get().jobSummaryLite;
        const prevSig = prev
          ? `${prev.status}|${prev.processedCount}/${prev.totalCount}|${prev.failedCount}|${prev.degraded ? 1 : 0}|${prev.lastItemUpdatedAt || ""}`
          : "null";
        const nextSig = jobSummaryLite
          ? `${jobSummaryLite.status}|${jobSummaryLite.processedCount}/${jobSummaryLite.totalCount}|${jobSummaryLite.failedCount}|${
              jobSummaryLite.degraded ? 1 : 0
            }|${jobSummaryLite.lastItemUpdatedAt || ""}`
          : "null";
        if (prevSig === nextSig) return;
        set({ jobSummaryLite });
      },
      patch: (value) =>
        set((state) => ({
          riskLevel: value.riskLevel ?? state.riskLevel,
          stabilityVerdict: value.stabilityVerdict ?? state.stabilityVerdict,
        })),
    }),
    {
      name: "dl.intelligence.pointers.v1",
      storage: createJSONStorage(() => localStorage),
      skipHydration: true,
      partialize: (state) => ({
        currentPost: state.currentPost
          ? {
              id: state.currentPost.id,
              snippet: clippedText(state.currentPost.snippet, 140),
              url: state.currentPost.url || null,
            }
          : null,
        phenomenon: state.phenomenon
          ? {
              id: state.phenomenon.id,
              name: clippedText(state.phenomenon.name, 64),
              status: state.phenomenon.status,
            }
          : null,
        lastRun: state.lastRun
          ? {
              id: state.lastRun.id,
              status: state.lastRun.status,
              updatedAt: state.lastRun.updatedAt || null,
            }
          : null,
      }),
    }
  )
);
