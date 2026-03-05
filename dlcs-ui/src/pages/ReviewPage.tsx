import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, isDegradedApiError } from "../lib/api";
import type { AnalysisJsonResponse, ClaimsResponse, PostItem } from "../lib/types";
import { compactErrorMessage, fmtDate, fmtPct } from "../lib/format";
import { PageHeader } from "../components/PageHeader";
import { SectionCard } from "../components/SectionCard";
import { readRouteSnapshot, writeRouteSnapshot } from "../lib/routeCache";
import { useIntelligenceStore } from "../store/intelligenceStore";

type ReviewSnapshot = {
  posts: PostItem[];
  postId: string;
  analysis: AnalysisJsonResponse | null;
  claims: ClaimsResponse | null;
};

const CACHE_KEY = "dl.cache.route.review.v1";

function cleanLower(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

function toneFromStatus(status: string | undefined): "good" | "risk" | "warn" | "neutral" {
  const s = cleanLower(status);
  if (!s) return "neutral";
  if (s.includes("drop") || s.includes("reject") || s.includes("fail")) return "risk";
  if (s.includes("accept") || s.includes("stable") || s.includes("audit") || s.includes("keep")) return "good";
  if (s.includes("hypothesis") || s.includes("pending") || s.includes("unknown")) return "warn";
  return "neutral";
}

export function ReviewPage() {
  const navigate = useNavigate();
  const setCurrentPost = useIntelligenceStore((s) => s.setCurrentPost);
  const setStabilityVerdict = useIntelligenceStore((s) => s.setStabilityVerdict);
  const initial = readRouteSnapshot<ReviewSnapshot>(CACHE_KEY);
  const [posts, setPosts] = useState<PostItem[]>(initial?.data.posts || []);
  const [postId, setPostId] = useState(initial?.data.postId || "");
  const [analysis, setAnalysis] = useState<AnalysisJsonResponse | null>(initial?.data.analysis || null);
  const [claims, setClaims] = useState<ClaimsResponse | null>(initial?.data.claims || null);
  const [commentId, setCommentId] = useState("");
  const [notes, setNotes] = useState("");
  const [decision, setDecision] = useState("accept");
  const [statusFilter, setStatusFilter] = useState("all");
  const [claimQuery, setClaimQuery] = useState("");
  const [selectedClaimId, setSelectedClaimId] = useState(initial?.data.claims?.claims?.[0]?.id || "");
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [degraded, setDegraded] = useState(false);
  const [syncing, setSyncing] = useState(Boolean(initial));
  const [loading, setLoading] = useState(!(initial?.data.posts?.length || initial?.data.claims));

  useEffect(() => {
    let alive = true;
    setLoading(!(posts.length > 0));
    setSyncing(true);
    api
      .getPosts()
      .then((rows) => {
        if (!alive) return;
        setPosts(rows);
        const nextPostId = postId || initial?.data.postId || rows[0]?.id || "";
        if (nextPostId) setPostId(nextPostId);
        writeRouteSnapshot<ReviewSnapshot>(CACHE_KEY, {
          posts: rows,
          postId: nextPostId,
          analysis,
          claims,
        });
        setDegraded(false);
      })
      .catch((e) => {
        if (!alive) return;
        try {
          const cached = JSON.parse(localStorage.getItem("dl.cache.posts") || "[]") as PostItem[];
          if (cached.length) {
            setPosts(cached);
            const fallbackPost = postId || initial?.data.postId || cached[0]?.id || "";
            if (fallbackPost) setPostId(fallbackPost);
            setError("即時資料暫不可用，已使用快取資料。");
            return;
          }
        } catch {
          // ignore cache parse errors
        }
        setError(compactErrorMessage(e instanceof Error ? e.message : String(e)));
        if (isDegradedApiError(e)) setDegraded(true);
      })
      .finally(() => {
        if (!alive) return;
        setLoading(false);
        setSyncing(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!postId) return;
    let alive = true;
    setSyncing(true);
    Promise.allSettled([api.getAnalysisJson(postId), api.getClaims(postId)]).then(([analysisRes, claimsRes]) => {
      if (!alive) return;
      const a = analysisRes.status === "fulfilled" ? analysisRes.value : null;
      const c = claimsRes.status === "fulfilled" ? claimsRes.value : null;
      setAnalysis(a);
      setClaims(c);
      if (c?.claims?.[0]?.id) {
        setSelectedClaimId((prev) => {
          if (prev && c.claims.some((x) => x.id === prev)) return prev;
          return c.claims[0].id;
        });
      }
      writeRouteSnapshot<ReviewSnapshot>(CACHE_KEY, {
        posts,
        postId,
        analysis: a,
        claims: c,
      });
      const degradedDetected = (analysisRes.status === "rejected" && isDegradedApiError(analysisRes.reason))
        || (claimsRes.status === "rejected" && isDegradedApiError(claimsRes.reason));
      if (degradedDetected) setDegraded(true);
      if (analysisRes.status === "rejected" || claimsRes.status === "rejected") {
        const analysisErr = analysisRes.status === "rejected" ? compactErrorMessage(String(analysisRes.reason)) : "";
        const claimsErr = claimsRes.status === "rejected" ? compactErrorMessage(String(claimsRes.reason)) : "";
        const merged = [analysisErr, claimsErr].filter(Boolean).join(" | ");
        if (merged) setError(merged);
      }
      setSyncing(false);
    });
    return () => {
      alive = false;
    };
  }, [postId]);

  const claimsList = claims?.claims || [];
  const statusOptions = useMemo(() => {
    const uniq = new Set<string>();
    for (const c of claimsList) {
      const s = String(c.status || "unknown").trim().toLowerCase();
      if (s) uniq.add(s);
    }
    return Array.from(uniq.values()).sort();
  }, [claimsList]);
  const filteredClaims = useMemo(() => {
    const q = cleanLower(claimQuery);
    const wantedStatus = cleanLower(statusFilter);
    return claimsList.filter((c) => {
      const status = cleanLower(c.status || "unknown");
      if (wantedStatus !== "all" && status !== wantedStatus) return false;
      if (!q) return true;
      return cleanLower(c.text).includes(q) || cleanLower(c.id).includes(q) || String(c.cluster_key ?? c.primary_cluster_key ?? "").includes(q);
    });
  }, [claimQuery, claimsList, statusFilter]);
  const selectedClaim =
    filteredClaims.find((c) => c.id === selectedClaimId)
    || claimsList.find((c) => c.id === selectedClaimId)
    || filteredClaims[0]
    || claimsList[0]
    || null;
  const bundleId = useMemo(() => {
    const root = (analysis?.analysis_json || {}) as Record<string, unknown>;
    const meta = (root.meta || {}) as Record<string, unknown>;
    return typeof meta.bundle_id === "string" ? meta.bundle_id : "";
  }, [analysis]);
  const verdict = claims?.audit?.verdict || "-";
  const keptCount = Number(claims?.audit?.kept_claims_count || 0);
  const droppedCount = Number(claims?.audit?.dropped_claims_count || 0);
  const totalCount = Number(claims?.audit?.total_claims_count || claimsList.length || 0);
  const coverageRatio = totalCount > 0 ? keptCount / totalCount : 0;
  const densityRatio = totalCount > 0 ? filteredClaims.length / totalCount : 0;

  useEffect(() => {
    if (!filteredClaims.length) return;
    if (!selectedClaimId || !filteredClaims.some((c) => c.id === selectedClaimId)) {
      setSelectedClaimId(filteredClaims[0].id);
    }
  }, [filteredClaims, selectedClaimId]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setMsg("");
    if (!analysis) {
      setError("缺少 analysis_json，不能送審。 ");
      return;
    }
    if (!bundleId) {
      setError("analysis_json.meta.bundle_id 不存在。 ");
      return;
    }
    if (!commentId.trim()) {
      setError("comment_id 必填。");
      return;
    }

    try {
      setSubmitting(true);
      const clusterKey = selectedClaim?.primary_cluster_key ?? selectedClaim?.cluster_key ?? null;
      await api.submitReview({
        post_id: postId,
        bundle_id: bundleId,
        analysis_build_id: null,
        label_type: "other",
        schema_version: "v1",
        decision: { verdict: decision, claim_id: selectedClaim?.id || null },
        comment_id: commentId.trim(),
        cluster_key: typeof clusterKey === "number" ? clusterKey : null,
        notes: notes.trim() || null,
      });
      setMsg("review 已記錄");
      setNotes("");
      setCommentId("");
    } catch (e) {
      setError(compactErrorMessage(e instanceof Error ? e.message : String(e)));
    } finally {
      setSubmitting(false);
    }
  };

  const audit = claims?.audit;
  const selectedPost = posts.find((p) => p.id === postId);

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
    setStabilityVerdict(audit?.verdict || "-");
  }, [audit?.verdict, selectedPost, setCurrentPost, setStabilityVerdict]);

  return (
    <div className="page-grid">
      <PageHeader
        title="Review Console"
        subtitle="人工審核 claims 輸出，並回寫 review 記錄。"
        actions={
          <>
            <Link to="/insights" className="chip-btn motion-btn">
              Open Insights
            </Link>
            <Link to="/library" className="chip-btn motion-btn">
              Open Library
            </Link>
            <button type="button" className="chip-btn motion-btn" onClick={() => navigate("/insights")}>
              Compare Cluster
            </button>
            <span className="chip">Verdict {verdict}</span>
            <span className="chip">Claims {claimsList.length}</span>
          </>
        }
      />

      {degraded ? <div className="degraded-banner">Review 來源暫時降級，已顯示最近快照；可繼續導航與提交。</div> : null}
      {syncing ? <div className="sync-banner">同步 Review 資料中…</div> : null}
      {error ? <div className="error-banner">{error}</div> : null}
      {msg ? <div className="ok-banner">{msg}</div> : null}

      <SectionCard title="Audit Snapshot">
        {loading && !posts.length ? (
          <div className="skeleton-stack">
            <div className="skeleton-card" />
            <div className="skeleton-card" />
          </div>
        ) : (
          <div className="review-snapshot-v2">
            <div className="review-top-grid">
              <div className="review-post-picker">
                <label className="input-label">Target Post</label>
                <select className="text-input" value={postId} onChange={(e) => setPostId(e.target.value)}>
                  {posts.map((p) => (
                    <option key={p.id} value={p.id}>{p.snippet || `Post #${p.id}`}</option>
                  ))}
                </select>
                <p className="row-sub">{selectedPost?.snippet || "No post selected."}</p>
                <div className="badge-row">
                  <span className="pill metric-number-inline">post #{selectedPost?.id?.slice(0, 8) || "-"}</span>
                  <span className="pill">bundle {bundleId ? bundleId.slice(0, 12) : "missing"}</span>
                </div>
              </div>
              <div className="review-audit-cards">
                <article className="review-mini-card">
                  <div className="review-mini-kicker">verdict</div>
                  <div className="review-mini-value">{verdict}</div>
                </article>
                <article className="review-mini-card">
                  <div className="review-mini-kicker">kept / dropped</div>
                  <div className="review-mini-value metric-number-inline">{keptCount} / {droppedCount}</div>
                </article>
                <article className="review-mini-card">
                  <div className="review-mini-kicker">coverage</div>
                  <div className="review-mini-value">{fmtPct(coverageRatio)}</div>
                </article>
                <article className="review-mini-card">
                  <div className="review-mini-kicker">audit time</div>
                  <div className="review-mini-value review-mini-time">{fmtDate(audit?.created_at || null)}</div>
                </article>
              </div>
            </div>
            {!bundleId ? (
              <div className="review-warning">analysis_json.meta.bundle_id 缺失，送審會被擋下。</div>
            ) : null}
          </div>
        )}
      </SectionCard>

      <div className="review-workbench">
        <SectionCard title="Claims Queue" action={<span className="chip">Filtered {filteredClaims.length}/{claimsList.length}</span>}>
          <div className="review-filter-row">
            <select className="text-input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">all status</option>
              {statusOptions.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <input
              className="text-input"
              value={claimQuery}
              onChange={(e) => setClaimQuery(e.target.value)}
              placeholder="search claim text / id / cluster"
            />
          </div>
          {loading && !claims?.claims?.length ? (
            <div className="skeleton-stack">
              {Array.from({ length: 7 }).map((_, i) => (
                <div key={`review-claim-sk-${i}`} className="skeleton-card" />
              ))}
            </div>
          ) : (
            <div className="review-claims-list">
              {filteredClaims.map((c) => {
                const active = selectedClaim?.id === c.id;
                const tone = toneFromStatus(c.status);
                return (
                  <button
                    key={c.id}
                    type="button"
                    className={`row-item selectable review-claim-row ${active ? "selected" : ""}`}
                    onClick={() => setSelectedClaimId(c.id)}
                  >
                    <div>
                      <div className="row-title">{c.text}</div>
                      <div className="row-sub">
                        <span className={`status-pill review-state ${tone}`}>{c.status || "unknown"}</span>
                        {" · "}cluster {c.primary_cluster_key ?? c.cluster_key ?? "-"}
                        {" · "}confidence {typeof c.confidence === "number" ? fmtPct(c.confidence) : "-"}
                      </div>
                    </div>
                    <div className="row-tail">
                      <span className="pill metric-number-inline">#{c.id.slice(0, 8)}</span>
                    </div>
                  </button>
                );
              })}
              {!filteredClaims.length ? <div className="empty-note">暫無符合篩選的 claims。</div> : null}
            </div>
          )}
        </SectionCard>

        <SectionCard title="Claim Inspector">
          {selectedClaim ? (
            <div className="review-inspector">
              <div className="review-inspector-head">
                <div className="ev-kicker">selected claim</div>
                <span className={`status-pill review-state ${toneFromStatus(selectedClaim.status)}`}>{selectedClaim.status || "unknown"}</span>
              </div>
              <p className="ev-text">{selectedClaim.text}</p>
              <div className="review-kv-grid">
                <div className="review-kv">
                  <span>claim id</span>
                  <strong className="metric-number-inline">{selectedClaim.id}</strong>
                </div>
                <div className="review-kv">
                  <span>cluster</span>
                  <strong>{selectedClaim.primary_cluster_key ?? selectedClaim.cluster_key ?? "-"}</strong>
                </div>
                <div className="review-kv">
                  <span>confidence</span>
                  <strong>{typeof selectedClaim.confidence === "number" ? fmtPct(selectedClaim.confidence) : "-"}</strong>
                </div>
                <div className="review-kv">
                  <span>type / scope</span>
                  <strong>{selectedClaim.claim_type || "-"} · {selectedClaim.scope || "-"}</strong>
                </div>
                <div className="review-kv">
                  <span>audit verdict</span>
                  <strong>{verdict}</strong>
                </div>
                <div className="review-kv">
                  <span>density</span>
                  <strong>{fmtPct(densityRatio)}</strong>
                </div>
              </div>
              <div className="badge-row">
                <span className="pill">kept {keptCount}</span>
                <span className="pill">dropped {droppedCount}</span>
                <span className="pill">total {totalCount}</span>
              </div>
            </div>
          ) : (
            <div className="empty-note">尚未選取 claim。</div>
          )}
        </SectionCard>

        <SectionCard title="Submit Review">
          <form className="stack" onSubmit={onSubmit}>
            <label className="input-label">Comment ID</label>
            <input className="text-input" value={commentId} onChange={(e) => setCommentId(e.target.value)} placeholder="threads_comments.id" />
            <label className="input-label">Decision</label>
            <select className="text-input" value={decision} onChange={(e) => setDecision(e.target.value)}>
              <option value="accept">accept</option>
              <option value="reject">reject</option>
              <option value="needs_more_evidence">needs_more_evidence</option>
            </select>
            <label className="input-label">Notes</label>
            <textarea className="text-area" value={notes} onChange={(e) => setNotes(e.target.value)} rows={5} />
            <div className="badge-row">
              <span className="pill">bundle {bundleId ? bundleId.slice(0, 12) : "-"}</span>
              <span className="pill">claim {selectedClaim?.id ? `#${selectedClaim.id.slice(0, 8)}` : "-"}</span>
            </div>
            <button className={`primary-btn ${submitting ? "loading" : ""}`} type="submit" disabled={submitting || !postId}>
              {submitting ? "Submitting..." : "Submit Review"}
            </button>
            <p className="helper-text">
              缺少 comment_id 或 bundle_id 會阻止送審；其餘未實作欄位暫保留於畫面與 payload 介面。
            </p>
          </form>
        </SectionCard>
      </div>

      <SectionCard title="Review Pipeline Hooks">
        <div className="review-hooks-grid">
          <article className="review-hook-card">
            <div className="ev-kicker">cache handshake</div>
            <p className="row-sub">
              route cache: <span className="metric-number-inline">{CACHE_KEY}</span>
            </p>
            <p className="row-sub">切頁時保留 post / claims / analysis，維持 seamless toggle。</p>
          </article>
          <article className="review-hook-card">
            <div className="ev-kicker">api guard</div>
            <p className="row-sub">degraded fallback + local cache posts，避免整頁失效。</p>
            <p className="row-sub">submit path: /api/reviews</p>
          </article>
          <article className="review-hook-card">
            <div className="ev-kicker">next backfill slots</div>
            <div className="badge-row">
              <span className="pill">review history list</span>
              <span className="pill">claim-level evidence trace</span>
              <span className="pill">auto comment lookup</span>
            </div>
          </article>
        </div>
      </SectionCard>
    </div>
  );
}
