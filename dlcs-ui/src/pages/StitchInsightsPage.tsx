import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import templateHtml from "../stitch/narrative_intelligence_console.html?raw";
import { StitchTemplateFrame, type StitchActionMeta, type StitchNotice } from "../components/StitchTemplateFrame";
import { api, formatApiError, isDegradedApiError, ApiError } from "../lib/api";
import { isDebugUI } from "../lib/debug";
import type {
  AnalysisJsonResponse,
  ClaimItem,
  ClusterGraphNode,
  ClusterGraphResponse,
  ClusterItem,
  CommentItem,
  PostItem,
} from "../lib/types";

const navMap = {
  Dashboard: "/overview",
  "Cluster Analysis": "/insights",
  Reports: "/review",
};

const actionMap = {
  refresh: "insights_refresh",
  export: "insights_export",
};

type InsightsStackMode = "narrative" | "evidence";

type GraphNodeVm = {
  id: string;
  cluster_key: number;
  label: string;
  size: number;
  share?: number | null;
  x: number;
  y: number;
};

type SimilarCaseVm = {
  postId: string;
  snippet: string;
  similarityPct: number;
  overlapSignals: string[];
};

type SourceHealthState = "ready" | "pending" | "empty" | "not_found" | "error";

type SourceHealth = {
  source: string;
  state: SourceHealthState;
  reason?: string;
  traceId?: string;
};

function parseSourceHealth(source: string, payload: unknown): SourceHealth {
  const body = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const raw = String(body.status || "ready").trim().toLowerCase();
  const state: SourceHealthState =
    raw === "pending" || raw === "empty" || raw === "not_found" || raw === "error" ? (raw as SourceHealthState) : "ready";
  return {
    source,
    state,
    reason: typeof body.reason_code === "string" ? body.reason_code : (typeof body.reason === "string" ? body.reason : undefined),
    traceId: typeof body.trace_id === "string" ? body.trace_id : undefined,
  };
}

function parseErrorHealth(source: string, error: unknown): SourceHealth {
  if (error instanceof ApiError) {
    return {
      source,
      state: "error",
      reason: error.reasonCode || error.message,
      traceId: error.traceId,
    };
  }
  return {
    source,
    state: "error",
    reason: String(error || "unknown_error"),
  };
}

function summarizeHealthNotice(rows: SourceHealth[]): StitchNotice | null {
  const issues = rows.filter((row) => row.state !== "ready");
  if (!issues.length) return null;
  const hard = issues.find((row) => row.state === "error" || row.state === "not_found");
  const pick = hard || issues[0];
  const reason = pick.reason ? ` · ${pick.reason}` : "";
  const trace = pick.traceId ? ` · trace ${pick.traceId}` : "";
  const kind: "info" | "error" = hard ? "error" : "info";
  return makeNotice(`${pick.source} ${pick.state}${reason}${trace}`, kind);
}

function makeNotice(message: string, kind: "info" | "ok" | "error" = "info"): StitchNotice {
  return { message, kind, nonce: Date.now() + Math.floor(Math.random() * 1000) };
}

function pct(value: number): string {
  if (!Number.isFinite(value)) return "0%";
  return `${Math.round(Math.max(0, Math.min(100, value)))}%`;
}

function shortK(value: number): string {
  if (!Number.isFinite(value)) return "0";
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(Math.round(value));
}

function pickNodeId(clusters: ClusterItem[]): string {
  const top = [...clusters].sort((a, b) => Number(b.size || 0) - Number(a.size || 0))[0];
  if (!top) return "Node C-000";
  return `Node C-${String(top.cluster_key).padStart(3, "0")}`;
}

function similarityScore(a: string, b: string): number {
  const wa = new Set(
    String(a || "")
      .toLowerCase()
      .split(/\s+/)
      .map((x) => x.replace(/[^\p{L}\p{N}_-]+/gu, ""))
      .filter((x) => x.length >= 2)
      .slice(0, 40)
  );
  const wb = new Set(
    String(b || "")
      .toLowerCase()
      .split(/\s+/)
      .map((x) => x.replace(/[^\p{L}\p{N}_-]+/gu, ""))
      .filter((x) => x.length >= 2)
      .slice(0, 40)
  );
  if (!wa.size || !wb.size) return 0;
  let inter = 0;
  for (const w of wa) if (wb.has(w)) inter += 1;
  const union = wa.size + wb.size - inter;
  if (!union) return 0;
  return Math.max(0, Math.min(100, Math.round((inter / union) * 100)));
}

function toTs(value?: string | null): number {
  if (!value) return 0;
  const t = new Date(value).getTime();
  return Number.isFinite(t) ? t : 0;
}

function dedupeEvidenceRows<T extends { id?: string; text?: string }>(rows: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  rows.forEach((row) => {
    const textKey = String(row.text || "")
      .trim()
      .replace(/\s+/g, " ")
      .toLowerCase();
    const idKey = String(row.id || "").trim().toLowerCase();
    const key = textKey ? `txt:${textKey}` : `id:${idKey}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(row);
  });
  return out;
}

function graphNodeToClusterItem(node: ClusterGraphNode): ClusterItem {
  const key = Number(node.cluster_key);
  const metrics = (node.metrics || {}) as Record<string, unknown>;
  const likes = Number(metrics.likes || 0);
  const replies = Number(metrics.replies || 0);
  return {
    cluster_key: Number.isFinite(key) ? key : 0,
    label: String(node.label || `Cluster ${Number.isFinite(key) ? key : 0}`),
    summary: "No cluster summary available.",
    size: Number(node.weight || 0),
    share: node.share ?? null,
    keywords: [],
    sample_total: Number(node.weight || 0),
    samples: [],
    engagement: {
      likes: Number.isFinite(likes) ? likes : 0,
      replies: Number.isFinite(replies) ? replies : 0,
    },
    coords: {
      x: Number(node.coords?.x ?? 0.5),
      y: Number(node.coords?.y ?? 0.5),
    },
    label_source: "graph",
    cip: node.cip || null,
  };
}

export function StitchInsightsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const debugMode = useMemo(() => isDebugUI(), []);
  const [postId, setPostId] = useState(() => {
    if (typeof window === "undefined") return "";
    const fromUrl = new URLSearchParams(window.location.search).get("post_id");
    const fromStorage = window.localStorage.getItem("dl.activePostId");
    return String(fromUrl || fromStorage || "").trim();
  });
  const [posts, setPosts] = useState<PostItem[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisJsonResponse | null>(null);
  const [clusters, setClusters] = useState<ClusterItem[]>([]);
  const [clusterGraph, setClusterGraph] = useState<ClusterGraphResponse | null>(null);
  const [selectedClusterKey, setSelectedClusterKey] = useState<number | null>(null);
  const [stackMode, setStackMode] = useState<InsightsStackMode>("narrative");
  const [comparePostId, setComparePostId] = useState("");
  const [compareDrawerOpen, setCompareDrawerOpen] = useState(false);
  const [claims, setClaims] = useState<ClaimItem[]>([]);
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [sourceHealth, setSourceHealth] = useState<SourceHealth[]>([]);
  const [notice, setNotice] = useState<StitchNotice | null>(null);

  const refreshPosts = useCallback(async () => {
    try {
      const rowsMeta = await api.getPostsMeta();
      const rows = [...(rowsMeta.data || [])].sort(
        (a, b) => toTs(b.created_at) - toTs(a.created_at)
      );
      const latestFromRun =
        (typeof window !== "undefined" ? String(window.localStorage.getItem("dl.activePostId") || "").trim() : "") ||
        "";
      const hasLatest = latestFromRun && rows.some((row) => String(row.id || "") === latestFromRun);
      const merged = hasLatest
        ? rows
        : (latestFromRun
            ? [
                {
                  id: latestFromRun,
                  snippet: "Latest pipeline result (indexing...)",
                  created_at: new Date().toISOString(),
                  author: "pipeline",
                } as PostItem,
                ...rows,
              ]
            : rows);
      setPosts(merged);
      setSourceHealth((prev) => [
        ...prev.filter((row) => row.source !== "posts"),
        {
          source: "posts",
          state: rowsMeta.degraded ? "pending" : "ready",
          reason: rowsMeta.degraded ? "posts_backend_degraded" : undefined,
          traceId: rowsMeta.requestId,
        },
      ]);
      if (rowsMeta.degraded) {
        setNotice(makeNotice(`posts pending · posts_backend_degraded${rowsMeta.requestId ? ` · trace ${rowsMeta.requestId}` : ""}`, "info"));
      }
      setPostId((prev) => {
        if (prev) return prev;
        const fromUrl = String(searchParams.get("post_id") || "").trim();
        return fromUrl || latestFromRun || merged[0]?.id || "";
      });
      setComparePostId((prev) => prev || merged[1]?.id || merged[0]?.id || "");
    } catch (e) {
      setSourceHealth((prev) => [...prev.filter((row) => row.source !== "posts"), parseErrorHealth("posts", e)]);
      setNotice(makeNotice(`Insights posts failed: ${formatApiError(e)}`, "error"));
    }
  }, [searchParams]);

  const refreshData = useCallback(async () => {
    if (!postId) return;
    try {
      const settled = await Promise.allSettled([
        api.getAnalysisJson(postId),
        api.getClusters(postId),
        api.getClusterGraph(postId),
        api.getClaims(postId),
        api.getCommentsByPost(postId, { limit: 200, sort: "time" }),
      ]);
      const nextHealth: SourceHealth[] = [];

      const analysisRes = settled[0];
      if (analysisRes.status === "fulfilled") {
        const health = parseSourceHealth("analysis_json", analysisRes.value);
        nextHealth.push(health);
        if (health.state === "ready" || health.state === "empty" || health.state === "not_found") {
          setAnalysis(analysisRes.value || null);
        }
      } else {
        nextHealth.push(parseErrorHealth("analysis_json", analysisRes.reason));
      }

      const clustersRes = settled[1];
      if (clustersRes.status === "fulfilled") {
        const health = parseSourceHealth("clusters", clustersRes.value);
        nextHealth.push(health);
        if (health.state === "ready" || health.state === "empty" || health.state === "not_found") {
          setClusters(clustersRes.value.clusters || []);
        }
      } else {
        nextHealth.push(parseErrorHealth("clusters", clustersRes.reason));
      }

      const graphRes = settled[2];
      if (graphRes.status === "fulfilled") {
        const health = parseSourceHealth("cluster_graph", graphRes.value);
        nextHealth.push(health);
        if (health.state === "ready" || health.state === "empty" || health.state === "not_found") {
          setClusterGraph(graphRes.value || null);
        }
      } else {
        nextHealth.push(parseErrorHealth("cluster_graph", graphRes.reason));
      }

      const claimsRes = settled[3];
      if (claimsRes.status === "fulfilled") {
        const health = parseSourceHealth("claims", claimsRes.value);
        nextHealth.push(health);
        if (health.state === "ready" || health.state === "empty" || health.state === "not_found") {
          setClaims(claimsRes.value.claims || []);
        }
      } else {
        nextHealth.push(parseErrorHealth("claims", claimsRes.reason));
      }

      const commentsRes = settled[4];
      if (commentsRes.status === "fulfilled") {
        nextHealth.push({ source: "comments", state: "ready" });
        setComments(commentsRes.value.items || []);
      } else {
        nextHealth.push(parseErrorHealth("comments", commentsRes.reason));
      }

      setSourceHealth((prev) => {
        const keep = prev.filter((row) => row.source === "posts");
        return [...keep, ...nextHealth];
      });
      const sourceNotice = summarizeHealthNotice(nextHealth);
      if (sourceNotice) setNotice(sourceNotice);
    } catch (e) {
      if (isDegradedApiError(e)) {
        setNotice(makeNotice("Insights source degraded; showing partial data.", "info"));
      } else {
        setNotice(makeNotice(`Insights sync failed: ${formatApiError(e)}`, "error"));
      }
    }
  }, [postId]);

  useEffect(() => {
    void refreshPosts();
    const timer = window.setInterval(() => void refreshPosts(), 12000);
    return () => window.clearInterval(timer);
  }, [refreshPosts]);

  useEffect(() => {
    const fromUrl = String(searchParams.get("post_id") || "").trim();
    if (fromUrl && fromUrl !== postId) {
      setPostId(fromUrl);
    }
  }, [postId, searchParams]);

  useEffect(() => {
    const next = String(postId || "").trim();
    if (!next) return;
    setSearchParams((prev) => {
      const query = new URLSearchParams(prev);
      if (query.get("post_id") === next) return query;
      query.set("post_id", next);
      return query;
    }, { replace: true });
  }, [postId, setSearchParams]);

  useEffect(() => {
    if (!posts.length) return;
    const current = String(postId || "").trim();
    const available = posts
      .map((row) => String(row.id || "").trim())
      .filter((id) => id && id !== current);
    if (!available.length) return;
    if (!comparePostId || comparePostId === current || !available.includes(comparePostId)) {
      setComparePostId(available[0]);
    }
  }, [comparePostId, postId, posts]);

  useEffect(() => {
    void refreshData();
    if (!postId) return;
    const timer = window.setInterval(() => void refreshData(), 7000);
    return () => window.clearInterval(timer);
  }, [postId, refreshData]);

  const onAction = useCallback(
    async (action: string, meta: StitchActionMeta) => {
      if (action === "insights_refresh") {
        await refreshData();
        setNotice(makeNotice("Insights refreshed.", "ok"));
        return;
      }
      if (action === "insights_select_cluster") {
        const raw = Number(meta.clusterKey);
        if (Number.isFinite(raw)) {
          setSelectedClusterKey(raw);
          setStackMode("narrative");
        }
        return;
      }
      if (action === "insights_select_post") {
        const raw = String(meta.postId || meta.query || meta.searchQuery || "").trim();
        if (!raw) return;
        const key = raw.toLowerCase();
        const hit = posts.find(
          (row) =>
            String(row.id || "") === raw ||
            String(row.id || "").toLowerCase() === key ||
            String(row.snippet || "").toLowerCase().includes(key) ||
            String(row.url || "").toLowerCase().includes(key)
        );
        if (!hit) {
          setNotice(makeNotice(`Post not found: ${raw}`, "info"));
          return;
        }
        setPostId(String(hit.id || ""));
        setCompareDrawerOpen(false);
        setNotice(makeNotice(`Insights switched to post ${String(hit.id)}`, "ok"));
        return;
      }
      if (action === "insights_compare_post") {
        const raw = String(meta.postId || meta.query || "").trim();
        const available = posts
          .map((row) => String(row.id || "").trim())
          .filter((id) => id && id !== String(postId || "").trim());
        if (!available.length) {
          setNotice(makeNotice("No compare post available.", "info"));
          return;
        }
        if (raw) {
          const hit = posts.find((row) => String(row.id || "") === raw);
          if (!hit) return;
          setComparePostId(String(hit.id || ""));
          setCompareDrawerOpen(true);
          setNotice(makeNotice(`Compare target set: ${String(hit.id)}`, "info"));
          return;
        }
        const idx = Math.max(0, available.indexOf(String(comparePostId || "")));
        const next = available[(idx + 1) % available.length] || available[0];
        setComparePostId(next);
        setCompareDrawerOpen(true);
        setNotice(makeNotice(`Compare target set: ${next}`, "info"));
        return;
      }
      if (action === "insights_close_compare") {
        setCompareDrawerOpen(false);
        return;
      }
      if (action === "insights_stack_primary") {
        setStackMode("narrative");
        return;
      }
      if (action === "insights_stack_secondary") {
        setStackMode("evidence");
        return;
      }
      if (action === "insights_open_evidence") {
        const clusterKey = Number(meta.clusterKey);
        if (Number.isFinite(clusterKey)) setSelectedClusterKey(clusterKey);
        setStackMode("evidence");
        return;
      }
      if (action === "insights_open_summary") {
        const clusterKey = Number(meta.clusterKey);
        if (Number.isFinite(clusterKey)) setSelectedClusterKey(clusterKey);
        setStackMode("narrative");
        return;
      }
      if (action === "insights_open_comment_review") {
        const commentId = String(meta.commentId || "").trim();
        const clusterKey = String(meta.clusterKey || selectedClusterKey || "").trim();
        const post = String(meta.postId || postId || "").trim();
        if (!commentId || !post) return;
        const query = new URLSearchParams({ post_id: post, comment_id: commentId });
        if (clusterKey) query.set("cluster_key", clusterKey);
        navigate(`/review?${query.toString()}`);
        return;
      }
      if (action === "insights_go_review") {
        const targetPost = String(meta.postId || postId || "").trim();
        if (!targetPost) return;
        navigate(`/review?post_id=${encodeURIComponent(targetPost)}`);
        return;
      }
      if (action === "insights_open_threads") {
        const targetPost = String(meta.postId || postId || "").trim();
        const byId = posts.find((row) => String(row.id || "") === targetPost);
        const targetUrl = String(meta.threadsUrl || byId?.url || "").trim();
        if (!targetUrl) {
          setNotice(makeNotice("Threads URL not available for this post.", "info"));
          return;
        }
        window.open(targetUrl, "_blank", "noopener,noreferrer");
        return;
      }
      if (action === "insights_copy_post_id") {
        const targetPost = String(meta.postId || postId || "").trim();
        if (!targetPost) return;
        try {
          await navigator.clipboard.writeText(targetPost);
          setNotice(makeNotice(`Post ID copied: ${targetPost}`, "ok"));
        } catch {
          setNotice(makeNotice(`Post ID: ${targetPost}`, "info"));
        }
        return;
      }
      if (action === "insights_export") {
        const payload = {
          postId,
          analysis,
          clusters,
          clusterGraph,
          claims,
          comments: comments.slice(0, 100),
          exportedAt: new Date().toISOString(),
        };
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `insights-${postId || "latest"}-${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setNotice(makeNotice("Insights snapshot exported.", "ok"));
      }
    },
    [analysis, claims, clusterGraph, clusters, comments, navigate, postId, posts, refreshData, selectedClusterKey, comparePostId]
  );

  const clusterRows = useMemo(() => {
    if (clusters.length) return clusters;
    const fallback = (clusterGraph?.nodes || []).map(graphNodeToClusterItem);
    return fallback.filter((row) => Number.isFinite(Number(row.cluster_key)));
  }, [clusterGraph?.nodes, clusters]);

  const clusterSizeMap = useMemo(() => {
    const m = new Map<number, number>();
    clusterRows.forEach((row) => m.set(Number(row.cluster_key), Number(row.size || 0)));
    return m;
  }, [clusterRows]);

  const graphNodes = useMemo(
    (): GraphNodeVm[] => {
      const source = (clusterGraph?.nodes || []).length
        ? (clusterGraph?.nodes || []).map((row) => {
            const ck = Number(row.cluster_key);
            const fallback = clusterSizeMap.get(ck) || 0;
            return {
              id: String(row.id || `c-${ck}`),
              cluster_key: ck,
              label: String(row.label || `C-${String(ck).padStart(3, "0")}`),
              size: Number(row.weight || fallback || 0),
              share: row.share,
              x: Number(row.coords?.x ?? 0.5),
              y: Number(row.coords?.y ?? 0.5),
            };
          })
        : clusterRows.map((row, idx) => {
            const angle = (idx / Math.max(1, clusters.length)) * Math.PI * 2;
            return {
              id: `c-${Number(row.cluster_key)}`,
              cluster_key: Number(row.cluster_key),
              label: String(row.label || `C-${String(row.cluster_key).padStart(3, "0")}`),
              size: Number(row.size || 0),
              share: row.share,
              x: 0.5 + 0.35 * Math.cos(angle),
              y: 0.5 + 0.35 * Math.sin(angle),
            };
          });
      const filtered = source
        .filter((row) => Number.isFinite(row.cluster_key))
        .map((row) => ({
          ...row,
          x: Number.isFinite(row.x) ? row.x : 0.5,
          y: Number.isFinite(row.y) ? row.y : 0.5,
          size: Math.max(1, Number(row.size || 1)),
        }));
      if (!filtered.length) return [];
      const sorted = [...filtered].sort((a, b) => b.size - a.size);
      const centerKey = selectedClusterKey != null && sorted.some((row) => row.cluster_key === selectedClusterKey)
        ? selectedClusterKey
        : Number(sorted[0]?.cluster_key ?? 0);
      const centerNode = sorted.find((row) => row.cluster_key === centerKey) || sorted[0];
      if (!centerNode) return filtered;
      const others = sorted.filter((row) => row.cluster_key !== centerNode.cluster_key);
      const ringCount = others.length;
      const maxRing = Math.max(1, Math.min(10, ringCount));
      const ringStep = (Math.PI * 2) / maxRing;
      let idx = 0;
      const radialById = new Map<string, { x: number; y: number }>();
      radialById.set(centerNode.id, { x: 0.5, y: 0.5 });
      for (const row of others) {
        const ring = Math.floor(idx / maxRing);
        const angle = (idx % maxRing) * ringStep - Math.PI / 2;
        const radius = Math.min(0.4, 0.24 + ring * 0.11);
        const x = 0.5 + radius * Math.cos(angle);
        const y = 0.5 + radius * Math.sin(angle);
        radialById.set(row.id, { x, y });
        idx += 1;
      }
      return filtered.map((row) => {
        const hit = radialById.get(row.id) || { x: 0.5, y: 0.5 };
        return {
          ...row,
          x: Math.max(0.06, Math.min(0.94, hit.x)),
          y: Math.max(0.08, Math.min(0.92, hit.y)),
        };
      });
    },
    [clusterGraph?.nodes, clusterRows, clusterSizeMap, selectedClusterKey]
  );
  const graphLinks = useMemo(
    () =>
      (clusterGraph?.links || [])
        .map((row) => ({
          source: String(row.source || ""),
          target: String(row.target || ""),
          weight: Number(row.weight || 0),
        }))
        .filter((row) => row.source && row.target && row.source !== row.target),
    [clusterGraph?.links]
  );

  const graphLabelMap = useMemo(() => {
    const map = new Map<number, string>();
    (clusterGraph?.nodes || []).forEach((node) => {
      const key = Number(node.cluster_key);
      const label = String(node.label || "").trim();
      if (Number.isFinite(key) && label) map.set(key, label);
    });
    return map;
  }, [clusterGraph?.nodes]);

  const clusterLabelMap = useMemo(() => {
    const map = new Map<number, string>();
    clusterRows.forEach((row) => {
      const key = Number(row.cluster_key);
      const label = String(row.label || "").trim();
      if (Number.isFinite(key) && label) map.set(key, label);
    });
    graphLabelMap.forEach((label, key) => {
      if (!map.has(key) && label) map.set(key, label);
    });
    return map;
  }, [clusterRows, graphLabelMap]);

  useEffect(() => {
    if (!graphNodes.length) {
      setSelectedClusterKey(null);
      return;
    }
    setSelectedClusterKey((prev) => {
      if (prev != null && graphNodes.some((row) => row.cluster_key === prev)) return prev;
      return Number(graphNodes[0]?.cluster_key ?? null);
    });
  }, [graphNodes]);

  const nodeId = useMemo(() => {
    if (selectedClusterKey != null) {
      return `Node C-${String(selectedClusterKey).padStart(3, "0")}`;
    }
    return pickNodeId(clusterRows);
  }, [clusterRows, selectedClusterKey]);
  const topClusters = useMemo(
    () => {
      const base = [...clusterRows].sort((a, b) => Number(b.size || 0) - Number(a.size || 0));
      if (selectedClusterKey == null) return base.slice(0, 3);
      const selected = base.find((row) => Number(row.cluster_key) === selectedClusterKey);
      const rest = base.filter((row) => Number(row.cluster_key) !== selectedClusterKey).slice(0, 2);
      return selected ? [selected, ...rest] : base.slice(0, 3);
    },
    [clusterRows, selectedClusterKey]
  );
  const topCluster = topClusters[0] || null;
  const totalSize = useMemo(() => topClusters.reduce((sum, c) => sum + Number(c.size || 0), 0), [topClusters]);
  const graphStats = useMemo(() => {
    const nodes = graphNodes.length;
    const edges = graphLinks.length;
    return {
      nodes: nodes || clusterRows.length || 0,
      edges: edges || Math.max(0, (nodes || clusterRows.length || 0) - 1),
    };
  }, [clusterRows.length, graphLinks.length, graphNodes.length]);
  const stack = useMemo(() => {
    const labels = ["L1", "L2", "L3"];
    return labels.map((title, idx) => {
      const c = topClusters[idx];
      const key = Number(c?.cluster_key);
      const sampleRows = c?.samples || [];
      const clusterComments = comments.filter((row) => Number(row.cluster_key) === key);
      const clusterClaims = claims.filter(
        (row) => Number(row.cluster_key) === key || Number(row.primary_cluster_key) === key
      );
      const likes = clusterComments.length
        ? clusterComments.reduce((sum, row) => sum + Number(row.like_count || 0), 0)
        : Number(c?.engagement?.likes || sampleRows.reduce((sum, row) => sum + Number(row.like_count || 0), 0));
      const replies = clusterComments.length
        ? clusterComments.reduce((sum, row) => sum + Number(row.reply_count || 0), 0)
        : Number(c?.engagement?.replies || sampleRows.reduce((sum, row) => sum + Number(row.reply_count || 0), 0));
      const engagement = likes + replies;
      const size = Number(c?.size || 0);
      const rawShare = Number(c?.share || 0);
      const share = rawShare > 0 ? (rawShare <= 1 ? rawShare * 100 : rawShare) : (totalSize > 0 ? (size / totalSize) * 100 : 0);
      const unstable = Boolean(c?.cip?.label_unstable);
      const fallbackBrief = String(clusterComments[0]?.text || sampleRows[0]?.text || "").trim();
      const brief = String(c?.summary || fallbackBrief || "No cluster brief available.").slice(0, 220);
      const commentsCount = clusterComments.length || Number(c?.size || sampleRows.length || 0);
      return {
        title: c?.label ? `${title}: ${c.label}` : `${title}: Cluster`,
        clusterKey: c ? key : null,
        brief,
        penetration: pct(share),
        penetrationPct: share,
        clusterSize: `${shortK(commentsCount)} comments`,
        engagement: engagement.toLocaleString(),
        commentsCount,
        claimsCount: clusterClaims.length,
        status: unstable ? "Risk" : "Stable",
      };
    });
  }, [claims, comments, topClusters, totalSize]);

  const stackAlt = useMemo(() => {
    return topClusters.map((cluster, idx) => {
      const key = Number(cluster?.cluster_key);
      const clusterClaims = claims.filter(
        (row) => Number(row.cluster_key) === key || Number(row.primary_cluster_key) === key
      );
      const clusterComments = comments.filter((row) => Number(row.cluster_key) === key);
      const sampleRows = cluster?.samples || [];
      const sample = String(clusterClaims[0]?.text || clusterComments[0]?.text || sampleRows[0]?.text || "No cluster evidence yet.").trim();
      const preview = sample.length > 70 ? `${sample.slice(0, 70)}...` : sample;
      const commentCount = clusterComments.length || Number(cluster?.size || sampleRows.length || 0);
      const pctValue = comments.length ? (commentCount / comments.length) * 100 : 0;
      return {
        title: `L${idx + 1}: ${(cluster?.label || `Cluster ${key}`)}`,
        clusterKey: Number.isFinite(key) ? key : null,
        penetration: `${commentCount} comments`,
        penetrationPct: pctValue,
        clusterSize: `${clusterClaims.length} claims`,
        status: clusterClaims.length ? "Evidence" : "Sparse",
        subtitle: preview,
      };
    });
  }, [claims, comments, topClusters]);

  const stability = useMemo(
    () => ({
      scorePct: "--",
      verdict: "MOCK",
      entropy: "--",
      driftScore: "--",
    }),
    []
  );

  const axis = useMemo(
    () => ({
      semanticMatch: "TBD",
      temporalDrift: "TBD",
      volumeImpact: "TBD",
      reachDelta: "TBD",
    }),
    []
  );

  const centerShare = useMemo(() => {
    const current =
      (selectedClusterKey != null
        ? clusterRows.find((row) => Number(row.cluster_key) === selectedClusterKey)
        : topClusters[0]) || null;
    if (!current || totalSize <= 0) return "0%";
    const rawShare = Number(current.share || 0);
    if (rawShare > 0) return pct(rawShare <= 1 ? rawShare * 100 : rawShare);
    return pct((Number(current.size || 0) / totalSize) * 100);
  }, [clusterRows, selectedClusterKey, topClusters, totalSize]);
  const topClusterCode = useMemo(() => {
    if (!topCluster) return "";
    const rawShare = Number(topCluster.share || 0);
    const shareText = rawShare > 0 ? pct(rawShare <= 1 ? rawShare * 100 : rawShare) : "";
    return `C-${String(topCluster.cluster_key).padStart(3, "0")}${shareText ? ` ${shareText}` : ""}`;
  }, [topCluster]);

  const selectedClusterSummary = useMemo(() => {
    const cluster =
      (selectedClusterKey != null
        ? clusterRows.find((row) => Number(row.cluster_key) === selectedClusterKey)
        : null) || topClusters[0] || null;
    if (!cluster) return null;
    const key = Number(cluster.cluster_key);
    const sampleRows = cluster.samples || [];
    const clusterClaims = claims.filter(
      (row) => Number(row.cluster_key) === key || Number(row.primary_cluster_key) === key
    );
    const clusterComments = comments.filter((row) => Number(row.cluster_key) === key);
    return {
      clusterKey: key,
      title: clusterLabelMap.get(key) || `Cluster ${key}`,
      summary: String(cluster.summary || sampleRows[0]?.text || "No summary available."),
      commentsCount: clusterComments.length || Number(cluster.size || sampleRows.length || 0),
      claimsCount: clusterClaims.length,
      sampleTotal: Number(cluster.sample_total || cluster.size || clusterComments.length || sampleRows.length || 0),
      risk: Boolean(cluster.cip?.label_unstable) ? "Risk" : "Stable",
    };
  }, [claims, clusterLabelMap, clusterRows, comments, selectedClusterKey, topClusters]);

  const selectedClusterLabel = useMemo(() => {
    if (selectedClusterSummary?.title) return String(selectedClusterSummary.title);
    if (selectedClusterKey == null) return "";
    return clusterLabelMap.get(Number(selectedClusterKey)) || "";
  }, [clusterLabelMap, selectedClusterKey, selectedClusterSummary?.title]);

  const evidencePreview = useMemo(() => {
    if (!selectedClusterSummary) return [];
    const key = Number(selectedClusterSummary.clusterKey);
    const fromComments = dedupeEvidenceRows(
      comments
      .filter((row) => Number(row.cluster_key) === key)
      .slice(0, 10)
      .map((row) => ({
        id: String(row.id || ""),
        clusterKey: key,
        postId: String(row.post_id || postId || ""),
        author: String(row.author_handle || "-"),
        likes: Number(row.like_count || 0),
        text: String(row.text || "").trim(),
        createdAt: String(row.created_at || ""),
      }))
    );
    if (fromComments.length) return fromComments;
    const cluster = clusterRows.find((row) => Number(row.cluster_key) === key);
    return dedupeEvidenceRows((cluster?.samples || []).slice(0, 10).map((row) => ({
      id: String(row.id || ""),
      clusterKey: key,
      postId: String(postId || ""),
      author: String(row.author_handle || "-"),
      likes: Number(row.like_count || 0),
      text: String(row.text || "").trim(),
      createdAt: String(row.created_at || ""),
    })));
  }, [clusterRows, comments, postId, selectedClusterSummary]);

  const evidenceByCluster = useMemo(() => {
    const out: Record<string, Array<{
      id: string;
      clusterKey: number;
      postId: string;
      author: string;
      likes: number;
      text: string;
      createdAt: string;
    }>> = {};
    topClusters.forEach((cluster) => {
      const key = Number(cluster.cluster_key);
      const fromComments = dedupeEvidenceRows(
        comments
        .filter((row) => Number(row.cluster_key) === key)
        .slice(0, 10)
        .map((row) => ({
          id: String(row.id || ""),
          clusterKey: key,
          postId: String(row.post_id || postId || ""),
          author: String(row.author_handle || "-"),
          likes: Number(row.like_count || 0),
          text: String(row.text || "").trim(),
          createdAt: String(row.created_at || ""),
        }))
      );
      if (fromComments.length) {
        out[String(key)] = fromComments;
        return;
      }
      out[String(key)] = dedupeEvidenceRows((cluster.samples || []).slice(0, 10).map((row) => ({
        id: String(row.id || ""),
        clusterKey: key,
        postId: String(postId || ""),
        author: String(row.author_handle || "-"),
        likes: Number(row.like_count || 0),
        text: String(row.text || "").trim(),
        createdAt: String(row.created_at || ""),
      })));
    });
    return out;
  }, [comments, postId, topClusters]);

  const comparePanel = useMemo(() => {
    const comparePost = posts.find((row) => String(row.id || "") === comparePostId) || null;
    const left = selectedClusterSummary;
    const activePost = posts.find((row) => String(row.id || "") === String(postId || "")) || null;
    const candidates = posts
      .filter((row) => String(row.id || "") !== String(postId || ""))
      .slice(0, 6)
      .map((row) => ({
        id: String(row.id || ""),
        snippet: String(row.snippet || ""),
      }));
    const similarCases: SimilarCaseVm[] = posts
      .filter((row) => String(row.id || "") && String(row.id || "") !== String(postId || ""))
      .slice(0, 8)
      .map((row) => {
        const snippet = String(row.snippet || "");
        const sim = similarityScore(String(activePost?.snippet || ""), snippet);
        return {
          postId: String(row.id || ""),
          snippet,
          similarityPct: sim > 0 ? sim : Math.max(14, Math.min(86, 30 + (String(row.id || "").length % 35))),
          overlapSignals: [
            left?.title ? `cluster:${left.title.split(":").slice(1).join(":").trim() || "n/a"}` : "cluster:n/a",
            `risk:${String(left?.risk || "stable").toLowerCase()}`,
            `comments:${Math.max(1, Number(left?.commentsCount || 0))}`,
          ],
        };
      });
    return {
      mode: "sample_ui_only",
      leftTitle: left?.title || "Cluster",
      leftCount: left?.commentsCount || 0,
      rightPostId: comparePost?.id || comparePostId || "-",
      rightSnippet: String(comparePost?.snippet || "Pending compare wiring."),
      similarity: left ? Math.max(12, Math.min(92, 35 + left.commentsCount)) : 0,
      note: "SAMPLE / TEST DATA FOR UI PURPOSE",
      candidates,
      similarCases,
      drawerOpen: compareDrawerOpen,
      drawer: comparePost
        ? {
            postId: String(comparePost.id || ""),
            snippet: String(comparePost.snippet || ""),
            likes: Number(comparePost.like_count || 0),
            replies: Number(comparePost.reply_count || 0),
            reposts: Number(comparePost.repost_count || 0),
            shares: Number(comparePost.share_count || 0),
            createdAt: String(comparePost.created_at || ""),
          }
        : null,
    };
  }, [compareDrawerOpen, comparePostId, postId, posts, selectedClusterSummary]);

  const postPicker = useMemo(
    () =>
      posts.slice(0, 20).map((row) => ({
        id: String(row.id || ""),
        label: `post ${String(row.id || "").slice(0, 8)}`,
        snippet: String(row.snippet || "").trim(),
        url: String(row.url || ""),
        createdAt: String(row.created_at || ""),
        author: String(row.author || ""),
      })),
    [posts]
  );

  const activePost = useMemo(
    () => posts.find((row) => String(row.id || "") === String(postId || "")) || null,
    [postId, posts]
  );

  const postCard = useMemo(
    () => ({
      postId: String(activePost?.id || postId || ""),
      author: String(activePost?.author || "-"),
      text: String(activePost?.snippet || ""),
      createdAt: String(activePost?.created_at || ""),
      threadsUrl: String(activePost?.url || ""),
      likes: activePost?.like_count == null ? null : Number(activePost.like_count || 0),
      replies: activePost?.reply_count == null ? null : Number(activePost.reply_count || 0),
      reposts: activePost?.repost_count == null ? null : Number(activePost.repost_count || 0),
      shares: activePost?.share_count == null ? null : Number(activePost.share_count || 0),
      mediaHint: "media pending",
    }),
    [activePost, postId]
  );

  const bridgeData = useMemo(
    () => ({
      page: "insights",
      nodeId,
      centerShare,
      nodes: graphStats.nodes,
      edges: graphStats.edges,
      topClusterCode,
      graphNodes,
      graphLinks,
      selectedClusterKey: selectedClusterKey ?? undefined,
      postId,
      posts: postPicker,
      postCard,
      stackMode,
      stack,
      stackAlt,
      evidenceByCluster,
      selectedClusterSummary,
      selectedClusterLabel,
      evidencePreview,
      comparePanel,
      axis,
      stability,
      sourceHealth,
      debugMode,
    }),
    [
      axis,
      centerShare,
      graphLinks,
      graphNodes,
      graphStats.edges,
      graphStats.nodes,
      nodeId,
      postId,
      postPicker,
      postCard,
      comparePanel,
      evidenceByCluster,
      evidencePreview,
      selectedClusterSummary,
      selectedClusterLabel,
      selectedClusterKey,
      sourceHealth,
      stack,
      stackAlt,
      stackMode,
      stability,
      topClusterCode,
      debugMode,
    ]
  );

  return (
    <StitchTemplateFrame
      html={templateHtml}
      navMap={navMap}
      title="Narrative Intelligence"
      pageId="insights"
      actionMap={actionMap}
      bridgeData={bridgeData}
      onAction={onAction}
      notice={notice}
      hideTemplateHeader
    />
  );
}
