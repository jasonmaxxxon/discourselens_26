import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import templateHtml from "../stitch/pipeline_forensics_command_view.html?raw";
import { StitchTemplateFrame, type StitchActionMeta, type StitchNotice } from "../components/StitchTemplateFrame";
import { api, formatApiError } from "../lib/api";
import type { JobStatus } from "../lib/types";

const actionMap = {
  "run pipeline": "run_pipeline",
  "run analysis": "run_pipeline",
  "stop task": "stop_pipeline",
  "open insights": "pipeline_open_insights",
  "view insights": "pipeline_open_insights",
};

const actionSelectorMap = {
  "button[title='Clear Logs']": "pipeline_clear_logs",
  "button[title='Download']": "pipeline_download_logs",
};

function isActiveStatus(status: string | undefined): boolean {
  const s = String(status || "").toLowerCase();
  return s === "processing" || s === "discovering" || s === "running";
}

function toRunMode(label: string): string {
  const value = String(label || "").toLowerCase();
  if (value.includes("schema")) return "validate";
  if (value.includes("full")) return "ingest";
  return "analyze";
}

function makeNotice(message: string, kind: "info" | "ok" | "error" = "info"): StitchNotice {
  return { message, kind, nonce: Date.now() + Math.floor(Math.random() * 1000) };
}

function isFinishedStatus(status: string | undefined): boolean {
  const s = String(status || "").toLowerCase();
  return ["completed", "complete", "finished", "done", "success", "succeeded", "stale"].includes(s);
}

function isFailedStatus(status: string | undefined): boolean {
  const s = String(status || "").toLowerCase();
  return ["failed", "error", "canceled"].includes(s);
}

function resolveResultPostId(job: JobStatus | null): string {
  if (!job) return "";
  const items = [...(job.items || [])].sort(
    (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
  );
  const fromItem = items.find((row) => String(row.result_post_id || "").trim());
  if (fromItem?.result_post_id) return String(fromItem.result_post_id).trim();
  const cfg = job.input_config || {};
  const candidates = [
    cfg.post_id,
    cfg.result_post_id,
    cfg.target_post_id,
  ].map((value) => String(value || "").trim());
  return candidates.find(Boolean) || "";
}

function mapStageLabel(job: JobStatus | null): string {
  if (!job) return "Stage 0/5: idle";
  const rows = [...(job.items || [])].sort((a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime());
  const current = rows.find((it) => ["processing", "running", "discovering", "pending"].includes(String(it.status || "").toLowerCase())) || rows[0];
  if (!current) return `Stage: ${job.mode || "analyze"} · ${job.status || "pending"}`;
  return `Stage: ${current.stage || "init"} · ${current.status || "pending"}`;
}

function buildTelemetryLines(job: JobStatus | null): string[] {
  const lines = (job?.items || []).slice(-24).map((item) => {
    const stamp = String(item.updated_at || "").slice(11, 19) || "--:--:--";
    const msg = [item.stage, item.status, item.error_log].filter(Boolean).join(" · ");
    return `${stamp}|${msg || "waiting"}`;
  });
  if (lines.length) return lines;
  if (!job) return ["--:--:--|idle · no active run"];
  return [`--:--:--|${String(job.status || "queued")} · no item telemetry`];
}

function msSince(isoText: string | undefined, nowMs: number): number {
  if (!isoText) return 0;
  const stamp = new Date(String(isoText)).getTime();
  if (!Number.isFinite(stamp)) return 0;
  return Math.max(0, nowMs - stamp);
}

export function StitchPipelinePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const pendingSelectedJobRef = useRef<string>("");
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [selectedJobId, setSelectedJobId] = useState(() => {
    if (typeof window === "undefined") return "";
    const fromUrl = new URLSearchParams(window.location.search).get("job_id");
    const fromStorage = window.localStorage.getItem("dl.activeRunId");
    return String(fromUrl || fromStorage || "").trim();
  });
  const [endpointUrl, setEndpointUrl] = useState("https://www.threads.com/@user/post/...");
  const [telemetryLines, setTelemetryLines] = useState<string[]>([]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [notice, setNotice] = useState<StitchNotice | null>(null);
  const [tickMs, setTickMs] = useState(() => Date.now());

  const activeJob = useMemo(() => jobs.find((job) => isActiveStatus(job.status)) || null, [jobs]);
  const historyRuns = useMemo(
    () =>
      jobs
        .filter((job) => job.id !== activeJob?.id)
        .sort((a, b) => {
          const ta = new Date(a.updated_at || a.created_at || 0).getTime();
          const tb = new Date(b.updated_at || b.created_at || 0).getTime();
          return tb - ta;
        }),
    [activeJob?.id, jobs]
  );
  const focusedJob = useMemo(
    () => jobs.find((job) => job.id === selectedJobId) || activeJob || null,
    [activeJob, jobs, selectedJobId]
  );

  useEffect(() => {
    const fromUrl = String(searchParams.get("job_id") || "").trim();
    if (fromUrl && fromUrl !== selectedJobId) {
      if (pendingSelectedJobRef.current && selectedJobId === pendingSelectedJobRef.current) {
        return;
      }
      setSelectedJobId(fromUrl);
    }
  }, [searchParams, selectedJobId]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const next = String(selectedJobId || activeJob?.id || "").trim();
    if (!next) return;
    if (pendingSelectedJobRef.current === next) {
      pendingSelectedJobRef.current = "";
    }
    window.localStorage.setItem("dl.activeRunId", next);
    setSearchParams((prev) => {
      const query = new URLSearchParams(prev);
      if (query.get("job_id") === next) return query;
      query.set("job_id", next);
      return query;
    }, { replace: true });
  }, [activeJob?.id, selectedJobId, setSearchParams]);

  const refreshJobs = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const list = await api.listJobs();
      const active = list.find((job) => isActiveStatus(job.status)) || null;
      const fallbackId = selectedJobId && list.some((job) => job.id === selectedJobId) ? selectedJobId : (active?.id || "");
      if (fallbackId !== selectedJobId) setSelectedJobId(fallbackId);

      const detailIds = Array.from(new Set([fallbackId, active?.id].filter(Boolean))) as string[];
      const detailSettled = await Promise.allSettled(detailIds.map((id) => api.getJob(id)));
      const detailMap = new Map<string, JobStatus>();
      detailSettled.forEach((detail, idx) => {
        if (detail.status === "fulfilled" && detail.value?.id) {
          detailMap.set(detail.value.id, detail.value);
          return;
        }
        const failedJobId = detailIds[idx];
        if (failedJobId) {
          setNotice(makeNotice(`Run detail unavailable #${String(failedJobId).slice(0, 8)} · ${formatApiError((detail as PromiseRejectedResult).reason)}`, "info"));
        }
      });

      const merged = list.map((job) => detailMap.get(job.id) || job);
      setJobs(merged);
      const current = merged.find((job) => job.id === fallbackId) || active || null;
      setTelemetryLines(buildTelemetryLines(current));
      if (current?.input_config?.endpoint_url && typeof current.input_config.endpoint_url === "string") {
        setEndpointUrl(current.input_config.endpoint_url);
      }
      if (current?.id && typeof window !== "undefined") {
        window.localStorage.setItem("dl.activeRunId", String(current.id));
      }
    } catch (e) {
      setNotice(makeNotice(`Pipeline sync failed: ${formatApiError(e)}`, "error"));
    } finally {
      setIsRefreshing(false);
    }
  }, [selectedJobId]);

  useEffect(() => {
    void refreshJobs();
    const timer = window.setInterval(() => {
      void refreshJobs();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [refreshJobs]);

  useEffect(() => {
    const timer = window.setInterval(() => setTickMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const onAction = useCallback(
    async (action: string, meta: StitchActionMeta) => {
      if (action === "run_pipeline") {
        const startedAt = Date.now();
        const endpoint = String(meta.endpointUrl || endpointUrl || "").trim();
        const extractionMode = String(meta.extractionMode || "Incremental Load").trim() || "Incremental Load";
        if (!endpoint || !/^https?:\/\/(www\.)?(threads\.com|threads\.net)\//i.test(endpoint)) {
          setNotice(makeNotice("請輸入有效 Threads 貼文連結。", "error"));
          return;
        }
        setEndpointUrl(endpoint || endpointUrl);
        setIsStarting(true);
        try {
          const created = await api.createJob({
            pipeline_type: "A",
            mode: toRunMode(extractionMode),
            input_config: {
              endpoint_url: endpoint,
              threads_url: endpoint,
              url: endpoint,
              target: endpoint,
              targets: [endpoint],
              extraction_mode: extractionMode,
            },
          });
          if (created?.id) {
            const nextId = String(created.id);
            pendingSelectedJobRef.current = nextId;
            setSelectedJobId(nextId);
            if (typeof window !== "undefined") {
              window.localStorage.setItem("dl.activeRunId", nextId);
            }
            setJobs((prev) => [created, ...prev.filter((row) => row.id !== created.id)]);
            setTelemetryLines(buildTelemetryLines(created));
            setSearchParams((prev) => {
              const query = new URLSearchParams(prev);
              query.set("job_id", nextId);
              return query;
            }, { replace: true });
          }
          setNotice(makeNotice(`Run queued #${String(created.id || "-").slice(0, 8)}`, "ok"));
          void refreshJobs();
          return;
        } catch (e) {
          setNotice(makeNotice(`Run failed: ${formatApiError(e)}`, "error"));
          return;
        } finally {
          const elapsed = Date.now() - startedAt;
          const holdMs = Math.max(0, 900 - elapsed);
          window.setTimeout(() => setIsStarting(false), holdMs);
        }
      }

      if (action === "stop_pipeline") {
        if (!activeJob?.id) {
          setNotice(makeNotice("No active run to stop.", "info"));
          return;
        }
        try {
          await api.cancelJob(activeJob.id);
          setNotice(makeNotice(`Stopped run #${String(activeJob.id).slice(0, 8)}`, "ok"));
          await refreshJobs();
          return;
        } catch (e) {
          setNotice(makeNotice(`Stop failed: ${formatApiError(e)}`, "error"));
          return;
        }
      }

      if (action === "pipeline_select_run") {
        const nextId = String(meta.jobId || "").trim();
        if (!nextId) return;
        setSelectedJobId(nextId);
        try {
          const detail = await api.getJob(nextId);
          setJobs((prev) => {
            const found = prev.some((job) => job.id === detail.id);
            if (!found) return [detail, ...prev];
            return prev.map((job) => (job.id === detail.id ? detail : job));
          });
          setTelemetryLines(buildTelemetryLines(detail));
        } catch {
          setTelemetryLines(["--:--:--|queued · waiting for next event..."]);
        }
        return;
      }

      if (action === "pipeline_clear_logs") {
        setTelemetryLines(["--:--:--|telemetry cleared"]);
        setNotice(makeNotice("Telemetry log cleared.", "info"));
        return;
      }

      if (action === "pipeline_download_logs") {
        const blob = new Blob([telemetryLines.join("\n")], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `pipeline-telemetry-${Date.now()}.log`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setNotice(makeNotice("Telemetry log exported.", "ok"));
        return;
      }

      if (action === "pipeline_open_insights") {
        const hintPostId = String(meta.postId || "").trim();
        const resultPostId = hintPostId || resolveResultPostId(focusedJob);
        if (resultPostId) {
          window.localStorage.setItem("dl.activePostId", resultPostId);
          window.location.assign(`/insights?post_id=${encodeURIComponent(resultPostId)}`);
          return;
        }
        window.location.assign("/insights");
      }
    },
    [activeJob?.id, endpointUrl, focusedJob, refreshJobs, telemetryLines]
  );

  const focusedRunComplete = useMemo(() => {
    if (!focusedJob) return false;
    if (isFailedStatus(focusedJob.status)) return false;
    if (isFinishedStatus(focusedJob.status)) return true;
    if (focusedJob.finished_at) return true;
    const total = Number(focusedJob.total_count || 0);
    const processed = Number(focusedJob.processed_count || 0);
    return total > 0 && processed >= total;
  }, [focusedJob]);

  const insightsPostId = useMemo(() => resolveResultPostId(focusedJob), [focusedJob]);
  const heartbeatLagMs = useMemo(
    () => msSince(focusedJob?.updated_at, tickMs),
    [focusedJob?.updated_at, tickMs]
  );
  const maybeStuck = useMemo(
    () => Boolean(focusedJob && isActiveStatus(focusedJob.status) && heartbeatLagMs >= 120000),
    [focusedJob, heartbeatLagMs]
  );

  const bridgeData = useMemo(
    () => ({
      page: "pipeline",
      endpointUrl,
      activeJob,
      displayJob: focusedJob,
      hasActiveRun: Boolean(activeJob?.id),
      queuedHint: "SAMPLE / TEST DATA FOR UI PURPOSE",
      selectedJobId,
      stageLabel: mapStageLabel(focusedJob),
      isFetching: isRefreshing || isStarting,
      isStarting,
      isComplete: focusedRunComplete,
      canOpenInsights: focusedRunComplete,
      insightsPostId,
      uiStatus: maybeStuck ? "stalled" : String(focusedJob?.status || activeJob?.status || "idle"),
      clientNowMs: tickMs,
      heartbeatLagMs,
      maybeStuck,
      queuedJobs: historyRuns.map((job) => ({
        id: job.id,
        status: job.status,
        pipeline_type: job.pipeline_type,
        name: `${job.pipeline_type || "run"}_${String(job.id).slice(0, 6)}`,
      })),
      logs: telemetryLines,
    }),
    [
      activeJob,
      endpointUrl,
      focusedJob,
      focusedRunComplete,
      heartbeatLagMs,
      historyRuns,
      insightsPostId,
      isRefreshing,
      isStarting,
      maybeStuck,
      selectedJobId,
      telemetryLines,
      tickMs,
    ]
  );

  return (
    <StitchTemplateFrame
      html={templateHtml}
      title="Pipeline Forensics"
      pageId="pipeline"
      actionMap={actionMap}
      actionSelectorMap={actionSelectorMap}
      bridgeData={bridgeData}
      onAction={onAction}
      notice={notice}
      hideTemplateHeader
    />
  );
}
