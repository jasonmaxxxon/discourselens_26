import { useEffect, useRef } from "react";
import { api, isDegradedApiError } from "../lib/api";
import { useIntelligenceStore } from "../store/intelligenceStore";

const ACTIVE_POLL_MS = 2500;
const IDLE_POLL_MS = 15000;
const HIDDEN_POLL_MS = 15000;

function isLiveStatus(status: string): boolean {
  const s = String(status || "").toLowerCase();
  return s === "processing" || s === "discovering" || s === "queued" || s === "pending";
}

function nextDelayMs(hasLiveWork: boolean): number {
  if (document.visibilityState === "hidden") return HIDDEN_POLL_MS;
  return hasLiveWork ? ACTIVE_POLL_MS : IDLE_POLL_MS;
}

export function useTelemetryLoop(enabled = true) {
  const setJobsSnapshot = useIntelligenceStore((s) => s.setJobsSnapshot);
  const setLastRun = useIntelligenceStore((s) => s.setLastRun);
  const setJobSummary = useIntelligenceStore((s) => s.setJobSummary);
  const setTelemetryDegraded = useIntelligenceStore((s) => s.setTelemetryDegraded);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!enabled) {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      return;
    }

    let alive = true;

    const schedule = (hasLiveWork: boolean) => {
      if (timerRef.current) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(tick, nextDelayMs(hasLiveWork));
    };

    const tick = async () => {
      let hasLiveWork = false;
      try {
        const jobsMeta = await api.listJobsMeta();
        if (!alive) return;
        const jobs = jobsMeta.data || [];
        hasLiveWork = jobs.some((j) => isLiveStatus(j.status));
        setJobsSnapshot(jobs, jobsMeta.degraded);

        const persistedRunId = localStorage.getItem("dl.activeRunId");
        const stateRunId = useIntelligenceStore.getState().lastRun?.id;
        const selectedId = persistedRunId || stateRunId || "";
        const selected = selectedId ? jobs.find((j) => j.id === selectedId) || null : null;
        const preferred =
          selected ||
          jobs.find((j) => {
            const s = String(j.status || "").toLowerCase();
            return s === "processing" || s === "discovering" || s === "queued" || s === "pending";
          }) ||
          jobs[0];

        if (!preferred?.id) {
          setJobSummary(null);
          setTelemetryDegraded(jobsMeta.degraded);
          return;
        }

        if (selectedId !== preferred.id) {
          localStorage.setItem("dl.activeRunId", preferred.id);
        }

        const runningCount = jobs.filter((j) => {
          const s = String(j.status || "").toLowerCase();
          return s === "processing" || s === "discovering";
        }).length;
        const queuedCount = jobs.filter((j) => {
          const s = String(j.status || "").toLowerCase();
          return s === "queued" || s === "pending";
        }).length;

        if (!hasLiveWork) {
          setLastRun({
            id: preferred.id,
            status: preferred.status,
            updatedAt: preferred.updated_at,
            runningCount,
            queuedCount,
          });
          setTelemetryDegraded(Boolean(jobsMeta.degraded));
          return;
        }

        const detail = await api.getJob(preferred.id);
        if (!alive) return;
        setLastRun({
          id: detail.id,
          status: detail.status,
          updatedAt: detail.updated_at,
          runningCount,
          queuedCount,
        });

        const summaryMeta = await api.getJobSummaryMeta(preferred.id);
        if (!alive) return;
        const summary = { ...summaryMeta.data, degraded: summaryMeta.degraded || summaryMeta.data.degraded };
        setJobSummary(summary);
        setTelemetryDegraded(Boolean(jobsMeta.degraded || summaryMeta.degraded || summary.degraded));
      } catch (error) {
        if (!alive) return;
        if (isDegradedApiError(error)) {
          setTelemetryDegraded(true);
        }
      } finally {
        if (alive) schedule(hasLiveWork);
      }
    };

    const onVisibility = () => {
      if (!alive) return;
      if (timerRef.current) window.clearTimeout(timerRef.current);
      tick();
    };

    tick();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [enabled, setJobSummary, setJobsSnapshot, setLastRun, setTelemetryDegraded]);
}
