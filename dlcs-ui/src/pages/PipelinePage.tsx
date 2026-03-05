import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, isDegradedApiError } from "../lib/api";
import type { JobStatus } from "../lib/types";
import { fmtDate } from "../lib/format";
import { PageHeader } from "../components/PageHeader";
import { SectionCard } from "../components/SectionCard";
import { readRouteSnapshot, writeRouteSnapshot } from "../lib/routeCache";
import { useIntelligenceStore } from "../store/intelligenceStore";

const TERMINAL_STATUSES = new Set(["completed", "failed", "canceled"]);
type StageKey = "ingest" | "quant" | "healthgate" | "llm";

const PIPELINE_STAGES: Array<{
  key: StageKey;
  label: string;
  from: string;
  to: string;
}> = [
  { key: "ingest", label: "Ingest", from: "Source", to: "DB_Raw" },
  { key: "quant", label: "Quant", from: "DB_Raw", to: "DB_Q1" },
  { key: "healthgate", label: "HealthGate", from: "DB_Q1", to: "DB_V8_Candidate" },
  { key: "llm", label: "LLM", from: "DB_V8_Candidate", to: "DB_V8_Final" },
];

function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(String(status || "").toLowerCase());
}

function isActiveStatus(status: string): boolean {
  const s = String(status || "").toLowerCase();
  return s === "processing" || s === "discovering";
}

function isQueueStatus(status: string): boolean {
  const s = String(status || "").toLowerCase();
  return s === "queued" || s === "pending";
}

function isAnyActiveStatus(status: string): boolean {
  return isActiveStatus(status) || isQueueStatus(status);
}

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function stageLabel(stage: StageKey): string {
  return PIPELINE_STAGES.find((s) => s.key === stage)?.label || "Ingest";
}

function normalizeStage(rawStage: string): StageKey {
  const s = String(rawStage || "").toLowerCase();
  if (!s) return "ingest";
  if (/(fetch|ingest|discover|init)/.test(s)) return "ingest";
  if (/(quant|preanalysis|pre-analysis|cluster|vision|embed|analyst)/.test(s)) return "quant";
  if (/(health|integrity|audit|risk|gate|cip)/.test(s)) return "healthgate";
  if (/(llm|claim|narrative|store)/.test(s)) return "llm";
  return "ingest";
}

function currentStageFromJob(job: JobStatus): StageKey {
  const status = String(job.status || "").toLowerCase();
  if (status === "completed") return "llm";
  if (status === "queued" || status === "discovering" || status === "pending") return "ingest";

  const items = [...(job.items || [])].sort(
    (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
  );
  const processingItem = items.find((it) => {
    const s = String(it.status || "").toLowerCase();
    return s === "processing" || s === "discovering" || s === "running";
  });
  if (processingItem) return normalizeStage(processingItem.stage);

  const failedItem = items.find((it) => String(it.status || "").toLowerCase() === "failed");
  if (failedItem) return normalizeStage(failedItem.stage);

  if (items[0]?.stage) return normalizeStage(items[0].stage);
  if (status === "failed" || status === "canceled" || status === "stale") return "healthgate";
  return "quant";
}

function stageFromJob(job: JobStatus): string {
  const status = String(job.status || "").toLowerCase();
  const items = job.items || [];
  if (status === "canceled") return "Pipeline stopped by operator.";
  if (status === "stale") return "Worker heartbeat stale. Stop task and rerun.";
  if (status === "queued" || status === "discovering") return "Queued. Waiting for worker slot...";
  const failed = items.find((it) => it.status === "failed");
  if (failed) return `Pipeline failed at ${failed.stage}.`;
  if (status === "failed") return "Pipeline failed.";
  const processing = items.find((it) => it.status === "processing" || it.status === "running");
  if (processing) return `Running ${stageLabel(normalizeStage(processing.stage))}...`;
  if (status === "completed") return "Pipeline completed.";
  return "Initializing analysis engine...";
}

function nowSec(): number {
  return Math.floor(Date.now() / 1000);
}

function elapsedText(startSec?: number, endSec?: number): string {
  if (!startSec) return "0.0s";
  const d = Math.max(0, (endSec ?? nowSec()) - startSec);
  return d < 60 ? `${d.toFixed(1)}s` : `${Math.floor(d / 60)}m ${d % 60}s`;
}

function logKeyForJob(jobId: string): string {
  return `dl.jobLog.${jobId}`;
}

type PipelineSnapshot = {
  jobs: JobStatus[];
  selectedJobId: string;
  selectedJob: JobStatus | null;
};

const CACHE_KEY = "dl.cache.route.pipeline.v1";

export function PipelinePage() {
  const navigate = useNavigate();
  const setJobsSnapshot = useIntelligenceStore((s) => s.setJobsSnapshot);
  const setLastRun = useIntelligenceStore((s) => s.setLastRun);
  const setJobSummary = useIntelligenceStore((s) => s.setJobSummary);
  const setTelemetryDegraded = useIntelligenceStore((s) => s.setTelemetryDegraded);
  const setStabilityVerdict = useIntelligenceStore((s) => s.setStabilityVerdict);
  const initial = readRouteSnapshot<PipelineSnapshot>(CACHE_KEY);
  const initialSelectedJobId = localStorage.getItem("dl.activeRunId") || initial?.data.selectedJobId || "";
  const [url, setUrl] = useState("");
  const [jobs, setJobs] = useState<JobStatus[]>(initial?.data.jobs || []);
  const [selectedJobId, setSelectedJobId] = useState<string>(initialSelectedJobId);
  const [selectedJob, setSelectedJob] = useState<JobStatus | null>(() => {
    const cached = initial?.data.selectedJob || null;
    if (!cached) return null;
    if (!initialSelectedJobId) return cached;
    return cached.id === initialSelectedJobId ? cached : null;
  });
  const [logLines, setLogLines] = useState<string[]>(() => {
    try {
      const active = initialSelectedJobId;
      if (!active) return [];
      return JSON.parse(localStorage.getItem(logKeyForJob(active)) || "[]");
    } catch {
      return [];
    }
  });
  const [logOpen, setLogOpen] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const [error, setError] = useState("");
  const [degraded, setDegraded] = useState(false);
  const [syncing, setSyncing] = useState(Boolean(initial));
  const [loading, setLoading] = useState(!(initial?.data.jobs?.length || initial?.data.selectedJob));
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [stoppingJobId, setStoppingJobId] = useState("");
  const logRef = useRef<HTMLPreElement | null>(null);
  const lastJobsSigRef = useRef("");
  const lastLogSigRef = useRef<Record<string, string>>({});

  const pollDelayMs = () => (document.visibilityState === "hidden" ? 15000 : 3000);

  useEffect(() => {
    if (!selectedJobId) return;
    try {
      const stored = JSON.parse(localStorage.getItem(logKeyForJob(selectedJobId)) || "[]") as string[];
      setLogLines(stored);
    } catch {
      setLogLines([]);
    }
  }, [selectedJobId]);

  useEffect(() => {
    let alive = true;
    let timer: number | null = null;
    setSyncing(true);
    if (!jobs.length && !selectedJob) setLoading(true);

    const schedule = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(poll, pollDelayMs());
    };

    const poll = async () => {
      try {
        const jobsMeta = await api.listJobsMeta();
        const data = jobsMeta.data || [];
        if (!alive) return;
        const sig = data
          .map((j) => `${j.id}:${j.status}:${j.processed_count}/${j.total_count}:${j.updated_at || ""}`)
          .join("|");
        if (sig !== lastJobsSigRef.current) {
          setJobs(data);
          lastJobsSigRef.current = sig;
        }
        setJobsSnapshot(data, jobsMeta.degraded);

        const activeJobs = data.filter((j) => isAnyActiveStatus(j.status));
        const selected = selectedJobId ? data.find((j) => j.id === selectedJobId) || null : null;
        const currentId = selected && isAnyActiveStatus(selected.status) ? selected.id : activeJobs[0]?.id;
        if (!currentId) {
          setSelectedJobId("");
          setSelectedJob(null);
          setJobSummary(null);
          setTelemetryDegraded(Boolean(jobsMeta.degraded));
          localStorage.removeItem("dl.activeRunId");
          return;
        }
        if (!selectedJobId || selectedJobId !== currentId) {
          setSelectedJobId(currentId);
          localStorage.setItem("dl.activeRunId", currentId);
        }

        const detail = await api.getJob(currentId);
        if (!alive) return;
        setSelectedJob((prev) => {
          const prevSig = prev ? `${prev.id}|${prev.status}|${prev.updated_at}|${prev.processed_count}/${prev.total_count}` : "null";
          const nextSig = `${detail.id}|${detail.status}|${detail.updated_at}|${detail.processed_count}/${detail.total_count}`;
          return prevSig === nextSig ? prev : detail;
        });
        setLastRun({
          id: detail.id,
          status: detail.status,
          updatedAt: detail.updated_at,
          runningCount: data.filter((j) => isActiveStatus(j.status)).length,
          queuedCount: data.filter((j) => isQueueStatus(j.status)).length,
        });
        setStabilityVerdict(isTerminalStatus(detail.status) && detail.status === "completed" ? "stable" : detail.status);
        try {
          const summaryMeta = await api.getJobSummaryMeta(currentId);
          const summary = { ...summaryMeta.data, degraded: summaryMeta.degraded || summaryMeta.data.degraded };
          if (alive) setJobSummary(summary);
          const anyDegraded = Boolean(summary.degraded || jobsMeta.degraded);
          if (anyDegraded) {
            setDegraded(true);
          } else {
            setDegraded(false);
          }
          setTelemetryDegraded(anyDegraded);
        } catch {
          if (alive) {
            setJobSummary(null);
            setTelemetryDegraded(Boolean(jobsMeta.degraded));
          }
        }

        const stageLine = stageFromJob(detail);
        const signature = `${detail.status}|${stageLine}|${detail.failed_count}|${detail.processed_count}/${detail.total_count}`;
        const line = `> ${stageLine} [${detail.status}]`;
        setLogLines((prev) => {
          const lastSig = lastLogSigRef.current[currentId] || "";
          if (lastSig === signature) return prev;
          lastLogSigRef.current[currentId] = signature;
          return [...prev, line].slice(-80);
        });
        writeRouteSnapshot<PipelineSnapshot>(CACHE_KEY, {
          jobs: data,
          selectedJobId: currentId,
          selectedJob: detail,
        });
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        if (isDegradedApiError(e)) {
          setDegraded(true);
          setTelemetryDegraded(true);
        }
        setJobSummary(null);
      } finally {
        if (!alive) return;
        setLoading(false);
        setSyncing(false);
        schedule();
      }
    };

    poll();
    const onVisibility = () => {
      if (!alive) return;
      if (timer) window.clearTimeout(timer);
      poll();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer) window.clearTimeout(timer);
    };
  }, [refreshNonce, selectedJobId, setJobSummary, setJobsSnapshot, setLastRun, setStabilityVerdict, setTelemetryDegraded]);

  useEffect(() => {
    if (!autoScroll || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logLines, autoScroll]);

  const currentElapsed = useMemo(() => {
    if (!selectedJob?.created_at) return "0.0s";
    const start = Math.floor(new Date(selectedJob.created_at).getTime() / 1000);
    const status = String(selectedJob.status || "").toLowerCase();
    if (isTerminalStatus(status) && selectedJob.finished_at) {
      const end = Math.floor(new Date(selectedJob.finished_at).getTime() / 1000);
      return elapsedText(start, end);
    }
    return elapsedText(start);
  }, [selectedJob?.created_at, selectedJob?.finished_at, selectedJob?.status, selectedJob?.updated_at]);

  const stageLine = useMemo(() => (selectedJob ? stageFromJob(selectedJob) : "Idle • Ready"), [selectedJob]);
  const canStopSelected = !!selectedJob && !isTerminalStatus(selectedJob.status);
  const selectedIsActive = !!selectedJob && isActiveStatus(selectedJob.status);
  const runningCount = useMemo(() => jobs.filter((job) => isActiveStatus(job.status)).length, [jobs]);
  const queuedCount = useMemo(() => jobs.filter((job) => isQueueStatus(job.status)).length, [jobs]);
  const hasActive = runningCount > 0 || queuedCount > 0;
  const lastCompleted = useMemo(
    () =>
      [...jobs]
        .filter((job) => String(job.status || "").toLowerCase() === "completed")
        .sort((a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime())[0] || null,
    [jobs]
  );

  const elapsedSeconds = useMemo(() => {
    if (!selectedJob?.created_at) return 0;
    const start = new Date(selectedJob.created_at).getTime();
    const end = selectedJob.finished_at ? new Date(selectedJob.finished_at).getTime() : Date.now();
    if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
    return Math.max(0, Math.round((end - start) / 1000));
  }, [selectedJob?.created_at, selectedJob?.finished_at, selectedJob?.updated_at]);

  const currentStage = selectedJob ? currentStageFromJob(selectedJob) : "ingest";
  const stageIndex = PIPELINE_STAGES.findIndex((s) => s.key === currentStage);
  const structuralProgress = ((Math.max(0, stageIndex) + (selectedIsActive ? 0.45 : 0.2)) / PIPELINE_STAGES.length) * 100;
  const progressPct = useMemo(() => {
    if (!selectedJob) return 8;
    if (String(selectedJob.status || "").toLowerCase() === "completed") return 100;
    const total = Number(selectedJob.total_count || 0);
    const processed = Number(selectedJob.processed_count || 0);
    const ratioProgress = total > 0 ? (processed / total) * 100 : 0;
    const cadenceBoost = selectedIsActive ? Math.min(11, elapsedSeconds * 0.14) : 0;
    return Math.round(clamp(Math.max(structuralProgress + cadenceBoost, ratioProgress), 6, 100));
  }, [selectedJob, elapsedSeconds, selectedIsActive, structuralProgress]);

  const llmInFlight = selectedIsActive && currentStage === "llm";
  const stageCards = useMemo(
    () =>
      PIPELINE_STAGES.map((stage, idx) => {
        const selectedStatus = String(selectedJob?.status || "").toLowerCase();
        const done = selectedStatus === "completed" ? idx <= stageIndex : idx < stageIndex;
        const active = hasActive && idx === stageIndex;
        const failed = Boolean(selectedJob && (selectedStatus === "failed" || selectedStatus === "stale") && idx === stageIndex);
        const state = failed ? "failed" : active ? "processing" : done ? "completed" : "idle";
        return { ...stage, state };
      }),
    [hasActive, selectedJob, stageIndex]
  );

  const logBody = useMemo(() => {
    const body = logLines.join("\n") || "> Waiting...";
    if (selectedIsActive) return `${body}\n> waiting for next event... ▋`;
    return body;
  }, [logLines, selectedIsActive]);

  const onRun = async (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setError("");
    const optimisticId = `pending-${Date.now().toString(36)}`;
    const nowIso = new Date().toISOString();
    const optimisticJob: JobStatus = {
      id: optimisticId,
      status: "queued",
      pipeline_type: "A",
      mode: "analyze",
      total_count: 1,
      processed_count: 0,
      success_count: 0,
      failed_count: 0,
      created_at: nowIso,
      updated_at: nowIso,
      finished_at: null,
      input_config: { url: url.trim(), target: url.trim(), targets: [url.trim()] },
      items: [],
    };
    setJobs((prev) => [optimisticJob, ...prev.filter((j) => j.id !== optimisticId)].slice(0, 24));
    try {
      const payload = {
        pipeline_type: "A",
        mode: "analyze",
        input_config: { url: url.trim(), target: url.trim(), targets: [url.trim()] },
      };
      const job = await api.createJob(payload);
      setJobs((prev) => [job, ...prev.filter((j) => j.id !== optimisticId && j.id !== job.id)].slice(0, 24));
      setSelectedJobId(job.id);
      setSelectedJob(job);
      setLastRun({
        id: job.id,
        status: job.status,
        updatedAt: job.updated_at,
      });
      const initial = ["> Initializing analysis engine... [OK]", "> Fetching target URL content... [RUNNING]"];
      setLogLines(initial);
      localStorage.setItem("dl.activeRunId", job.id);
      localStorage.setItem(logKeyForJob(job.id), JSON.stringify(initial));
      lastLogSigRef.current[job.id] = "";
      setUrl("");
      setRefreshNonce((v) => v + 1);
    } catch (err) {
      setJobs((prev) => prev.filter((j) => j.id !== optimisticId));
      setError(err instanceof Error ? err.message : String(err));
      if (isDegradedApiError(err)) setDegraded(true);
    }
  };

  const onStopJob = async (jobId: string) => {
    if (!jobId) return;
    try {
      setError("");
      setStoppingJobId(jobId);
      await api.cancelJob(jobId);
      if (selectedJobId === jobId) {
        const msg = "> Stop requested by operator [CANCELING]";
        setLogLines((prev) => {
          const next = [...prev, msg].slice(-80);
          localStorage.setItem(logKeyForJob(jobId), JSON.stringify(next));
          return next;
        });
      }
      setRefreshNonce((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStoppingJobId("");
    }
  };

  return (
    <div className="page-grid">
      <PageHeader
        title="Pipeline A"
        subtitle="可同時排隊多個 run，切頁後仍會保留進度。"
        actions={
          <>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/insights")}>
              Open Insights
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/review")}>
              Open Review
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => setRefreshNonce((v) => v + 1)}>
              Refresh now
            </button>
          </>
        }
      />

      {degraded ? <div className="degraded-banner">作業狀態通道暫時降級，已保留最近執行快照；可繼續操作。</div> : null}
      {syncing ? <div className="sync-banner">同步作業佇列中…</div> : null}
      {error ? <div className="error-banner">{error}</div> : null}

      <div className="pipeline-layout">
        <SectionCard title="Run Pipeline A">
          <form className="stack" onSubmit={onRun}>
            <label className="input-label">Threads URL</label>
            <input className="text-input" placeholder="https://www.threads.com/@user/post/..." value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="primary-btn" type="submit">Run Analysis</button>
            {selectedJobId ? <div className="helper-text">Active job: {selectedJobId}</div> : null}
          </form>
        </SectionCard>

        <SectionCard title={hasActive ? "Current Run" : "Idle"}>
          {loading && hasActive && !selectedJob ? (
            <div className="skeleton-stack">
              <div className="skeleton-card" />
              <div className="skeleton-card" />
              <div className="skeleton-card" />
            </div>
          ) : hasActive && selectedJob ? (
            <div className="run-detail stack">
              <div className="processing-hero">
                <div className={`processing-orb ${selectedIsActive ? "live" : ""}`}>
                  <span className="processing-orb-core" />
                  <span className="processing-orb-ring ring-a" />
                  <span className="processing-orb-ring ring-b" />
                </div>
                <div className="processing-copy">
                  <div className={`processing-kicker ${selectedIsActive ? "live" : ""}`}>{String(selectedJob.status || "processing")}</div>
                  <div className="processing-title">System Processing</div>
                  <div className="processing-sub">{stageLine}</div>
                </div>
              </div>

              <div className="meta-row"><span>status</span><strong>{selectedJob.status}</strong></div>
              <div className="meta-row"><span>elapsed</span><strong className="metric-number-inline">{currentElapsed}</strong></div>
              <div className="meta-row"><span>processed</span><strong className="metric-number-inline">{selectedJob.processed_count}/{selectedJob.total_count}</strong></div>
              <div className="meta-row"><span>updated</span><strong>{fmtDate(selectedJob.updated_at)}</strong></div>
              <div className="run-actions">
                {canStopSelected ? (
                  <button
                    type="button"
                    className="small-btn danger"
                    disabled={stoppingJobId === selectedJob.id}
                    onClick={() => onStopJob(selectedJob.id)}
                  >
                    {stoppingJobId === selectedJob.id ? "Stopping..." : "Stop Task"}
                  </button>
                ) : null}
              </div>

              <div className="liquid-progress-shell">
                <div className="liquid-progress-head">
                  <strong className="metric-number-inline">{progressPct}% complete</strong>
                </div>
                <div className="liquid-vessel">
                  <div className={`liquid-fill ${selectedIsActive ? "anim" : ""}`} style={{ height: `${progressPct}%` }}>
                    <span className="liquid-wave wave-a" />
                    <span className="liquid-wave wave-b" />
                  </div>
                  <span className="liquid-vessel-gloss" />
                </div>
                <div className="pipeline-stage-rail" role="list" aria-label="pipeline stage rail">
                  {stageCards.map((stage) => (
                    <div
                      key={stage.key}
                      role="listitem"
                      className="pipeline-stage-node"
                      data-state={stage.state}
                      title={`${stage.from} -> ${stage.to}`}
                    >
                      <span className="pipeline-stage-dot" aria-hidden />
                      <span className="pipeline-stage-label">{stage.label}</span>
                    </div>
                  ))}
                </div>
              </div>
              {llmInFlight ? (
                <div className="llm-skeleton-panel" aria-label="LLM processing skeleton">
                  <div className="skeleton-line w-60 glass-pulse" />
                  <div className="skeleton-line w-90 glass-pulse delay-1" />
                  <div className="skeleton-line w-75 glass-pulse delay-2" />
                  <div className="skeleton-line w-45 glass-pulse delay-3" />
                </div>
              ) : null}
            </div>
          ) : (
            <div className="empty-note" data-testid="pipeline-idle-ready">
              Idle • Ready
              {lastCompleted ? ` · Last completed #${String(lastCompleted.id).slice(0, 8)} (${fmtDate(lastCompleted.updated_at)})` : ""}
            </div>
          )}
        </SectionCard>
      </div>

      <div className="pipeline-layout">
        <SectionCard title="Queued Runs">
          <div className="list-rows queued-list">
            {loading && !jobs.length ? (
              <div className="skeleton-stack">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={`pipeline-q-skeleton-${i}`} className="skeleton-card" />
                ))}
              </div>
            ) : null}
            {jobs.map((job) => (
              <div key={job.id} className={`row-item ${selectedJobId === job.id ? "selected" : ""}`}>
                <button
                  type="button"
                  className="row-main-btn"
                  onClick={() => {
                    setSelectedJobId(job.id);
                    localStorage.setItem("dl.activeRunId", job.id);
                  }}
                >
                  <div>
                    <div className="row-title">#{String(job.id).slice(0, 8)}</div>
                    <div className="row-sub metric-number-inline">processed {job.processed_count}/{job.total_count}</div>
                  </div>
                </button>
                <div className="row-tail">
                  <span className={`status-pill ${job.status}`}>{job.status}</span>
                  {!isTerminalStatus(job.status) ? (
                    <button
                      type="button"
                      className="small-btn danger"
                      disabled={stoppingJobId === job.id}
                      onClick={() => onStopJob(job.id)}
                    >
                      {stoppingJobId === job.id ? "Stopping..." : "Stop"}
                    </button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard
          title="Live Execution Log"
          action={
            <div className="log-actions">
              <button type="button" className="chip-btn" onClick={() => setAutoScroll((v) => !v)}>
                {autoScroll ? "Auto-scroll on" : "Auto-scroll off"}
              </button>
              <button type="button" className="chip-btn" onClick={() => navigator.clipboard.writeText(logLines.join("\n"))}>
                Copy
              </button>
              <button type="button" className="chip-btn" onClick={() => setLogOpen((v) => !v)}>
                {logOpen ? "Collapse" : "Expand"}
              </button>
            </div>
          }
        >
          {loading && !logLines.length ? (
            <div className="skeleton-stack">
              <div className="skeleton-card" />
              <div className="skeleton-card" />
            </div>
          ) : logOpen ? (
            <pre ref={logRef} className="log-panel">{logBody}</pre>
          ) : (
            <div className="empty-note">log collapsed</div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
