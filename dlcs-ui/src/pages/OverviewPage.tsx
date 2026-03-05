import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useShallow } from "zustand/react/shallow";
import { api, isDegradedApiError } from "../lib/api";
import type { CasebookItem, CommentItem, EvidenceItem, JobItem, JobStatus, PhenomenonListItem } from "../lib/types";
import { fmtDate, fmtNumber } from "../lib/format";
import { CommentMomentumPanel } from "../components/CommentMomentumPanel";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { SectionCard } from "../components/SectionCard";
import { TimelineDriftPanel } from "../components/TimelineDriftPanel";
import { readRouteSnapshot, writeRouteSnapshot } from "../lib/routeCache";
import { useIntelligenceStore } from "../store/intelligenceStore";

type Stage = "ingest" | "quant" | "healthgate" | "llm";
type StageState = "inactive" | "running" | "complete" | "failed";

type OverviewSnapshot = {
  phenomena: PhenomenonListItem[];
};

const CACHE_KEY = "dl.cache.route.overview.v3";
const STAGES: Array<{ key: Stage; label: string }> = [
  { key: "ingest", label: "Ingest" },
  { key: "quant", label: "Quant" },
  { key: "healthgate", label: "HealthGate" },
  { key: "llm", label: "LLM" },
];

function cachedPostId(): string {
  try {
    const raw = localStorage.getItem("dl.cache.posts");
    if (!raw) return "";
    const parsed = JSON.parse(raw) as Array<{ id?: string }>;
    return String(parsed?.[0]?.id || "");
  } catch {
    return "";
  }
}

function normalizeStage(rawStage: string): Stage {
  const s = String(rawStage || "").toLowerCase();
  if (/(fetch|ingest|discover|init)/.test(s)) return "ingest";
  if (/(quant|preanalysis|pre-analysis|cluster|embed|analyst)/.test(s)) return "quant";
  if (/(health|integrity|audit|risk|gate|cip)/.test(s)) return "healthgate";
  if (/(llm|claim|narrative|store)/.test(s)) return "llm";
  return "ingest";
}

function currentStage(items: JobItem[] | undefined, status: string): Stage {
  const normalizedStatus = String(status || "").toLowerCase();
  if (normalizedStatus === "completed") return "llm";
  if (normalizedStatus === "queued" || normalizedStatus === "pending" || normalizedStatus === "discovering") return "ingest";
  const rows = [...(items || [])].sort(
    (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
  );
  const running = rows.find((it) => ["processing", "discovering", "running"].includes(String(it.status || "").toLowerCase()));
  if (running) return normalizeStage(running.stage);
  if (rows[0]?.stage) return normalizeStage(rows[0].stage);
  if (normalizedStatus === "failed" || normalizedStatus === "stale" || normalizedStatus === "canceled") return "healthgate";
  return "quant";
}

function stageRail(detail: JobStatus | null): Array<{ key: Stage; label: string; state: StageState }> {
  if (!detail) return STAGES.map((s) => ({ ...s, state: "inactive" }));
  const status = String(detail.status || "").toLowerCase();
  const current = currentStage(detail.items, detail.status);
  const currentIndex = STAGES.findIndex((s) => s.key === current);
  const isFailed = status === "failed" || status === "canceled" || status === "stale";
  const isCompleted = status === "completed";
  return STAGES.map((stage, idx) => {
    if (isCompleted) return { ...stage, state: "complete" };
    if (isFailed) {
      if (idx < currentIndex) return { ...stage, state: "complete" };
      if (idx === currentIndex) return { ...stage, state: "failed" };
      return { ...stage, state: "inactive" };
    }
    if (idx < currentIndex) return { ...stage, state: "complete" };
    if (idx === currentIndex) return { ...stage, state: "running" };
    return { ...stage, state: "inactive" };
  });
}

export function OverviewPage() {
  const navigate = useNavigate();
  const initial = readRouteSnapshot<OverviewSnapshot>(CACHE_KEY);
  const { jobs, lastRun, telemetryDegraded, currentPost } = useIntelligenceStore(
    useShallow((s) => ({
      jobs: s.jobs,
      lastRun: s.lastRun,
      telemetryDegraded: s.telemetryDegraded,
      currentPost: s.currentPost,
    }))
  );

  const [phenomena, setPhenomena] = useState<PhenomenonListItem[]>(initial?.data.phenomena || []);
  const [detectPostId, setDetectPostId] = useState("");
  const [detectComments, setDetectComments] = useState<CommentItem[]>([]);
  const [detectEvidence, setDetectEvidence] = useState<EvidenceItem[]>([]);
  const [detectCasebook, setDetectCasebook] = useState<CasebookItem[]>([]);
  const [detectLoading, setDetectLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [phenomenaDegraded, setPhenomenaDegraded] = useState(false);
  const [detectDegraded, setDetectDegraded] = useState(false);
  const [syncing, setSyncing] = useState(Boolean(initial));
  const [loading, setLoading] = useState(!initial);

  useEffect(() => {
    let alive = true;
    let timer: number | null = null;

    const pollDelayMs = () => (document.visibilityState === "hidden" ? 20000 : 6000);
    const schedule = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(load, pollDelayMs());
    };

    const load = async () => {
      try {
        const nextPhenomena = await api.listPhenomena({ limit: 40 });
        if (!alive) return;
        setPhenomena(nextPhenomena);
        writeRouteSnapshot<OverviewSnapshot>(CACHE_KEY, { phenomena: nextPhenomena });
        setPhenomenaDegraded(false);
        setError("");
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? e.message : String(e));
        if (isDegradedApiError(e)) setPhenomenaDegraded(true);
      } finally {
        if (!alive) return;
        setLoading(false);
        setSyncing(false);
        schedule();
      }
    };

    load();
    const onVisibility = () => {
      if (!alive) return;
      if (timer) window.clearTimeout(timer);
      load();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const loadDetectPost = async () => {
      if (currentPost?.id) {
        setDetectPostId(currentPost.id);
        return;
      }
      const cached = cachedPostId();
      if (cached) {
        setDetectPostId(cached);
        return;
      }
      try {
        const posts = await api.getPosts();
        if (!alive) return;
        setDetectPostId(posts[0]?.id || "");
      } catch {
        if (!alive) return;
        setDetectPostId("");
      }
    };
    loadDetectPost();
    return () => {
      alive = false;
    };
  }, [currentPost?.id]);

  useEffect(() => {
    let alive = true;
    if (!detectPostId) {
      setDetectComments([]);
      setDetectEvidence([]);
      setDetectCasebook([]);
      setDetectLoading(false);
      return () => {
        alive = false;
      };
    }
    setDetectLoading(true);
    Promise.allSettled([api.getCommentsByPost(detectPostId, { limit: 300, sort: "time" }), api.getEvidence(detectPostId)])
      .then(([commentsRes, evidenceRes]) => {
        if (!alive) return;
        if (commentsRes.status === "fulfilled") setDetectComments(commentsRes.value.items || []);
        else setDetectComments([]);
        if (evidenceRes.status === "fulfilled") setDetectEvidence(evidenceRes.value.items || []);
        else setDetectEvidence([]);

        const degradedDetected =
          (commentsRes.status === "rejected" && isDegradedApiError(commentsRes.reason)) ||
          (evidenceRes.status === "rejected" && isDegradedApiError(evidenceRes.reason));
        setDetectDegraded(degradedDetected);
      })
      .finally(() => {
        if (!alive) return;
        setDetectLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [detectPostId]);

  useEffect(() => {
    let alive = true;
    if (!detectPostId) {
      setDetectCasebook([]);
      return () => {
        alive = false;
      };
    }
    api
      .listCasebook({ post_id: detectPostId, limit: 500 })
      .then((res) => {
        if (!alive) return;
        setDetectCasebook(res.items || []);
      })
      .catch(() => {
        if (!alive) return;
        setDetectCasebook([]);
      });
    return () => {
      alive = false;
    };
  }, [detectPostId]);

  const runningJobs = jobs.filter((j) => {
    const s = String(j.status || "").toLowerCase();
    return s === "processing" || s === "discovering";
  });
  const queuedJobs = jobs.filter((j) => {
    const s = String(j.status || "").toLowerCase();
    return s === "queued" || s === "pending";
  });
  const failedJobs = jobs.filter((j) => {
    const s = String(j.status || "").toLowerCase();
    return s === "failed" || s === "stale";
  });

  const activeJob = useMemo(() => {
    if (!jobs.length) return null;
    if (lastRun?.id) {
      const selected = jobs.find((j) => j.id === lastRun.id);
      if (selected) return selected;
    }
    return (
      jobs.find((j) => {
        const s = String(j.status || "").toLowerCase();
        return s === "processing" || s === "discovering" || s === "queued" || s === "pending";
      }) || jobs[0]
    );
  }, [jobs, lastRun?.id]);

  const rail = useMemo(() => stageRail(activeJob), [activeJob]);
  const topPhenomena = useMemo(
    () =>
      [...phenomena]
        .sort((a, b) => Number(b.total_posts || 0) - Number(a.total_posts || 0))
        .slice(0, 3),
    [phenomena]
  );
  const registryPulse = useMemo(
    () =>
      [...phenomena]
        .sort((a, b) => new Date(b.last_seen_at || 0).getTime() - new Date(a.last_seen_at || 0).getTime())
        .slice(0, 6),
    [phenomena]
  );
  const lastUpdated = lastRun?.updatedAt || jobs[0]?.updated_at || null;
  const degraded = telemetryDegraded || phenomenaDegraded;
  const detectPanelDegraded = degraded || detectDegraded;
  const skeletonOnly = loading && !jobs.length && !phenomena.length;
  const casebookAnnotations = useMemo(() => {
    const byBucket: Record<string, { count: number; lastAnnotatedAt: string | null; noteSnippet?: string | null }> = {};
    for (const item of detectCasebook) {
      if (!item?.bucket?.t0 || !item?.bucket?.t1) continue;
      const key = `${item.bucket.t0}|${item.bucket.t1}`;
      const lastAnnotated = item.created_at || item.captured_at || null;
      const noteSnippet = item.analyst_note ? item.analyst_note.slice(0, 60) : null;
      if (!byBucket[key]) {
        byBucket[key] = { count: 1, lastAnnotatedAt: lastAnnotated, noteSnippet };
        continue;
      }
      byBucket[key].count += 1;
      const prevTs = new Date(String(byBucket[key].lastAnnotatedAt || 0)).getTime();
      const nextTs = new Date(String(lastAnnotated || 0)).getTime();
      if (Number.isFinite(nextTs) && (!Number.isFinite(prevTs) || nextTs > prevTs)) {
        byBucket[key].lastAnnotatedAt = lastAnnotated;
        byBucket[key].noteSnippet = noteSnippet;
      }
    }
    return byBucket;
  }, [detectCasebook]);

  return (
    <div className="page-grid">
      <PageHeader
        title="Ops Dashboard"
        subtitle="Live system pulse for current runs, queue pressure, and phenomenon registry."
        actions={
          <>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/pipeline")}>
              Open Pipeline
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/library")}>
              Open Registry
            </button>
            <span className="chip">Phenomena {fmtNumber(phenomena.length)}</span>
          </>
        }
      />

      {degraded ? <div className="degraded-banner">資料通道暫時降級，已顯示最近快照；你仍可繼續操作。</div> : null}
      {syncing ? <div className="sync-banner">同步最新資料中…</div> : null}
      {error ? <div className="error-banner">{error}</div> : null}

      {skeletonOnly ? (
        <>
          <div className="metrics-grid four compact-metrics">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={`m-sk-${i}`} className="skeleton-card" />
            ))}
          </div>
          <SectionCard title="System Pulse">
            <div className="skeleton-stack">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={`pulse-sk-${i}`} className="skeleton-card" />
              ))}
            </div>
          </SectionCard>
        </>
      ) : (
        <>
          <div className="metrics-grid four compact-metrics">
            <MetricCard label="Active Jobs" value={runningJobs.length} hint="processing / discovering" />
            <MetricCard label="Queued" value={queuedJobs.length} hint="pending execution" />
            <MetricCard label="Failed + Stale" value={failedJobs.length} hint="operator check" />
            <MetricCard
              label="Last Refresh"
              value={lastUpdated ? fmtDate(lastUpdated).slice(11) : "-"}
              hint={lastUpdated ? fmtDate(lastUpdated) : "no data"}
            />
          </div>

          <SectionCard title="System Pulse">
            <div className="pulse-shell">
              <div className="pulse-kpis">
                <div className="pulse-kpi-row"><span>Active</span><strong className="metric-number-inline">{runningJobs.length}</strong></div>
                <div className="pulse-kpi-row"><span>Queued</span><strong className="metric-number-inline">{queuedJobs.length}</strong></div>
                <div className="pulse-kpi-row"><span>Failed</span><strong className="metric-number-inline">{failedJobs.length}</strong></div>
                <div className="pulse-kpi-row"><span>Degraded</span><strong>{degraded ? "yes" : "no"}</strong></div>
              </div>
              <div className="pulse-rail">
                <div className="pulse-rail-track">
                  {rail.map((stage) => (
                    <div key={stage.key} className={`pulse-stage ${stage.state}`}>
                      <span>{stage.label}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </SectionCard>

          <SectionCard
            title="Detect"
            action={
              detectPostId ? (
                <button
                  type="button"
                  className="chip-btn motion-btn"
                  onClick={() => navigate(`/library?post_id=${encodeURIComponent(detectPostId)}`)}
                >
                  Open Investigate Window
                </button>
              ) : null
            }
          >
            <div className="detect-strip">
              <TimelineDriftPanel
                comments={detectComments}
                evidence={detectEvidence}
                loading={detectLoading}
                degraded={detectPanelDegraded}
                annotations={casebookAnnotations}
                onBucketClick={({ t0, t1, cluster_key, casebook_only }) => {
                  const effectivePostId = detectPostId || currentPost?.id || cachedPostId();
                  if (!effectivePostId) return;
                  const query = new URLSearchParams();
                  query.set("post_id", effectivePostId);
                  query.set("t0", t0);
                  query.set("t1", t1);
                  if (typeof cluster_key === "number") query.set("cluster_key", String(cluster_key));
                  if (casebook_only) query.set("casebookOnly", "1");
                  navigate(`/library?${query.toString()}`);
                }}
              />
              <CommentMomentumPanel comments={detectComments} loading={detectLoading} degraded={detectPanelDegraded} />
            </div>
          </SectionCard>

          <div className="split two">
            <SectionCard title="Recent Intelligence">
              <div className="list-rows">
                {topPhenomena.map((p) => (
                  <article key={p.id} className="row-item">
                    <div>
                      <div className="row-title">{p.canonical_name || "Unnamed phenomenon"}</div>
                      <div className="row-sub">
                        posts <span className="metric-number-inline">{fmtNumber(p.total_posts || 0)}</span> · last seen {fmtDate(p.last_seen_at || null)}
                      </div>
                    </div>
                    <span className={`status-pill ${String(p.status || "unknown").toLowerCase()}`}>{p.status || "unknown"}</span>
                  </article>
                ))}
                {!topPhenomena.length ? <div className="empty-note">No phenomenon data yet.</div> : null}
              </div>
            </SectionCard>

            <SectionCard title="Registry Pulse">
              <div className="list-rows">
                {registryPulse.map((p) => (
                  <article key={`registry-${p.id}`} className="row-item">
                    <div>
                      <div className="row-title">{p.canonical_name || p.id}</div>
                      <div className="row-sub">occurrence <span className="metric-number-inline">{fmtNumber(p.total_posts || 0)}</span></div>
                    </div>
                    <div className="row-sub">{fmtDate(p.last_seen_at || null)}</div>
                  </article>
                ))}
                {!registryPulse.length ? <div className="empty-note">Registry has no tracked phenomena yet.</div> : null}
              </div>
            </SectionCard>
          </div>
        </>
      )}
    </div>
  );
}
