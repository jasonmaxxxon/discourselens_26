import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, isDegradedApiError } from "../lib/api";
import type { CasebookItem, ClaimItem, ClusterItem, CommentItem, EvidenceItem, PostItem } from "../lib/types";
import {
  buildCasebookSnapshot,
  casebookToCsv,
  casebookToJson,
  downloadTextFile,
  renderCasebookSummary,
} from "../lib/casebook";
import { compactErrorMessage, fmtNumber, normalizeForDedupe } from "../lib/format";
import { CommentMomentumPanel } from "../components/CommentMomentumPanel";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { SectionCard } from "../components/SectionCard";
import { readRouteSnapshot, writeRouteSnapshot } from "../lib/routeCache";
import { useIntelligenceStore } from "../store/intelligenceStore";

type EvidenceGroup = {
  key: string;
  representativeEvidence: EvidenceItem;
  mergedClaims: Array<{ text: string; status: string }>;
  clusterKeys: number[];
  rawEvidenceCount: number;
  evidenceIds: string[];
};

type ViewMode = "cards" | "table" | "timeline";

type EvidenceQuality = {
  label: "high" | "medium" | "low";
  score: number;
};

type LibrarySnapshot = {
  posts: PostItem[];
  postId: string;
  clusters: ClusterItem[];
  claims: ClaimItem[];
  evidence: EvidenceItem[];
};

const CACHE_KEY = "dl.cache.route.library.v1";

function validIsoOrNull(raw: string | null): string | null {
  if (!raw) return null;
  const ts = new Date(raw).getTime();
  if (!Number.isFinite(ts)) return null;
  return new Date(ts).toISOString();
}

function tokenSet(text: string): Set<string> {
  return new Set(normalizeForDedupe(text).split(" ").filter((t) => t.length >= 2));
}

function isNearDuplicate(a: string, b: string): boolean {
  const na = normalizeForDedupe(a);
  const nb = normalizeForDedupe(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  if (na.includes(nb) || nb.includes(na)) return true;
  const ta = tokenSet(na);
  const tb = tokenSet(nb);
  if (!ta.size || !tb.size) return false;
  let inter = 0;
  for (const t of ta) if (tb.has(t)) inter += 1;
  const union = ta.size + tb.size - inter;
  return union > 0 && inter / union >= 0.72;
}

function toShort(text: string, max = 120): string {
  const t = text.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max)}...`;
}

function shortToken(value: unknown, max = 8): string {
  if (value === null || value === undefined) return "-";
  const kind = typeof value;
  const raw = kind === "string" || kind === "number" || kind === "bigint" || kind === "boolean" ? String(value).trim() : "";
  if (!raw) return "-";
  return raw.slice(0, max);
}

function qualityForGroup(group: EvidenceGroup): EvidenceQuality {
  const txt = String(group.representativeEvidence.text || "");
  const likes = Number(group.representativeEvidence.like_count || 0);
  const hasDetail = txt.length >= 80 ? 1 : 0;
  const richDetail = txt.length >= 160 ? 1 : 0;
  const hasQuant = /\d+/.test(txt) ? 1 : 0;
  const engagement = likes >= 100 ? 2 : likes >= 20 ? 1 : 0;
  const score = hasDetail + richDetail + hasQuant + engagement;
  if (score >= 4) return { label: "high", score };
  if (score >= 2) return { label: "medium", score };
  return { label: "low", score };
}

function buildEvidenceGroups(claims: ClaimItem[], evidence: EvidenceItem[]): EvidenceGroup[] {
  const claimMap = new Map<string, ClaimItem>();
  for (const c of claims) claimMap.set(String(c.id), c);

  const grouped = new Map<string, EvidenceGroup>();

  for (const ev of evidence) {
    const evNorm = normalizeForDedupe(ev.text || "");
    const claim = claimMap.get(String(ev.claim_id || ""));
    const claimText = (ev.claim_text || claim?.text || "").trim();
    const claimNorm = normalizeForDedupe(claimText);
    const key = evNorm || claimNorm;
    if (!key) continue;

    if (!grouped.has(key)) {
      grouped.set(key, {
        key,
        representativeEvidence: ev,
        mergedClaims: [],
        clusterKeys: [],
        rawEvidenceCount: 0,
        evidenceIds: [],
      });
    }

    const g = grouped.get(key)!;
    g.rawEvidenceCount += 1;
    if (ev.id && !g.evidenceIds.includes(ev.id)) {
      g.evidenceIds.push(ev.id);
    }

    if ((ev.like_count || 0) > (g.representativeEvidence.like_count || 0)) {
      g.representativeEvidence = ev;
    }

    if (typeof ev.cluster_key === "number" && !g.clusterKeys.includes(ev.cluster_key)) {
      g.clusterKeys.push(ev.cluster_key);
    }

    const status = claim?.status || ev.claim_status || "unknown";
    if (claimText && !g.mergedClaims.find((c) => c.status === status && isNearDuplicate(c.text, claimText))) {
      g.mergedClaims.push({ text: claimText, status });
    }
  }

  return Array.from(grouped.values())
    .map((g) => {
      g.mergedClaims = g.mergedClaims.slice(0, 4);
      g.clusterKeys.sort((a, b) => a - b);
      return g;
    })
    .sort((a, b) => (b.representativeEvidence.like_count || 0) - (a.representativeEvidence.like_count || 0));
}

export function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const setCurrentPost = useIntelligenceStore((s) => s.setCurrentPost);
  const initial = readRouteSnapshot<LibrarySnapshot>(CACHE_KEY);
  const [posts, setPosts] = useState<PostItem[]>(initial?.data.posts || []);
  const [postId, setPostId] = useState<string>(initial?.data.postId || "");
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [commentsTotal, setCommentsTotal] = useState<number | null>(null);
  const [clusters, setClusters] = useState<ClusterItem[]>(initial?.data.clusters || []);
  const [claims, setClaims] = useState<ClaimItem[]>(initial?.data.claims || []);
  const [evidence, setEvidence] = useState<EvidenceItem[]>(initial?.data.evidence || []);
  const [clusterFilter, setClusterFilter] = useState<string>("all");
  const [claimFilter, setClaimFilter] = useState<string>("all");
  const [authorFilter, setAuthorFilter] = useState("");
  const [windowT0, setWindowT0] = useState<string | null>(null);
  const [windowT1, setWindowT1] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("cards");
  const [q, setQ] = useState("");
  const [selectedEvidenceKeys, setSelectedEvidenceKeys] = useState<string[]>([]);
  const [hoveredGroupKey, setHoveredGroupKey] = useState("");
  const [scopeModalOpen, setScopeModalOpen] = useState(false);
  const [inspectorKey, setInspectorKey] = useState("");
  const [error, setError] = useState("");
  const [investigateMsg, setInvestigateMsg] = useState("");
  const [degraded, setDegraded] = useState(false);
  const [syncing, setSyncing] = useState(Boolean(initial));
  const [loadingPosts, setLoadingPosts] = useState(!(initial?.data.posts?.length));
  const [loadingData, setLoadingData] = useState(
    !(initial?.data.evidence?.length || initial?.data.claims?.length || initial?.data.clusters?.length)
  );
  const [menuState, setMenuState] = useState<{ x: number; y: number; comment: CommentItem } | null>(null);
  const [casebookCount, setCasebookCount] = useState(0);
  const [casebookItems, setCasebookItems] = useState<CasebookItem[]>([]);
  const [casebookOnly, setCasebookOnly] = useState(false);

  const updateInvestigateQuery = (patch: {
    post_id?: string | null;
    t0?: string | null;
    t1?: string | null;
    cluster_key?: string | null;
    author?: string | null;
    q?: string | null;
    casebookOnly?: boolean | null;
  }) => {
    const next = new URLSearchParams(searchParams);
    const assign = (key: string, value?: string | null) => {
      if (value === null || value === undefined || value === "" || value === "all") next.delete(key);
      else next.set(key, value);
    };
    const assignBool = (key: string, value?: boolean | null) => {
      if (!value) next.delete(key);
      else next.set(key, "1");
    };
    assign("post_id", patch.post_id);
    assign("t0", patch.t0);
    assign("t1", patch.t1);
    assign("cluster_key", patch.cluster_key);
    assign("author", patch.author);
    assign("q", patch.q);
    assignBool("casebookOnly", patch.casebookOnly);
    setSearchParams(next, { replace: true });
  };

  useEffect(() => {
    const key = searchParams.toString();
    if (!key) return;
    const nextPostId = searchParams.get("post_id");
    const nextQuery = searchParams.get("q") || "";
    const nextCluster = searchParams.get("cluster_key") || "all";
    const nextAuthor = searchParams.get("author") || "";
    const nextCasebookOnly = searchParams.get("casebookOnly") === "1";
    const parsedT0 = validIsoOrNull(searchParams.get("t0"));
    const parsedT1 = validIsoOrNull(searchParams.get("t1"));
    if (searchParams.get("t0") && !parsedT0) setInvestigateMsg("Invalid t0 ignored.");
    else if (searchParams.get("t1") && !parsedT1) setInvestigateMsg("Invalid t1 ignored.");
    else setInvestigateMsg("");
    setWindowT0(parsedT0);
    setWindowT1(parsedT1);
    if (nextPostId && nextPostId !== postId) setPostId(nextPostId);
    if (nextQuery !== q) setQ(nextQuery);
    if (nextCluster !== clusterFilter) setClusterFilter(nextCluster);
    if (nextAuthor !== authorFilter) setAuthorFilter(nextAuthor);
    if (nextCasebookOnly !== casebookOnly) setCasebookOnly(nextCasebookOnly);
  }, [authorFilter, casebookOnly, clusterFilter, postId, q, searchParams]);

  const refreshCasebook = async (targetPostId: string): Promise<CasebookItem[]> => {
    if (!targetPostId) {
      setCasebookItems([]);
      setCasebookCount(0);
      return [];
    }
    const res = await api.listCasebook({ post_id: targetPostId, limit: 200 });
    const rows = res.items || [];
    setCasebookItems(rows);
    setCasebookCount(rows.length);
    return rows;
  };

  useEffect(() => {
    const onCloseMenu = () => setMenuState(null);
    window.addEventListener("click", onCloseMenu);
    return () => window.removeEventListener("click", onCloseMenu);
  }, []);

  useEffect(() => {
    if (!postId) return;
    refreshCasebook(postId).catch(() => {
      setCasebookItems([]);
      setCasebookCount(0);
    });
  }, [postId]);

  useEffect(() => {
    let alive = true;
    setSyncing(Boolean(initial));
    setLoadingPosts(!(posts.length > 0));
    api
      .getPosts()
      .then((rows) => {
        if (!alive) return;
        setPosts(rows);
        localStorage.setItem("dl.cache.posts", JSON.stringify(rows));
        const nextPostId = postId || initial?.data.postId || rows[0]?.id || "";
        if (nextPostId) setPostId(nextPostId);
        writeRouteSnapshot<LibrarySnapshot>(CACHE_KEY, {
          posts: rows,
          postId: nextPostId,
          clusters,
          claims,
          evidence,
        });
        setError("");
        setDegraded(false);
      })
      .catch((e) => {
        if (!alive) return;
        try {
          const cached = JSON.parse(localStorage.getItem("dl.cache.posts") || "[]") as PostItem[];
          if (cached.length) {
            setPosts(cached);
            if (cached[0]?.id) setPostId(cached[0].id);
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
    if (!postId) return;
    let alive = true;
    setLoadingData(!(clusters.length > 0 || claims.length > 0 || evidence.length > 0));
    setCommentsTotal(null);
    setSyncing(true);
    Promise.all([api.getClusters(postId), api.getClaims(postId), api.getEvidence(postId), api.getCommentsByPost(postId, { limit: 300, sort: "time" })])
      .then(([clu, clm, ev, commentsResp]) => {
        if (!alive) return;
        setClusters(clu.clusters || []);
        setClaims(clm.claims || []);
        setEvidence(ev.items || []);
        setComments(commentsResp.items || []);
        setCommentsTotal(typeof commentsResp.total === "number" ? commentsResp.total : null);
        setSelectedEvidenceKeys([]);
        writeRouteSnapshot<LibrarySnapshot>(CACHE_KEY, {
          posts,
          postId,
          clusters: clu.clusters || [],
          claims: clm.claims || [],
          evidence: ev.items || [],
        });
        setError("");
        setDegraded(false);
      })
      .catch((e) => {
        if (!alive) return;
        setCommentsTotal(null);
        setError(compactErrorMessage(e instanceof Error ? e.message : String(e)));
        if (isDegradedApiError(e)) setDegraded(true);
      })
      .finally(() => {
        if (!alive) return;
        setLoadingData(false);
        setSyncing(false);
      });
    return () => {
      alive = false;
    };
  }, [postId]);

  const selectedPost = posts.find((p) => p.id === postId);
  const groups = useMemo(() => buildEvidenceGroups(claims, evidence), [claims, evidence]);
  const casebookCommentIds = useMemo(() => {
    const ids = new Set<string>();
    for (const item of casebookItems) {
      if (!item?.comment_id) continue;
      if (windowT0 && windowT1 && (item.bucket.t0 !== windowT0 || item.bucket.t1 !== windowT1)) continue;
      ids.add(String(item.comment_id));
    }
    return ids;
  }, [casebookItems, windowT0, windowT1]);
  const investigateComments = useMemo(() => {
    if (!casebookOnly) return comments;
    if (!casebookCommentIds.size) return [];
    return comments.filter((comment) => casebookCommentIds.has(String(comment.id)));
  }, [casebookCommentIds, casebookOnly, comments]);
  const selectedWindowLabel = useMemo(() => {
    if (!windowT0 || !windowT1) return "No time window";
    return `${windowT0.slice(11, 16)} - ${windowT1.slice(11, 16)}`;
  }, [windowT0, windowT1]);

  const filteredGroups = useMemo(() => {
    return groups.filter((g) => {
      if (clusterFilter !== "all") {
        const target = Number(clusterFilter);
        if (!g.clusterKeys.includes(target)) return false;
      }
      if (q.trim()) {
        const hay = `${g.representativeEvidence.text || ""} ${g.mergedClaims.map((c) => c.text).join(" ")}`.toLowerCase();
        if (!hay.includes(q.toLowerCase())) return false;
      }
      if (claimFilter !== "all") {
        if (!g.mergedClaims.some((c) => c.status === claimFilter)) return false;
      }
      return true;
    });
  }, [groups, clusterFilter, q, claimFilter]);

  const activeEvidenceKeys = useMemo(() => {
    if (selectedEvidenceKeys.length) return selectedEvidenceKeys;
    return filteredGroups.slice(0, 3).map((g) => g.key);
  }, [selectedEvidenceKeys, filteredGroups]);

  const activeGroups = useMemo(() => {
    const set = new Set(activeEvidenceKeys);
    return filteredGroups.filter((g) => set.has(g.key));
  }, [activeEvidenceKeys, filteredGroups]);

  const evidenceMap = useMemo(() => {
    const claimIndex = new Map<string, { id: string; text: string; status: string }>();
    const edges: Array<{ from: string; to: string }> = [];

    for (const g of activeGroups) {
      for (const c of g.mergedClaims) {
        const claimId = normalizeForDedupe(c.text) || `${g.key}-${c.status}`;
        if (!claimIndex.has(claimId)) {
          claimIndex.set(claimId, { id: claimId, text: c.text, status: c.status });
        }
        edges.push({ from: g.key, to: claimId });
      }
    }

    return { claims: Array.from(claimIndex.values()), edges };
  }, [activeGroups]);

  const mapHeight = Math.max(360, Math.max(activeGroups.length, evidenceMap.claims.length) * 92);
  const dedupRemoved = Math.max(0, claims.length - groups.length);
  const skeletonOnly = loadingPosts && !posts.length;

  const saveCasebookItem = async (comment: CommentItem) => {
    setMenuState(null);
    try {
      if (!postId) {
        setInvestigateMsg("No post selected for casebook.");
        return;
      }
      const payload = buildCasebookSnapshot({
        postId,
        comment,
        comments,
        commentsTotal,
        windowT0,
        windowT1,
        filters: {
          author: authorFilter.trim() || null,
          cluster_key: clusterFilter === "all" ? null : Number(clusterFilter),
          query: q.trim() || null,
          sort: "time_desc",
        },
      });
      await api.createCasebookEntry(payload);
      const rows = await refreshCasebook(postId);
      setInvestigateMsg(
        `Saved to Casebook · ${renderCasebookSummary({
          id: rows[0]?.id || "latest",
          ...payload,
          created_at: rows[0]?.created_at || null,
        })[0]}`
      );
    } catch (e) {
      setInvestigateMsg(compactErrorMessage(e instanceof Error ? e.message : String(e)));
    }
  };

  const exportCasebookJson = async () => {
    let rows = casebookItems;
    try {
      rows = await refreshCasebook(postId);
    } catch {
      // keep current snapshot
    }
    if (!rows.length) {
      setInvestigateMsg("Casebook is empty.");
      return;
    }
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    downloadTextFile(`casebook-${stamp}.json`, casebookToJson(rows), "application/json;charset=utf-8");
    setInvestigateMsg(`Exported JSON (${rows.length})`);
  };

  const exportCasebookCsv = async () => {
    let rows = casebookItems;
    try {
      rows = await refreshCasebook(postId);
    } catch {
      // keep current snapshot
    }
    if (!rows.length) {
      setInvestigateMsg("Casebook is empty.");
      return;
    }
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    downloadTextFile(`casebook-${stamp}.csv`, casebookToCsv(rows), "text/csv;charset=utf-8");
    setInvestigateMsg(`Exported CSV (${rows.length})`);
  };

  const toggleKey = (key: string) => {
    setSelectedEvidenceKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  };

  const highlightedKey = hoveredGroupKey || (selectedEvidenceKeys.length === 1 ? selectedEvidenceKeys[0] : "");
  const inspectorGroup = useMemo(() => filteredGroups.find((g) => g.key === inspectorKey) || null, [filteredGroups, inspectorKey]);

  useEffect(() => {
    if (inspectorKey && !filteredGroups.some((g) => g.key === inspectorKey)) {
      setInspectorKey("");
    }
  }, [filteredGroups, inspectorKey]);

  useEffect(() => {
    setCurrentPost(
      selectedPost
        ? {
            id: selectedPost.id,
            snippet: selectedPost.snippet || "-",
            url: selectedPost.url || null,
          }
        : null
    );
  }, [selectedPost, setCurrentPost]);

  return (
    <div className="page-grid">
      <PageHeader
        title="Evidence Library"
        subtitle="Audit-ready evidence graph with claim linkage, quality scoring, and view-level forensic control."
        actions={
          <>
            <button type="button" className="chip-btn motion-btn" onClick={() => setScopeModalOpen(true)}>
              Scope Preview
            </button>
            <Link to="/insights" className="chip-btn motion-btn">
              Open Insights
            </Link>
            <Link to="/review" className="chip-btn motion-btn">
              Open Review
            </Link>
            <span className="chip">Casebook {fmtNumber(casebookCount)}</span>
          </>
        }
      />

      {degraded ? <div className="degraded-banner">Evidence 服務暫時降級，已顯示最近可用快照；你仍可繼續篩選與檢視。</div> : null}
      {syncing ? <div className="sync-banner">同步 Evidence 資料中…</div> : null}
      {error ? <div className="error-banner compact">{error}</div> : null}
      {investigateMsg ? <div className="ok-banner">{investigateMsg}</div> : null}

      <SectionCard title="Library Scope">
        {skeletonOnly ? (
          <div className="skeleton-stack">
            <div className="skeleton-card" />
            <div className="skeleton-card" />
          </div>
        ) : (
          <>
            <div className="library-scope-head">
              <select
                className="text-input"
                value={postId}
                onChange={(e) => {
                  const nextPost = e.target.value;
                  setPostId(nextPost);
                  updateInvestigateQuery({ post_id: nextPost || null });
                }}
              >
                {posts.map((p) => (
                  <option key={p.id} value={p.id}>{toShort(p.snippet || `Post #${p.id}`)}</option>
                ))}
              </select>
              <div className="view-switch">
                {(["cards", "table", "timeline"] as ViewMode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className={`view-pill motion-btn ${viewMode === mode ? "active" : ""}`}
                    onClick={() => setViewMode(mode)}
                  >
                    {mode}
                  </button>
                ))}
              </div>
            </div>
            <button type="button" className="scope-mini-card motion-btn" onClick={() => setScopeModalOpen(true)}>
              <div className="scope-mini-title">{selectedPost?.author || "unknown"}</div>
              <div className="scope-mini-text">{toShort(selectedPost?.snippet || "No post selected", 220)}</div>
            </button>
          </>
        )}
      </SectionCard>

      <div className="metrics-grid four">
        <MetricCard label="Clusters" value={clusters.length} />
        <MetricCard label="Claims (raw)" value={claims.length} />
        <MetricCard label="Claims (deduped)" value={groups.length} hint={`Removed duplicates: ${dedupRemoved}`} />
        <MetricCard label="Evidence" value={evidence.length} />
      </div>

      <SectionCard title="Investigate">
        <div className="investigate-head-row">
          <div className="investigate-filters">
            <input
              className="text-input"
              value={authorFilter}
              onChange={(e) => {
                const next = e.target.value;
                setAuthorFilter(next);
                updateInvestigateQuery({ author: next || null });
              }}
              placeholder="author filter (@handle)"
            />
            <input
              className="text-input"
              value={q}
              onChange={(e) => {
                const next = e.target.value;
                setQ(next);
                updateInvestigateQuery({ q: next || null });
              }}
              placeholder="search comments"
            />
          </div>
          <div className="investigate-meta">
            <span>Window {selectedWindowLabel}</span>
            <span>Casebook {casebookCount}</span>
            <button
              type="button"
              className={`chip-btn motion-btn ${casebookOnly ? "active" : ""}`}
              onClick={() => {
                const next = !casebookOnly;
                setCasebookOnly(next);
                updateInvestigateQuery({ casebookOnly: next ? true : null });
              }}
            >
              {casebookOnly ? "Casebook only: on" : "Casebook only: off"}
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => void exportCasebookJson()}>
              Export JSON
            </button>
            <button type="button" className="chip-btn motion-btn" onClick={() => void exportCasebookCsv()}>
              Export CSV
            </button>
          </div>
        </div>
        <CommentMomentumPanel
          comments={investigateComments}
          loading={loadingData}
          degraded={degraded}
          t0={windowT0}
          t1={windowT1}
          q={q}
          author={authorFilter}
          onCommentContext={(comment, point) => setMenuState({ x: point.x, y: point.y, comment })}
        />
        {casebookOnly && !investigateComments.length ? (
          <div className="row-sub">No casebook-linked comments in current window.</div>
        ) : null}
        {casebookItems[0] ? (
          <div className="casebook-summary-snapshot">
            <div className="ev-kicker">Latest Casebook Snapshot</div>
            {renderCasebookSummary(casebookItems[0]).map((line) => (
              <div key={line} className="row-sub">
                {line}
              </div>
            ))}
          </div>
        ) : null}
      </SectionCard>

      <SectionCard title="Evidence Bank">
        <div className="filters-row">
          <select
            className="text-input"
            value={clusterFilter}
            onChange={(e) => {
              const next = e.target.value;
              setClusterFilter(next);
              updateInvestigateQuery({ cluster_key: next === "all" ? null : next });
            }}
          >
            <option value="all">All clusters</option>
            {clusters.map((c) => (
              <option key={c.cluster_key} value={String(c.cluster_key)}>{c.label || `C${c.cluster_key}`}</option>
            ))}
          </select>
          <select className="text-input" value={claimFilter} onChange={(e) => setClaimFilter(e.target.value)}>
            <option value="all">All claims</option>
            <option value="audited">audited</option>
            <option value="hypothesis">hypothesis</option>
            <option value="dropped">dropped</option>
          </select>
          <input
            className="text-input"
            value={q}
            onChange={(e) => {
              const next = e.target.value;
              setQ(next);
              updateInvestigateQuery({ q: next || null });
            }}
            placeholder="Search evidence / claims"
          />
        </div>

        <div className="evidence-split">
          <div className="evidence-left-col">
            {loadingData && !filteredGroups.length ? (
              <div className="skeleton-stack">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={`lib-left-sk-${i}`} className="skeleton-card" />
                ))}
              </div>
            ) : null}
            {viewMode === "cards" ? (
              <div className="evidence-grid gallery">
                {filteredGroups.map((g) => {
                  const quality = qualityForGroup(g);
                  const active = activeEvidenceKeys.includes(g.key);
                    return (
                    <article
                      key={g.key}
                      className={`evidence-card full evidence-group-card selectable ${active ? "selected" : ""}`}
                      onMouseEnter={() => setHoveredGroupKey(g.key)}
                      onMouseLeave={() => setHoveredGroupKey("")}
                      onClick={() => setInspectorKey(g.key)}
                    >
                      <div className="gallery-thumb">
                        <span>Evidence Asset</span>
                        <strong className="metric-number-inline">#{shortToken(g.evidenceIds[0] || g.representativeEvidence.id, 8)}</strong>
                      </div>
                      <div className="ev-top-row">
                        <label className="ev-select">
                          <input type="checkbox" checked={active} onChange={() => toggleKey(g.key)} />
                          <span>Map</span>
                        </label>
                        <span className={`quality-tag ${quality.label}`}>quality {quality.label}</span>
                      </div>
                      <div className="ev-kicker">Evidence</div>
                      <p className="ev-text">{g.representativeEvidence.text || "-"}</p>
                      <div className="ev-meta">{g.representativeEvidence.author_handle || "unknown"} · {fmtNumber(g.representativeEvidence.like_count)} likes · refs {g.rawEvidenceCount}</div>
                      <div className="badge-row">
                        <span className="pill">clusters {g.clusterKeys.join(", ") || "-"}</span>
                        <span className="pill">merged claims {g.mergedClaims.length}</span>
                      </div>
                      <div className="merged-claims-list">
                        {g.mergedClaims.map((mc, i) => (
                          <article key={`${g.key}-${i}`} className="claim-chip-card">
                            <div className="claim-status">{mc.status}</div>
                            <div>{mc.text}</div>
                          </article>
                        ))}
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : null}

            {viewMode === "table" ? (
              <div className="evidence-table-wrap">
                <table className="evidence-table">
                  <thead>
                    <tr>
                      <th>Map</th>
                      <th>Evidence</th>
                      <th>Likes</th>
                      <th>Clusters</th>
                      <th>Quality</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredGroups.map((g) => {
                      const quality = qualityForGroup(g);
                      const active = activeEvidenceKeys.includes(g.key);
                      return (
                        <tr
                          key={g.key}
                          className={active ? "active" : ""}
                          onMouseEnter={() => setHoveredGroupKey(g.key)}
                          onMouseLeave={() => setHoveredGroupKey("")}
                          onClick={() => setInspectorKey(g.key)}
                        >
                          <td><input type="checkbox" checked={active} onChange={() => toggleKey(g.key)} /></td>
                          <td>{toShort(g.representativeEvidence.text || "-", 90)}</td>
                          <td className="metric-number-inline">{fmtNumber(g.representativeEvidence.like_count)}</td>
                          <td>{g.clusterKeys.join(", ") || "-"}</td>
                          <td><span className={`quality-tag ${quality.label}`}>{quality.label}</span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : null}

            {viewMode === "timeline" ? (
              <div className="library-timeline">
                {filteredGroups.map((g) => {
                  const quality = qualityForGroup(g);
                  const active = activeEvidenceKeys.includes(g.key);
                  return (
                    <article
                      key={g.key}
                      className={`timeline-item ${active ? "active" : ""}`}
                      onMouseEnter={() => setHoveredGroupKey(g.key)}
                      onMouseLeave={() => setHoveredGroupKey("")}
                      onClick={() => setInspectorKey(g.key)}
                    >
                      <div className="timeline-dot" />
                      <div className="timeline-body">
                        <div className="ev-top-row">
                          <label className="ev-select">
                            <input type="checkbox" checked={active} onChange={() => toggleKey(g.key)} />
                            <span>{g.representativeEvidence.author_handle || "unknown"}</span>
                          </label>
                          <span className={`quality-tag ${quality.label}`}>quality {quality.label}</span>
                        </div>
                        <div className="ev-text">{g.representativeEvidence.text || "-"}</div>
                        <div className="ev-meta">{fmtNumber(g.representativeEvidence.like_count)} likes · refs {g.rawEvidenceCount}</div>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : null}

            {!filteredGroups.length && !loadingData ? <div className="empty-note">沒有符合篩選的資料。</div> : null}
          </div>

          <aside className="evidence-map-panel">
            <div className="evidence-map-head">
              <h4>Evidence Map</h4>
              <p><span className="metric-number-inline">{activeGroups.length}</span> evidence nodes · <span className="metric-number-inline">{evidenceMap.claims.length}</span> claim nodes</p>
            </div>
            {loadingData && !activeGroups.length ? (
              <div className="skeleton-stack">
                <div className="skeleton-card" />
                <div className="skeleton-card" />
              </div>
            ) : !activeGroups.length ? (
              <div className="empty-note">選取 evidence 後會顯示關聯地圖。</div>
            ) : (
              <svg className="evidence-map-canvas" viewBox={`0 0 680 ${mapHeight}`} role="img" aria-label="evidence map">
                {activeGroups.map((g, i) => {
                  const y = 70 + i * ((mapHeight - 120) / Math.max(1, activeGroups.length - 1));
                  return (
                    <g key={`n-ev-${g.key}`}>
                      <rect x={40} y={y - 26} width={250} height={52} rx={14} className={`map-node ev ${highlightedKey && highlightedKey !== g.key ? "dim" : ""}`} />
                      <text x={56} y={y - 4} className="map-node-title">E{i + 1}</text>
                      <text x={56} y={y + 14} className="map-node-sub">{toShort(g.representativeEvidence.text || "-", 34)}</text>
                    </g>
                  );
                })}

                {evidenceMap.claims.map((c, i) => {
                  const y = 70 + i * ((mapHeight - 120) / Math.max(1, evidenceMap.claims.length - 1));
                  return (
                    <g key={`n-claim-${c.id}`}>
                      <rect x={392} y={y - 24} width={250} height={48} rx={14} className="map-node claim" />
                      <text x={408} y={y - 3} className="map-node-title">{c.status}</text>
                      <text x={408} y={y + 14} className="map-node-sub">{toShort(c.text, 34)}</text>
                    </g>
                  );
                })}

                {evidenceMap.edges.map((edge, idx) => {
                  const fromIndex = activeGroups.findIndex((g) => g.key === edge.from);
                  const toIndex = evidenceMap.claims.findIndex((c) => c.id === edge.to);
                  if (fromIndex < 0 || toIndex < 0) return null;
                  const y1 = 70 + fromIndex * ((mapHeight - 120) / Math.max(1, activeGroups.length - 1));
                  const y2 = 70 + toIndex * ((mapHeight - 120) / Math.max(1, evidenceMap.claims.length - 1));
                  const dim = Boolean(highlightedKey && highlightedKey !== edge.from);
                  return <path key={`edge-${idx}`} d={`M290 ${y1} C340 ${y1}, 342 ${y2}, 392 ${y2}`} className={`map-edge ${dim ? "dim" : ""}`} />;
                })}
              </svg>
            )}
          </aside>
        </div>
      </SectionCard>

      {scopeModalOpen ? (
        <div className="scope-modal-wrap">
          <button type="button" className="scope-modal-backdrop" aria-label="close" onClick={() => setScopeModalOpen(false)} />
          <article className="scope-modal">
            <header>
              <h3>Post Scope</h3>
              <button type="button" className="chip-btn motion-btn" onClick={() => setScopeModalOpen(false)}>Close</button>
            </header>
            <p>{selectedPost?.snippet || "-"}</p>
          </article>
        </div>
      ) : null}

      {inspectorGroup ? (
        <div className="library-inspector-wrap">
          <button type="button" className="library-inspector-backdrop" aria-label="close inspector" onClick={() => setInspectorKey("")} />
          <article className="library-inspector">
            <header className="library-inspector-head">
              <div>
                <h4>Evidence Inspector</h4>
                <p>
                  clusters {inspectorGroup.clusterKeys.join(", ") || "-"} · refs{" "}
                  <span className="metric-number-inline">{inspectorGroup.rawEvidenceCount}</span>
                </p>
              </div>
              <button type="button" className="chip-btn motion-btn" onClick={() => setInspectorKey("")}>
                Close
              </button>
            </header>
            <div className="library-inspector-grid">
              <section>
                <div className="ev-kicker">Primary Evidence</div>
                <p className="ev-text">{inspectorGroup.representativeEvidence.text || "-"}</p>
                <div className="ev-meta">
                  {inspectorGroup.representativeEvidence.author_handle || "unknown"} ·{" "}
                  <span className="metric-number-inline">{fmtNumber(inspectorGroup.representativeEvidence.like_count)}</span> likes
                </div>
              </section>
              <section>
                <div className="ev-kicker">Evidence IDs</div>
                <div className="badge-row">
                  {inspectorGroup.evidenceIds.slice(0, 10).map((id) => (
                    <span key={id} className="pill metric-number-inline">{id}</span>
                  ))}
                </div>
              </section>
              <section>
                <div className="ev-kicker">Linked Claims</div>
                <div className="merged-claims-list">
                  {inspectorGroup.mergedClaims.map((mc, i) => (
                    <article key={`${inspectorGroup.key}-inspector-${i}`} className="claim-chip-card">
                      <div className="claim-status">{mc.status}</div>
                      <div>{mc.text}</div>
                    </article>
                  ))}
                </div>
              </section>
              <section>
                <div className="ev-kicker">Audit Workflow</div>
                <div className="selected-post-actions">
                  <Link to="/insights">Open Insights</Link>
                  <Link to="/review">Open Review</Link>
                  <Link to="/pipeline">Open Pipeline</Link>
                </div>
              </section>
            </div>
          </article>
        </div>
      ) : null}

      {menuState ? (
        <div className="context-menu-lite" style={{ left: menuState.x, top: menuState.y }}>
          <button type="button" onClick={() => void saveCasebookItem(menuState.comment)}>
            Save to Casebook
          </button>
          <button
            type="button"
            onClick={() => {
              setInvestigateMsg(`Ready for review: ${menuState.comment.id}`);
              setMenuState(null);
            }}
          >
            Mark for Review
          </button>
        </div>
      ) : null}
    </div>
  );
}
