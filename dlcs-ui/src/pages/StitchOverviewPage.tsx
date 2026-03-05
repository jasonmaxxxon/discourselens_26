import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import templateHtml from "../stitch/enterprise_intelligence_command_center.html?raw";
import { StitchTemplateFrame, type StitchActionMeta, type StitchNotice } from "../components/StitchTemplateFrame";
import { api, formatApiError, isDegradedApiError } from "../lib/api";
import { isDebugUI } from "../lib/debug";
import type { JobStatus, OverviewTelemetryResponse, PhenomenonListItem, PostItem } from "../lib/types";

const actionMap = {
  "load older events": "overview_load_older",
  "open pipeline": "overview_open_pipeline",
  "current run": "overview_open_active_run",
  "open registry": "overview_open_registry",
  "jump to registry": "overview_open_registry",
  phenomena: "overview_open_registry",
  inspect: "overview_open_insights",
  "open investigate window": "overview_open_review",
};

function makeNotice(message: string, kind: "info" | "ok" | "error" = "info"): StitchNotice {
  return { message, kind, nonce: Date.now() + Math.floor(Math.random() * 1000) };
}

function activeRank(status: string | undefined): number {
  const s = String(status || "").toLowerCase();
  if (s === "processing" || s === "discovering") return 0;
  if (s === "queued" || s === "pending") return 1;
  if (s === "failed" || s === "stale") return 3;
  if (s === "canceled") return 4;
  return 2;
}

function pickActiveJob(jobs: JobStatus[]): JobStatus | null {
  if (!jobs.length) return null;
  return [...jobs].sort((a, b) => {
    const rank = activeRank(a.status) - activeRank(b.status);
    if (rank !== 0) return rank;
    return new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime();
  })[0] || null;
}

export function StitchOverviewPage() {
  const navigate = useNavigate();
  const debugMode = useMemo(() => isDebugUI(), []);
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [degradedJobs, setDegradedJobs] = useState(false);
  const [phenomena, setPhenomena] = useState<PhenomenonListItem[]>([]);
  const [degradedPhenomena, setDegradedPhenomena] = useState(false);
  const [latestPost, setLatestPost] = useState<PostItem | null>(null);
  const [telemetry, setTelemetry] = useState<OverviewTelemetryResponse | null>(null);
  const [degradedTelemetry, setDegradedTelemetry] = useState(false);
  const [notice, setNotice] = useState<StitchNotice | null>(null);

  const refreshMeta = useCallback(async () => {
    try {
      const [jobsRes, phenomenaRes, telemetryRes] = await Promise.all([
        api.listJobsMeta(),
        api.listPhenomena({ limit: 60 }).then((rows) => ({ data: rows, degraded: false })),
        api.getOverviewTelemetry("24h"),
      ]);
      setJobs(jobsRes.data || []);
      setDegradedJobs(Boolean(jobsRes.degraded));
      setPhenomena(phenomenaRes.data || []);
      setDegradedPhenomena(Boolean(phenomenaRes.degraded));
      setTelemetry(telemetryRes || null);
      const telemPending = String(telemetryRes?.status || "ready") !== "ready";
      setDegradedTelemetry(Boolean(telemetryRes?.meta?.degraded) || telemPending);
      if (telemPending) {
        const trace = telemetryRes?.trace_id ? ` · trace ${telemetryRes.trace_id}` : "";
        setNotice(makeNotice(`Overview telemetry ${String(telemetryRes?.status || "pending")}${trace}`, "info"));
      }
    } catch (e) {
      if (isDegradedApiError(e)) {
        setDegradedJobs(true);
        setDegradedPhenomena(true);
        setDegradedTelemetry(true);
      }
      setNotice(makeNotice(`Overview sync failed: ${formatApiError(e)}`, "error"));
    }
  }, []);

  const refreshPost = useCallback(async () => {
    try {
      const postsMeta = await api.getPostsMeta();
      const posts = postsMeta.data || [];
      setLatestPost(posts[0] || null);
      if (postsMeta.degraded) {
        setNotice(makeNotice(`Posts feed pending${postsMeta.requestId ? ` · trace ${postsMeta.requestId}` : ""}`, "info"));
      }
    } catch (e) {
      setNotice(makeNotice(`Overview posts failed: ${formatApiError(e)}`, "error"));
      setLatestPost(null);
    }
  }, []);

  useEffect(() => {
    void refreshMeta();
    void refreshPost();
    const timer = window.setInterval(() => {
      void refreshMeta();
      void refreshPost();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [refreshMeta, refreshPost]);

  const onAction = useCallback(
    async (action: string, meta: StitchActionMeta) => {
      if (action === "overview_load_older") {
        setNotice(makeNotice("Timeline Drift / Comment Momentum 正在等待資料模型接線。", "info"));
        return;
      }
      if (action === "overview_open_pipeline") {
        navigate("/pipeline");
        return;
      }
      if (action === "overview_open_active_run") {
        const fromMeta = String(meta.jobId || "").trim();
        const stored = typeof window !== "undefined" ? String(window.localStorage.getItem("dl.activeRunId") || "").trim() : "";
        const targetId = fromMeta || stored;
        navigate(targetId ? `/pipeline?job_id=${encodeURIComponent(targetId)}` : "/pipeline");
        return;
      }
      if (action === "overview_open_registry") {
        navigate("/library");
        return;
      }
      if (action === "overview_open_insights") {
        navigate("/insights");
        return;
      }
      if (action === "overview_open_review") {
        navigate("/review");
      }
    },
    [navigate]
  );

  const active = useMemo(() => {
    const preferredJobId = String(telemetry?.active_context?.job_id || "").trim();
    if (preferredJobId) {
      const found = jobs.find((row) => row.id === preferredJobId);
      if (found) return found;
    }
    if (typeof window !== "undefined") {
      const pinned = String(window.localStorage.getItem("dl.activeRunId") || "").trim();
      if (pinned) {
        const found = jobs.find((row) => row.id === pinned);
        if (found) return found;
      }
    }
    return pickActiveJob(jobs);
  }, [jobs, telemetry?.active_context?.job_id]);

  useEffect(() => {
    if (!active?.id) return;
    if (typeof window === "undefined") return;
    window.localStorage.setItem("dl.activeRunId", String(active.id));
  }, [active?.id]);
  const runningCount = useMemo(
    () => jobs.filter((j) => ["processing", "discovering"].includes(String(j.status || "").toLowerCase())).length,
    [jobs]
  );
  const queuedCount = useMemo(
    () => jobs.filter((j) => ["queued", "pending"].includes(String(j.status || "").toLowerCase())).length,
    [jobs]
  );
  const failedCount = useMemo(
    () => jobs.filter((j) => ["failed", "stale"].includes(String(j.status || "").toLowerCase())).length,
    [jobs]
  );
  const progressPct = useMemo(() => {
    if (!active) return 0;
    const total = Number(active.total_count || 0);
    const processed = Number(active.processed_count || 0);
    if (total <= 0) return 0;
    return Math.max(0, Math.min(100, (processed / total) * 100));
  }, [active]);

  const timeline: number[] = [];
  const events: Array<{ user: string; message: string; time: string; state: string }> = [];

  const contextPhenomenon = useMemo(() => {
    return (
      [...phenomena]
        .sort((a, b) => Number(b.total_posts || 0) - Number(a.total_posts || 0))
        .find((row) => row.id) || null
    );
  }, [phenomena]);

  const bridgeData = useMemo(
    () => ({
      page: "overview",
      activeCount: runningCount,
      queuedCount,
      failedCount,
      degradedCount: degradedJobs || degradedPhenomena || degradedTelemetry ? 1 : 0,
      currentRunId: active?.id || "",
      currentStatus: active?.status || "idle",
      progressPct,
      timeline,
      events,
      timelineState: "mock_pending_wiring",
      momentumState: "mock_pending_wiring",
      contextState: "mock_pending_wiring",
      context: {
        phenomenonId: "PH-XXXX",
        stability: "TBD",
        isoTimestamp: "TBD",
        latency: "TBD",
        load: "TBD",
        uptime: "TBD",
        threads: "TBD",
      },
      debugMode,
    }),
    [
      active,
      contextPhenomenon?.id,
      contextPhenomenon?.last_seen_at,
      contextPhenomenon?.status,
      degradedJobs,
      degradedPhenomena,
      degradedTelemetry,
      events,
      latestPost?.created_at,
      phenomena.length,
      progressPct,
      queuedCount,
      runningCount,
      failedCount,
      telemetry?.active_context?.phenomenon_id,
      telemetry?.meta?.generated_at,
      timeline,
      debugMode,
    ]
  );

  return (
    <StitchTemplateFrame
      html={templateHtml}
      title="Overview Command Center"
      pageId="overview"
      actionMap={actionMap}
      bridgeData={bridgeData}
      onAction={onAction}
      notice={notice}
      hideTemplateHeader
    />
  );
}
