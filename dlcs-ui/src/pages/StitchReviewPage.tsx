import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import templateHtml from "../stitch/evidence_archive_console.html?raw";
import { StitchTemplateFrame, type StitchActionMeta, type StitchNotice } from "../components/StitchTemplateFrame";
import { api, isDegradedApiError } from "../lib/api";
import type { ClaimItem, CommentItem, EvidenceItem, PhenomenonDetail, PostItem } from "../lib/types";

const actionMap = {
  "load more evidence": "review_load_more",
  "run deep analysis": "review_run_deep_analysis",
  share: "review_share",
  report: "review_report",
};

type ReviewCard = {
  id: string;
  tag: string;
  title: string;
  summary: string;
  risk: string;
  reliability: string;
  engagement: string;
  dotClass: string;
  item: EvidenceItem | CommentItem;
  claimId?: string;
};

function makeNotice(message: string, kind: "info" | "ok" | "error" = "info"): StitchNotice {
  return { message, kind, nonce: Date.now() + Math.floor(Math.random() * 1000) };
}

function riskLabel(status: string | undefined): string {
  const s = String(status || "").toLowerCase();
  if (s.includes("drop") || s.includes("fail")) return "Critical";
  if (s.includes("hypothesis") || s.includes("pending")) return "Med";
  if (s.includes("audited") || s.includes("stable") || s.includes("accept")) return "Low";
  return "Unknown";
}

function statusDotClass(status: string | undefined): string {
  const s = String(status || "").toLowerCase();
  if (s.includes("drop") || s.includes("fail")) return "bg-status-red";
  if (s.includes("hypothesis") || s.includes("pending")) return "bg-status-amber";
  if (s.includes("audited") || s.includes("stable") || s.includes("accept")) return "bg-status-green";
  return "bg-status-cyan";
}

function fmtIso(ts: string | null | undefined): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (!Number.isFinite(d.getTime())) return "-";
  return d.toISOString().slice(0, 19) + "Z";
}

function trimLine(value: string, size = 110): string {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length > size ? `${text.slice(0, size - 1)}…` : text;
}

function byteSizeLabel(text: string): string {
  const bytes = new TextEncoder().encode(String(text || "")).length;
  if (bytes > 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes > 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

export function StitchReviewPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const phenomenonId = searchParams.get("phenomenon_id") || "";
  const queryPostId = searchParams.get("post_id") || "";
  const queryClusterKey = searchParams.get("cluster_key") || "";
  const queryCommentId = searchParams.get("comment_id") || "";
  const queryEvidenceId = searchParams.get("evidence_id") || "";

  const [posts, setPosts] = useState<PostItem[]>([]);
  const [postId, setPostId] = useState("");
  const [phenomenon, setPhenomenon] = useState<PhenomenonDetail | null>(null);
  const [claims, setClaims] = useState<ClaimItem[]>([]);
  const [evidence, setEvidence] = useState<EvidenceItem[]>([]);
  const [comments, setComments] = useState<CommentItem[]>([]);
  const [visibleCount, setVisibleCount] = useState(6);
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState<StitchNotice | null>(null);

  const loadPosts = useCallback(async () => {
    try {
      const rows = await api.getPosts();
      setPosts(rows || []);
      if (!postId) setPostId(queryPostId || rows[0]?.id || "");
    } catch (e) {
      setNotice(makeNotice(`Review posts failed: ${e instanceof Error ? e.message : String(e)}`, "error"));
    }
  }, [postId, queryPostId]);

  const loadPhenomenon = useCallback(async () => {
    if (!phenomenonId) {
      setPhenomenon(null);
      return;
    }
    try {
      const detail = await api.getPhenomenon(phenomenonId, 10);
      setPhenomenon(detail);
      const fromPhenomenon = detail.recent_posts?.[0]?.id;
      if (fromPhenomenon) setPostId(String(fromPhenomenon));
    } catch {
      setPhenomenon(null);
    }
  }, [phenomenonId]);

  const loadEvidence = useCallback(async () => {
    if (!postId) {
      setClaims([]);
      setEvidence([]);
      setComments([]);
      return;
    }
    try {
      const [claimsRes, evidenceRes, commentsRes] = await Promise.all([
        api.getClaims(postId),
        api.getEvidence(postId),
        api.getCommentsByPost(postId, { limit: 160, sort: "time" }),
      ]);
      setClaims(claimsRes.claims || []);
      setEvidence(evidenceRes.items || []);
      setComments(commentsRes.items || []);
    } catch (e) {
      if (isDegradedApiError(e)) {
        setNotice(makeNotice("Review source degraded; showing partial evidence.", "info"));
      } else {
        setNotice(makeNotice(`Review sync failed: ${e instanceof Error ? e.message : String(e)}`, "error"));
      }
    }
  }, [postId]);

  useEffect(() => {
    void loadPosts();
    void loadPhenomenon();
    const timer = window.setInterval(() => {
      void loadPosts();
      void loadPhenomenon();
    }, 12000);
    return () => window.clearInterval(timer);
  }, [loadPhenomenon, loadPosts]);

  useEffect(() => {
    void loadEvidence();
    if (!postId) return;
    const timer = window.setInterval(() => void loadEvidence(), 8000);
    return () => window.clearInterval(timer);
  }, [loadEvidence, postId]);

  const claimById = useMemo(() => {
    const out = new Map<string, ClaimItem>();
    claims.forEach((c) => out.set(String(c.id), c));
    return out;
  }, [claims]);

  const cards = useMemo<ReviewCard[]>(() => {
    if (!evidence.length && !comments.length) return [];
    const clusterFilter = queryClusterKey ? Number(queryClusterKey) : null;
    const isClusterMatch = (value: unknown) =>
      clusterFilter == null || (Number.isFinite(Number(value)) && Number(value) === clusterFilter);

    const fromEvidence = evidence.filter((item) => isClusterMatch(item.cluster_key)).map((item) => {
      const claim = item.claim_id ? claimById.get(String(item.claim_id)) : null;
      const title = trimLine(item.claim_text || claim?.text || item.text || "Evidence");
      const summary = trimLine(item.text || claim?.text || "", 140);
      const tag = item.evidence_id ? `#${String(item.evidence_id).slice(0, 8)}` : `#POST-${postId}`;
      const risk = riskLabel(item.claim_status || claim?.status);
      return {
        id: String(item.id),
        tag,
        title,
        summary,
        risk,
        reliability: item.claim_status || claim?.status || "unknown",
        engagement: `${Number(item.like_count || 0)} Eng`,
        dotClass: statusDotClass(item.claim_status || claim?.status),
        item,
        claimId: item.claim_id ? String(item.claim_id) : undefined,
      };
    });

    if (fromEvidence.length) return fromEvidence;

    return comments.filter((item) => isClusterMatch(item.cluster_key)).map((item) => ({
      id: String(item.id),
      tag: `#CMT-${String(item.id).slice(0, 8)}`,
      title: trimLine(item.text || "Comment", 48),
      summary: trimLine(item.text || "", 140),
      risk: item.like_count && item.like_count > 100 ? "Med" : "Low",
      reliability: "comment",
      engagement: `${Number(item.like_count || 0)} Eng`,
      dotClass: item.like_count && item.like_count > 100 ? "bg-status-amber" : "bg-status-cyan",
      item,
    }));
  }, [claimById, comments, evidence, postId, queryClusterKey]);

  useEffect(() => {
    setSelectedId((prev) => {
      if (prev && cards.some((c) => c.id === prev)) return prev;
      if (queryEvidenceId && cards.some((c) => c.id === queryEvidenceId)) return queryEvidenceId;
      if (queryCommentId && cards.some((c) => c.id === queryCommentId)) return queryCommentId;
      return cards[0]?.id || "";
    });
  }, [cards, queryCommentId, queryEvidenceId]);

  const selected = useMemo(() => cards.find((x) => x.id === selectedId) || cards[0] || null, [cards, selectedId]);

  const onAction = useCallback(
    async (action: string, meta: StitchActionMeta) => {
      if (action === "review_load_more") {
        setVisibleCount((prev) => Math.min(prev + 6, 60));
        setNotice(makeNotice("Loaded more evidence.", "ok"));
        return;
      }

      if (action === "select_review_item") {
        const next = String(meta.reviewId || "").trim();
        if (next) setSelectedId(next);
        return;
      }

      if (action === "review_run_deep_analysis") {
        const target = posts.find((p) => p.id === postId);
        try {
          const created = await api.createJob({
            pipeline_type: "A",
            mode: "analyze",
            input_config: {
              post_id: postId,
              target: target?.url || null,
              source: "review",
            },
          });
          setNotice(makeNotice(`Deep analysis queued #${String(created.id || "").slice(0, 8)}`, "ok"));
        } catch (e) {
          setNotice(makeNotice(`Run failed: ${e instanceof Error ? e.message : String(e)}`, "error"));
        }
        return;
      }

      if (action === "review_share") {
        if (!selected) {
          setNotice(makeNotice("No evidence selected.", "info"));
          return;
        }
        const text = `Evidence ${selected.id}`;
        try {
          await navigator.clipboard.writeText(text);
          setNotice(makeNotice("Evidence ID copied.", "ok"));
        } catch {
          setNotice(makeNotice(text, "info"));
        }
        return;
      }

      if (action === "review_report") {
        if (postId) {
          navigate(`/library?post_id=${encodeURIComponent(postId)}`);
        }
      }
    },
    [navigate, postId, posts, selected]
  );

  const selectedBridge = useMemo(() => {
    if (!selected) return null;
    const text = String(selected.item?.text || selected.summary || "");
    const linked = text
      .split(/\s+/)
      .filter((w) => /^[@#]/.test(w) || /\d+\.\d+\.\d+\.\d+/.test(w))
      .slice(0, 6);

    const claim = selected.claimId ? claimById.get(selected.claimId) : null;

    return {
      id: selected.id,
      risk: selected.risk,
      title: selected.title,
      context: phenomenon?.meta?.canonical_name ? `${selected.tag} · ${phenomenon.meta.canonical_name}` : selected.tag,
      timestamp: fmtIso(selected.item?.created_at || claim?.created_at),
      payloadSize: byteSizeLabel(text),
      entityCount: `${linked.length || 1} Unique`,
      hashStatus: claim?.status === "audited" ? "Verified" : "Pending",
      confidence: claim?.confidence != null ? `${Math.round(Number(claim.confidence) * 100)}% Confidence` : "-",
      fragment: text || selected.summary || "-",
      entities: linked.length ? linked : ["@n/a"],
    };
  }, [claimById, phenomenon?.meta?.canonical_name, selected]);

  const bridgeData = useMemo(
    () => ({
      page: "review",
      total: cards.length,
      cards: cards.slice(0, visibleCount),
      selected: selectedBridge,
    }),
    [cards, selectedBridge, visibleCount]
  );

  return (
    <StitchTemplateFrame
      html={templateHtml}
      title="Evidence Archive"
      pageId="review"
      actionMap={actionMap}
      bridgeData={bridgeData}
      onAction={onAction}
      notice={notice}
      hideTemplateHeader
    />
  );
}
