import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, isDegradedApiError } from "../lib/api";
import type { AnalysisJsonResponse, ClaimItem, ClusterItem, CommentItem, EvidenceItem, PostItem } from "../lib/types";
import { compactErrorMessage, extractExecutiveSummary, fmtNumber, fmtPct, normalizeForDedupe } from "../lib/format";
import { dedupeEvidenceWithStats, evidenceStableKey, selectEvidencePreview, selectUniqueEvidenceForCluster } from "../lib/evidencePreview";
import { extractFocusTerms } from "../lib/focusTerms";
import { buildTopDeltaWindows } from "../lib/compareDeltaWindows";
import { CompareBoard } from "../components/CompareBoard";
import { PageHeader } from "../components/PageHeader";
import { SectionCard } from "../components/SectionCard";
import { ClusterBubbleChart } from "../components/ClusterBubbleChart";
import { PostCard } from "../components/PostCard";
import { PostPickerDrawer } from "../components/insights/PostPickerDrawer";
import { readRouteSnapshot, writeRouteSnapshot } from "../lib/routeCache";
import { useIntelligenceStore } from "../store/intelligenceStore";

type AnalysisMeta = Record<string, unknown>;

type InsightsSnapshot = {
  posts: PostItem[];
  selectedPostId: string;
  analysis: AnalysisJsonResponse | null;
  clusters: ClusterItem[];
  claims: ClaimItem[];
  evidence: EvidenceItem[];
  selectedClusterKey?: number;
};

const CACHE_KEY = "dl.cache.route.insights.v1";
const MIN_COMPARE_COMMENTS = 30;
const MIN_COMPARE_EVIDENCE = 8;

function getMeta(analysis: AnalysisJsonResponse | null): AnalysisMeta {
  const root = (analysis?.analysis_json || {}) as Record<string, unknown>;
  return ((root.meta || {}) as Record<string, unknown>) || {};
}

function pickRiskText(a: AnalysisJsonResponse | null): string {
  const meta = getMeta(a);
  const risk = (meta.risk || {}) as Record<string, unknown>;
  const level = risk.level || risk.risk_level || meta.risk_level;
  if (typeof level === "string" && level.trim()) return level;
  return "-";
}

function pickCoverage(a: AnalysisJsonResponse | null): string {
  const meta = getMeta(a);
  const coverage = (meta.coverage || {}) as Record<string, unknown>;
  const ratio = coverage.coverage_ratio || coverage.ratio;
  if (typeof ratio === "number") return fmtPct(ratio);
  return "-";
}

function pickBehaviorFlags(a: AnalysisJsonResponse | null): string[] {
  const meta = getMeta(a);
  const behavior = (meta.behavior || {}) as Record<string, unknown>;
  const rawFlags = behavior.flags;
  if (Array.isArray(rawFlags)) {
    return rawFlags.map((f) => String(f)).filter(Boolean).slice(0, 6);
  }
  if (rawFlags && typeof rawFlags === "object") {
    const obj = rawFlags as Record<string, unknown>;
    return Object.keys(obj)
      .filter((k) => Boolean(obj[k]))
      .slice(0, 6);
  }
  return [];
}

function safeText(input: unknown): string {
  const text = String(input || "").trim();
  return text || "-";
}

function riskPercent(level: string): number {
  const x = level.toLowerCase();
  if (x.includes("critical") || x.includes("severe") || x.includes("高")) return 84;
  if (x.includes("medium") || x.includes("mid") || x.includes("中")) return 58;
  if (x.includes("low") || x.includes("低")) return 26;
  return 42;
}

function firstHook(text: string): string {
  const cleaned = text.replace(/\s+/g, " ").trim();
  if (!cleaned) return "This narrative thread is still being synthesized from available forensic evidence.";
  const m = cleaned.match(/^(.{24,160}?[。.!?！？])/u);
  if (m?.[1]) return m[1];
  return cleaned.length > 160 ? `${cleaned.slice(0, 157)}...` : cleaned;
}

function pickPhenomenon(analysis: AnalysisJsonResponse | null): { id?: string; name: string; status?: string } | null {
  const direct = (analysis as unknown as { phenomenon?: Record<string, unknown> })?.phenomenon;
  const fromMeta = ((analysis?.analysis_json || {}) as Record<string, unknown>)?.meta as Record<string, unknown> | undefined;
  const candidate = (direct || (fromMeta?.phenomenon as Record<string, unknown> | undefined)) as Record<string, unknown> | undefined;
  if (!candidate) return null;
  const name = String(candidate.canonical_name || candidate.name || candidate.label || "").trim();
  if (!name) return null;
  return {
    id: candidate.id ? String(candidate.id) : undefined,
    name,
    status: candidate.status ? String(candidate.status) : undefined,
  };
}

export function InsightsPage() {
  const navigate = useNavigate();
  const setCurrentPost = useIntelligenceStore((s) => s.setCurrentPost);
  const setPhenomenon = useIntelligenceStore((s) => s.setPhenomenon);
  const setRiskLevel = useIntelligenceStore((s) => s.setRiskLevel);
  const setStabilityVerdict = useIntelligenceStore((s) => s.setStabilityVerdict);
  const initial = readRouteSnapshot<InsightsSnapshot>(CACHE_KEY);
  const [posts, setPosts] = useState<PostItem[]>(initial?.data.posts || []);
  const [selectedPostId, setSelectedPostId] = useState<string>(initial?.data.selectedPostId || "");
  const [analysis, setAnalysis] = useState<AnalysisJsonResponse | null>(initial?.data.analysis || null);
  const [clusters, setClusters] = useState<ClusterItem[]>(initial?.data.clusters || []);
  const [claims, setClaims] = useState<ClaimItem[]>(initial?.data.claims || []);
  const [evidence, setEvidence] = useState<EvidenceItem[]>(initial?.data.evidence || []);
  const [selectedClusterKey, setSelectedClusterKey] = useState<number | undefined>(initial?.data.selectedClusterKey);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [rerunPending, setRerunPending] = useState(false);
  const [rerunMessage, setRerunMessage] = useState("");
  const [error, setError] = useState("");
  const [degraded, setDegraded] = useState(false);
  const [syncing, setSyncing] = useState(Boolean(initial));
  const [loadingPosts, setLoadingPosts] = useState(!(initial?.data.posts?.length));
  const [loadingData, setLoadingData] = useState(!(initial?.data.analysis || initial?.data.clusters?.length || initial?.data.evidence?.length));
  const [baseTotalComments, setBaseTotalComments] = useState(0);
  const [baseComments, setBaseComments] = useState<CommentItem[]>([]);
  const [compareMode, setCompareMode] = useState(false);
  const [comparePostId, setComparePostId] = useState("");
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState("");
  const [compareClusters, setCompareClusters] = useState<ClusterItem[]>([]);
  const [compareClaims, setCompareClaims] = useState<ClaimItem[]>([]);
  const [compareEvidence, setCompareEvidence] = useState<EvidenceItem[]>([]);
  const [compareTotalComments, setCompareTotalComments] = useState(0);
  const [compareComments, setCompareComments] = useState<CommentItem[]>([]);
  const [baseAuditCounts, setBaseAuditCounts] = useState<{ kept: number; dropped: number }>({ kept: 0, dropped: 0 });
  const [compareAuditCounts, setCompareAuditCounts] = useState<{ kept: number; dropped: number }>({ kept: 0, dropped: 0 });

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPickerOpen(true);
      }
      if (e.key === "Escape") {
        setPickerOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoadingPosts(!(posts.length > 0));
    setSyncing(true);
    api
      .getPosts()
      .then((rows) => {
        if (!alive) return;
        setPosts(rows);
        localStorage.setItem("dl.cache.posts", JSON.stringify(rows));
        const nextPostId = selectedPostId || initial?.data.selectedPostId || rows[0]?.id || "";
        if (nextPostId) setSelectedPostId(nextPostId);
        writeRouteSnapshot<InsightsSnapshot>(CACHE_KEY, {
          posts: rows,
          selectedPostId: nextPostId,
          analysis,
          clusters,
          claims,
          evidence,
          selectedClusterKey,
        });
        setDegraded(false);
      })
      .catch((e) => {
        if (!alive) return;
        try {
          const cached = JSON.parse(localStorage.getItem("dl.cache.posts") || "[]") as PostItem[];
          if (cached.length) {
            setPosts(cached);
            if (!selectedPostId && cached[0]?.id) setSelectedPostId(cached[0].id);
            setError("即時資料暫不可用，已使用快取。");
            return;
          }
        } catch {
          // ignore
        }
        setError(compactErrorMessage(e instanceof Error ? e.message : String(e)));
        if (isDegradedApiError(e)) setDegraded(true);
      })
      .finally(() => {
        if (!alive) return;
        setLoadingPosts(false);
        setSyncing(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedPostId) return;
    let alive = true;
    setError("");
    setRerunMessage("");
    setLoadingData(!(analysis || clusters.length > 0 || evidence.length > 0));
    setSyncing(true);

    Promise.allSettled([
      api.getAnalysisJson(selectedPostId),
      api.getClusters(selectedPostId),
      api.getClaims(selectedPostId),
      api.getEvidence(selectedPostId),
      api.getCommentsByPost(selectedPostId, { limit: 300, sort: "time" }),
    ]).then(([analysisRes, clusterRes, claimsRes, evidenceRes, commentsRes]) => {
      if (!alive) return;
      const nextAnalysis = analysisRes.status === "fulfilled" ? analysisRes.value : null;
      const nextClusters = clusterRes.status === "fulfilled" ? clusterRes.value.clusters || [] : [];
      const nextTotalComments =
        commentsRes.status === "fulfilled"
          ? Number(commentsRes.value.total || commentsRes.value.items?.length || 0)
          : clusterRes.status === "fulfilled"
            ? clusterRes.value.total_comments || 0
            : 0;
      const nextClaims = claimsRes.status === "fulfilled" ? claimsRes.value.claims || [] : [];
      const nextAudit =
        claimsRes.status === "fulfilled"
          ? {
              kept: Number(claimsRes.value.audit?.kept_claims_count || 0),
              dropped: Number(claimsRes.value.audit?.dropped_claims_count || 0),
            }
          : { kept: 0, dropped: 0 };
      const nextEvidence = evidenceRes.status === "fulfilled" ? evidenceRes.value.items || [] : [];
      const nextComments = commentsRes.status === "fulfilled" ? commentsRes.value.items || [] : [];
      setAnalysis(nextAnalysis);
      setClusters(nextClusters);
      setBaseTotalComments(nextTotalComments);
      setBaseComments(nextComments);
      setClaims(nextClaims);
      setBaseAuditCounts(nextAudit);
      setEvidence(nextEvidence);
      setSelectedClusterKey(nextClusters[0]?.cluster_key);
      writeRouteSnapshot<InsightsSnapshot>(CACHE_KEY, {
        posts,
        selectedPostId,
        analysis: nextAnalysis,
        clusters: nextClusters,
        claims: nextClaims,
        evidence: nextEvidence,
        selectedClusterKey: nextClusters[0]?.cluster_key,
      });
      const degradedDetected = [analysisRes, clusterRes, claimsRes, evidenceRes, commentsRes].some(
        (res) => res.status === "rejected" && isDegradedApiError(res.reason)
      );
      if (degradedDetected) {
        setDegraded(true);
        setError("部分資料來源暫時不可用，已顯示可用內容。");
      } else {
        setDegraded(false);
      }
      setLoadingData(false);
      setSyncing(false);
    });

    return () => {
      alive = false;
    };
  }, [selectedPostId]);

  useEffect(() => {
    if (!compareMode || !comparePostId || comparePostId === selectedPostId) {
      setCompareClusters([]);
      setCompareClaims([]);
      setCompareEvidence([]);
      setCompareComments([]);
      setCompareTotalComments(0);
      setCompareAuditCounts({ kept: 0, dropped: 0 });
      setCompareError("");
      return;
    }
    let alive = true;
    setCompareLoading(true);
    setCompareError("");
    Promise.all([
      api.getClusters(comparePostId),
      api.getClaims(comparePostId),
      api.getEvidence(comparePostId),
      api.getCommentsByPost(comparePostId, { limit: 300, sort: "time" }),
    ])
      .then(([clusterRes, claimsRes, evidenceRes, commentsRes]) => {
        if (!alive) return;
        setCompareClusters(clusterRes.clusters || []);
        setCompareClaims(claimsRes.claims || []);
        setCompareEvidence(evidenceRes.items || []);
        setCompareComments(commentsRes.items || []);
        setCompareTotalComments(Number(commentsRes.total || commentsRes.items?.length || clusterRes.total_comments || 0));
        setCompareAuditCounts({
          kept: Number(claimsRes.audit?.kept_claims_count || 0),
          dropped: Number(claimsRes.audit?.dropped_claims_count || 0),
        });
      })
      .catch((err) => {
        if (!alive) return;
        setCompareError(compactErrorMessage(err instanceof Error ? err.message : String(err)));
      })
      .finally(() => {
        if (!alive) return;
        setCompareLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [compareMode, comparePostId, selectedPostId]);

  useEffect(() => {
    setSummaryExpanded(false);
  }, [selectedPostId, selectedClusterKey]);

  const selectedPost = posts.find((p) => p.id === selectedPostId);
  const canonicalClusterKey = Number.isFinite(Number(selectedClusterKey)) ? Number(selectedClusterKey) : undefined;
  const selectedCluster = clusters.find((c) => c.cluster_key === canonicalClusterKey) || clusters[0];
  const integrityLabel = analysis?.analysis_is_valid ? "pass" : "partial";
  const executiveSummary = analysis ? extractExecutiveSummary(analysis.analysis_json) : "暫無中文摘要（可重跑分析）";
  const coverageText = pickCoverage(analysis);
  const riskLevel = pickRiskText(analysis);
  const riskScore = riskPercent(riskLevel);
  const summaryHook = firstHook(executiveSummary);
  const summaryPreview = summaryExpanded || executiveSummary.length <= 180 ? executiveSummary : `${executiveSummary.slice(0, 180)}...`;
  const behaviorFlags = pickBehaviorFlags(analysis);

  const uniqueEvidenceForCluster = useMemo(
    () => selectUniqueEvidenceForCluster(evidence, selectedCluster?.cluster_key),
    [evidence, selectedCluster?.cluster_key]
  );

  const clusterClaims = useMemo(() => {
    if (!selectedCluster) return [];
    const seen = new Set<string>();
    const out: ClaimItem[] = [];
    for (const c of claims) {
      const ck = c.primary_cluster_key ?? c.cluster_key;
      if (ck !== selectedCluster.cluster_key) continue;
      const key = normalizeForDedupe(c.text || "");
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(c);
      if (out.length >= 5) break;
    }
    return out;
  }, [claims, selectedCluster]);

  const evidencePreview = useMemo(
    () => selectEvidencePreview(evidence, selectedCluster?.cluster_key),
    [evidence, selectedCluster?.cluster_key]
  );
  const timelineEvidenceWithStats = useMemo(() => dedupeEvidenceWithStats(evidencePreview), [evidencePreview]);
  const timelineEvidence = timelineEvidenceWithStats.items;
  const duplicatesRemoved = timelineEvidenceWithStats.removed;
  const focusTerms = useMemo(
    () =>
      extractFocusTerms(
        timelineEvidence.map((item) => ({
          text: String(item.text || ""),
          created_at: item.created_at || null,
        })),
        5
      ),
    [timelineEvidence]
  );
  const focusChips = focusTerms.length ? focusTerms : (selectedCluster?.keywords || []).filter((kw) => String(kw || "").trim().length >= 2).slice(0, 5);

  const clusterEngagement = (selectedCluster?.engagement?.likes || 0) + (selectedCluster?.engagement?.replies || 0);
  const canRerun = Boolean(selectedPost?.url);
  const baselineTopShare = Math.max(...clusters.map((cluster) => Number(cluster.share || 0)), 0);
  const compareTopShare = Math.max(...compareClusters.map((cluster) => Number(cluster.share || 0)), 0);
  const baselineEngagement = clusters.reduce(
    (sum, cluster) => sum + Number(cluster.engagement?.likes || 0) + Number(cluster.engagement?.replies || 0),
    0
  );
  const compareEngagement = compareClusters.reduce(
    (sum, cluster) => sum + Number(cluster.engagement?.likes || 0) + Number(cluster.engagement?.replies || 0),
    0
  );
  const baselineKeptRate = (() => {
    const kept = baseAuditCounts.kept;
    const dropped = baseAuditCounts.dropped;
    const total = kept + dropped;
    return total > 0 ? (kept / total) * 100 : 0;
  })();
  const compareKeptRate = (() => {
    const kept = compareAuditCounts.kept;
    const dropped = compareAuditCounts.dropped;
    const total = kept + dropped;
    if (total > 0) return (kept / total) * 100;
    const any = compareClaims.length;
    if (!any) return 0;
    const keepApprox = compareClaims.filter((claim) => claim.status !== "dropped").length;
    const keptRatio = keepApprox / any;
    return keptRatio * 100;
  })();
  const compareWarnings = useMemo(() => {
    if (!compareMode || !comparePostId) return [];
    const warnings: string[] = [];
    if (baseTotalComments < MIN_COMPARE_COMMENTS) warnings.push(`Baseline comments < ${MIN_COMPARE_COMMENTS}`);
    if (compareTotalComments < MIN_COMPARE_COMMENTS) warnings.push(`Compare comments < ${MIN_COMPARE_COMMENTS}`);
    if (evidence.length < MIN_COMPARE_EVIDENCE) warnings.push(`Baseline evidence < ${MIN_COMPARE_EVIDENCE}`);
    if (compareEvidence.length < MIN_COMPARE_EVIDENCE) warnings.push(`Compare evidence < ${MIN_COMPARE_EVIDENCE}`);
    return warnings;
  }, [baseTotalComments, compareEvidence.length, compareMode, comparePostId, compareTotalComments, evidence.length]);
  const compareTopWindows = useMemo(() => {
    if (!compareMode || !comparePostId || compareLoading) return [];
    return buildTopDeltaWindows({
      baselineComments: baseComments,
      baselineEvidence: evidence,
      compareComments,
      compareEvidence,
      topK: 3,
    });
  }, [baseComments, compareComments, compareEvidence, compareLoading, compareMode, comparePostId, evidence]);

  useEffect(() => {
    if (selectedPost) {
      setCurrentPost({
        id: selectedPost.id,
        snippet: selectedPost.snippet || "-",
        url: selectedPost.url || null,
      });
    } else if (!loadingPosts && posts.length === 0) {
      setCurrentPost(null);
    }
    setPhenomenon(pickPhenomenon(analysis));
    setRiskLevel(riskLevel);
    setStabilityVerdict(integrityLabel);
  }, [
    analysis,
    integrityLabel,
    loadingPosts,
    posts.length,
    riskLevel,
    selectedPost,
    setCurrentPost,
    setPhenomenon,
    setRiskLevel,
    setStabilityVerdict,
  ]);

  const rerun = async () => {
    if (!selectedPost?.url || rerunPending) return;
    setRerunPending(true);
    setRerunMessage("");
    setError("");
    try {
      const payload = {
        pipeline_type: "A",
        mode: "analyze",
        input_config: { url: selectedPost.url, target: selectedPost.url, targets: [selectedPost.url] },
      };
      const job = await api.createJob(payload);
      localStorage.setItem("dl.activeRunId", job.id);
      setRerunMessage(`Re-analysis queued: #${String(job.id).slice(0, 8)}`);
    } catch (e) {
      setError(compactErrorMessage(e instanceof Error ? e.message : String(e)));
    } finally {
      setRerunPending(false);
    }
  };

  return (
    <div className="page-grid">
      <PageHeader
        title="Narrative Intelligence"
        subtitle="Executive-grade narrative forensics with linked evidence, cluster trajectories, and audit confidence."
        actions={
          <>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/pipeline")}>
              Open Pipeline
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/review")}>
              Open Review
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => setPickerOpen(true)}>
              Select Post
            </button>
            <button
              type="button"
              className={`chip-btn motion-btn ${compareMode ? "active" : ""}`}
              onClick={() => setCompareMode((v) => !v)}
            >
              {compareMode ? "Compare on" : "Compare off"}
            </button>
            <span className="chip">Posts: {posts.length}</span>
          </>
        }
      />

      {degraded ? <div className="degraded-banner">Narrative 資料通道暫時降級，已保留最近有效快照。</div> : null}
      {syncing ? <div className="sync-banner">同步 Narrative 資料中…</div> : null}
      {error ? <div className="error-banner compact">{error}</div> : null}
      {rerunMessage ? <div className="ok-banner">{rerunMessage}</div> : null}

      <div className="insights-v2-grid">
        <div className="slot-post">
          <SectionCard
            title="Selected Narrative Asset"
            action={
              <button
                type="button"
                className={`primary-btn motion-btn ${rerunPending ? "loading" : ""}`}
                onClick={rerun}
                disabled={!canRerun || rerunPending}
              >
                {rerunPending ? "Re-analyzing..." : "Re-run Analysis"}
              </button>
            }
          >
            {loadingPosts && !selectedPost ? (
              <div className="skeleton-stack">
                <div className="skeleton-card" />
                <div className="skeleton-card" />
              </div>
            ) : selectedPost ? (
              <div className="selected-post-shell hero-style">
                <PostCard post={selectedPost} variant="standard" />
                <div className="micro-kpi-row">
                  <article className="micro-kpi"><span>Likes</span><strong className="metric-number-inline">{fmtNumber(selectedPost.like_count)}</strong></article>
                  <article className="micro-kpi"><span>Replies</span><strong className="metric-number-inline">{fmtNumber(selectedPost.reply_count)}</strong></article>
                  <article className="micro-kpi"><span>Views</span><strong className="metric-number-inline">{fmtNumber(selectedPost.view_count)}</strong></article>
                  <article className="micro-kpi"><span>Shares</span><strong className="metric-number-inline">{fmtNumber(selectedPost.share_count ?? 0)}</strong></article>
                </div>
                <div className="selected-post-actions">
                  <Link to="/library">Open in Library</Link>
                  <Link to="/review">Open in Review</Link>
                  <Link to="/pipeline">Open Pipeline Runs</Link>
                </div>
              </div>
            ) : (
              <div className="empty-note">無貼文資料。</div>
            )}
          </SectionCard>
        </div>

        <div className="right-stack sticky-risk-stack">
          <SectionCard title="Risk Panel">
            <div className="risk-ring-wrap">
              <div className="risk-ring" style={{ ["--risk" as string]: `${riskScore}%` }}>
                <div>
                  <strong>{riskScore}%</strong>
                  <span>Risk</span>
                </div>
              </div>
              <div className="risk-meta">
                <div><span>Level</span><strong>{riskLevel}</strong></div>
                <div><span>Coverage</span><strong>{coverageText}</strong></div>
                <div><span>Integrity</span><strong>{integrityLabel}</strong></div>
              </div>
            </div>
          </SectionCard>
          <SectionCard title="Behavior Flags">
            <div className="flag-row">
              {behaviorFlags.length ? behaviorFlags.map((flag) => <span key={flag} className="flag-pill">{flag}</span>) : <span className="empty-note">no flags</span>}
            </div>
          </SectionCard>
        </div>

        <div className="slot-summary">
          <SectionCard title="Executive Summary">
            <div className="summary-hook">{summaryHook}</div>
            <p className="summary-text">{summaryPreview}</p>
            {executiveSummary.length > 180 ? (
              <button type="button" className="chip-btn motion-btn" onClick={() => setSummaryExpanded((v) => !v)}>
                {summaryExpanded ? "收起摘要" : "展開完整摘要"}
              </button>
            ) : null}
          </SectionCard>
        </div>

        <div className="slot-audit">
          <SectionCard title="Audit Readiness & Signals">
            {compareMode ? (
              <div className="compare-controls">
                <select className="text-input" value={comparePostId} onChange={(e) => setComparePostId(e.target.value)}>
                  <option value="">Select compare post</option>
                  {posts
                    .filter((post) => post.id !== selectedPostId)
                    .map((post) => (
                      <option key={`cmp-${post.id}`} value={post.id}>
                        {post.snippet || `Post #${post.id}`}
                      </option>
                    ))}
                </select>
                {compareError ? <div className="error-banner compact">{compareError}</div> : null}
              </div>
            ) : null}
            {loadingData && !clusters.length && !claims.length && !evidence.length ? (
              <div className="skeleton-stack">
                <div className="skeleton-card" />
                <div className="skeleton-card" />
              </div>
            ) : (
              <div className="audit-grid">
              <div className="mini-metric-grid">
                <article className="mini-metric"><div className="mini-k">Coverage</div><div className="mini-v metric-number-inline">{coverageText}</div></article>
                <article className="mini-metric"><div className="mini-k">Clusters</div><div className="mini-v metric-number-inline">{clusters.length}</div></article>
                <article className="mini-metric"><div className="mini-k">Claims</div><div className="mini-v metric-number-inline">{claims.length}</div></article>
                <article className="mini-metric"><div className="mini-k">Evidence</div><div className="mini-v metric-number-inline">{evidence.length}</div></article>
              </div>
              <div className="mini-metric-grid">
                <article className="mini-metric"><div className="mini-k">Cluster Engagement</div><div className="mini-v metric-number-inline">{fmtNumber(clusterEngagement)}</div></article>
                <article className="mini-metric"><div className="mini-k">Selected Cluster</div><div className="mini-v metric-number-inline">{selectedCluster ? `C${selectedCluster.cluster_key}` : "-"}</div></article>
                <article className="mini-metric"><div className="mini-k">Cluster Share</div><div className="mini-v metric-number-inline">{fmtPct(selectedCluster?.share)}</div></article>
                <article className="mini-metric"><div className="mini-k">Top Claims</div><div className="mini-v metric-number-inline">{clusterClaims.length}</div></article>
              </div>
              {compareMode ? (
                compareLoading ? (
                  <div className="skeleton-stack">
                    <div className="skeleton-card" />
                  </div>
                ) : comparePostId ? (
                  <CompareBoard
                    baselineLabel={selectedPostId ? `#${selectedPostId}` : "baseline"}
                    compareLabel={`#${comparePostId}`}
                    metrics={[
                      { label: "Comments", baseline: baseTotalComments, compare: compareTotalComments },
                      { label: "Evidence", baseline: evidence.length, compare: compareEvidence.length },
                      { label: "Cluster count", baseline: clusters.length, compare: compareClusters.length },
                      { label: "Top share", baseline: baselineTopShare * 100, compare: compareTopShare * 100, unit: "%" },
                      { label: "Engagement", baseline: baselineEngagement, compare: compareEngagement },
                      { label: "Kept rate", baseline: baselineKeptRate, compare: compareKeptRate, unit: "%" },
                    ]}
                    warnings={compareWarnings}
                    topWindows={compareTopWindows}
                  />
                ) : (
                  <div className="empty-note">Select a baseline post and one comparison post</div>
                )
              ) : null}
            </div>
            )}
          </SectionCard>
        </div>

        <div className="slot-cluster">
          <SectionCard title="Cluster Explorer">
            <div className="cluster-panel">
              <div className="cluster-canvas elevated">
                <ClusterBubbleChart
                  clusters={clusters}
                  selectedKey={selectedCluster?.cluster_key}
                  onSelect={(key) => setSelectedClusterKey(Number(key))}
                  sizeMode="engagement"
                />
              </div>

              <aside className="cluster-thread-panel">
                {selectedCluster ? (
                  <>
                    <h4>{safeText(selectedCluster.label)}</h4>
                    <div className="cluster-meta">互動 {fmtNumber(clusterEngagement)} · 佔比 {fmtPct(selectedCluster.share)} · 顯示 {timelineEvidence.length}/{uniqueEvidenceForCluster.length || selectedCluster.sample_total || 0}（最多 10）</div>
                    <p className="cluster-summary">{safeText(selectedCluster.summary)}</p>
                    <div className="keyword-wrap">
                      {focusChips.map((kw) => (
                        <span key={kw} className="keyword-chip">{kw}</span>
                      ))}
                    </div>
                    {clusterClaims.length ? (
                      <div className="cluster-claims">
                        {clusterClaims.map((c) => (
                          <article key={c.id} className="claim-chip-card">{safeText(c.text)}</article>
                        ))}
                      </div>
                    ) : null}
                    {duplicatesRemoved > 0 ? (
                      <div className="row-sub" data-testid="insights-duplicates-removed">duplicates removed: {duplicatesRemoved}</div>
                    ) : null}
                    <div className="timeline-list">
                      {timelineEvidence.map((ev) => {
                        const stableKey = evidenceStableKey(ev);
                        return (
                          <article
                            key={stableKey}
                            className="timeline-item"
                            data-testid="insights-timeline-item"
                            data-evidence-key={stableKey}
                          >
                            <div className="timeline-dot" />
                            <div className="timeline-body">
                              <div className="ev-author">{safeText(ev.author_handle)}</div>
                              <div className="ev-text">{safeText(ev.text)}</div>
                              <div className="ev-meta">{fmtNumber(ev.like_count)} likes</div>
                            </div>
                          </article>
                        );
                      })}
                      {!uniqueEvidenceForCluster.length ? (
                        <div className="empty-note">No evidence lock available · Underspecified</div>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="empty-note">尚未選擇 cluster。</div>
                )}
              </aside>
            </div>
          </SectionCard>
        </div>
      </div>

      <PostPickerDrawer
        open={pickerOpen}
        posts={posts}
        selectedPostId={selectedPostId}
        onSelect={setSelectedPostId}
        onClose={() => setPickerOpen(false)}
      />
    </div>
  );
}
