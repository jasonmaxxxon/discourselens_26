import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import templateHtml from "../stitch/phenomenon_registry_console.html?raw";
import { StitchTemplateFrame, type StitchActionMeta, type StitchNotice } from "../components/StitchTemplateFrame";
import { api, formatApiError, ApiError } from "../lib/api";
import { isDebugUI } from "../lib/debug";
import type { PhenomenonDetail, PhenomenonListItem, PhenomenonSignalsResponse } from "../lib/types";

const navMap = {
  Registry: "/library",
  Analysis: "/insights",
  Governance: "/review",
};

const actionMap = {
  filter: "library_filter_toggle",
  sort: "library_sort_toggle",
  refresh: "library_refresh",
  download: "library_export_json",
  "promote to axis": "library_promote_axis",
  share: "library_share",
  report: "library_report",
};

const actionSelectorMap = {
  "aside button:nth-of-type(2)": "library_side_monitoring",
  "aside button:nth-of-type(3)": "library_side_security",
  "aside button:nth-of-type(4)": "library_side_folder",
};

type SortMode = "last_seen_desc" | "count_desc";

type LibraryRow = {
  id: string;
  name: string;
  status: string;
  totalPosts: number;
  lastSeen: string;
  fingerprint: string;
};

type PhenomenonPostDrill = {
  postId: string;
  snippet: string;
  clusters: Array<{ clusterKey: number; label: string; size: number }>;
  evidence: Array<{ id: string; clusterKey: number | null; text: string }>;
};

type SourceHealthState = "ready" | "pending" | "empty" | "not_found" | "error";
type SourceHealth = { source: string; state: SourceHealthState; reason?: string; traceId?: string };

function payloadHealth(source: string, payload: unknown): SourceHealth {
  const data = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const raw = String(data.status || "ready").trim().toLowerCase();
  const state: SourceHealthState =
    raw === "pending" || raw === "empty" || raw === "not_found" || raw === "error" ? (raw as SourceHealthState) : "ready";
  return {
    source,
    state,
    reason: typeof data.reason_code === "string" ? data.reason_code : (typeof data.reason === "string" ? data.reason : undefined),
    traceId: typeof data.trace_id === "string" ? data.trace_id : undefined,
  };
}

function errorHealth(source: string, error: unknown): SourceHealth {
  if (error instanceof ApiError) {
    return {
      source,
      state: "error",
      reason: error.reasonCode || error.message,
      traceId: error.traceId,
    };
  }
  return { source, state: "error", reason: String(error || "unknown_error") };
}

function makeNotice(message: string, kind: "info" | "ok" | "error" = "info"): StitchNotice {
  return { message, kind, nonce: Date.now() + Math.floor(Math.random() * 1000) };
}

function toRow(item: PhenomenonListItem): LibraryRow {
  const id = String(item.id || "");
  const name = item.canonical_name || id || "-";
  const totalPosts = Number(item.total_posts || 0);
  const lastSeen = item.last_seen_at || "-";
  const compact = id.replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
  const fingerprint = compact ? `0x${compact.slice(0, 2)}...${compact.slice(-2) || "00"}` : "0x--";
  return {
    id,
    name,
    status: String(item.status || "unknown"),
    totalPosts,
    lastSeen,
    fingerprint,
  };
}

function formatIso(ts: string | null | undefined): string {
  if (!ts) return "-";
  const date = new Date(ts);
  if (!Number.isFinite(date.getTime())) return String(ts);
  return date.toISOString().slice(0, 19) + "Z";
}

function tsMs(ts: string | null | undefined): number | null {
  if (!ts) return null;
  const ms = new Date(ts).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function buildOccurrenceFromSignals(
  rows: PhenomenonSignalsResponse["occurrence_timeline"]
): { bars: number[]; deltaText: string } {
  const values = (rows || []).map((row) => Number(row.comment_count || 0) + Number(row.post_count || 0));
  if (!values.length) return { bars: [], deltaText: "-" };
  const avg = values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
  const recent = values.slice(-6).reduce((sum, value) => sum + value, 0) / Math.max(1, Math.min(6, values.length));
  if (avg <= 0) return { bars: values, deltaText: "-" };
  const delta = ((recent - avg) / avg) * 100;
  const sign = delta > 0 ? "+" : "";
  return { bars: values, deltaText: `${sign}${Math.round(delta)}% vs avg` };
}

function firstObservedIso(posts: Array<{ created_at?: string | null }>): string | null {
  const values = posts
    .map((row) => tsMs(row.created_at))
    .filter((x): x is number => Number.isFinite(x));
  if (!values.length) return null;
  return new Date(Math.min(...values)).toISOString();
}

export function StitchLibraryPage() {
  const navigate = useNavigate();
  const debugMode = useMemo(() => isDebugUI(), []);
  const [rows, setRows] = useState<LibraryRow[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [detail, setDetail] = useState<PhenomenonDetail | null>(null);
  const [signals, setSignals] = useState<PhenomenonSignalsResponse | null>(null);
  const [postDrilldown, setPostDrilldown] = useState<PhenomenonPostDrill[]>([]);
  const [sortMode, setSortMode] = useState<SortMode>("last_seen_desc");
  const [filterStatus, setFilterStatus] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [sourceHealth, setSourceHealth] = useState<Record<string, SourceHealth>>({});
  const [viewMode, setViewMode] = useState<"table" | "cards">(() => {
    if (typeof window === "undefined") return "table";
    const saved = String(window.localStorage.getItem("dl.library.viewMode") || "").trim().toLowerCase();
    return saved === "cards" ? "cards" : "table";
  });
  const [notice, setNotice] = useState<StitchNotice | null>(null);
  const [updatedAtText, setUpdatedAtText] = useState("-");

  const upsertHealth = useCallback((entry: SourceHealth) => {
    setSourceHealth((prev) => ({ ...prev, [entry.source]: entry }));
  }, []);

  const refreshList = useCallback(async (opts?: { q?: string; status?: string }) => {
    const q = typeof opts?.q === "string" ? opts.q : query;
    const status = typeof opts?.status === "string" ? opts.status : filterStatus;
    try {
      const raw = await api.listPhenomena({
        q: q || undefined,
        status: status === "all" ? undefined : status,
        limit: 80,
      });
      const mapped = raw.map(toRow);
      setRows(mapped);
      setUpdatedAtText(new Date().toLocaleTimeString());
      if (!selectedId && mapped[0]?.id) setSelectedId(mapped[0].id);
      if (selectedId && !mapped.some((row) => row.id === selectedId)) {
        setSelectedId(mapped[0]?.id || "");
      }
    } catch (e) {
      upsertHealth(errorHealth("phenomena_list", e));
      setNotice(makeNotice(`Library sync failed: ${formatApiError(e)}`, "error"));
    }
  }, [filterStatus, query, selectedId, upsertHealth]);

  useEffect(() => {
    void refreshList();
    const timer = window.setInterval(() => {
      void refreshList();
    }, 12000);
    return () => window.clearInterval(timer);
  }, [refreshList]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("dl.library.viewMode", viewMode);
  }, [viewMode]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setSignals(null);
      setPostDrilldown([]);
      return;
    }
    let alive = true;
    void api.getPhenomenon(selectedId, 30)
      .then((payload) => {
        if (!alive) return;
        const health = payloadHealth("phenomenon_detail", payload);
        upsertHealth(health);
        if (health.state !== "ready") {
          const trace = health.traceId ? ` · trace ${health.traceId}` : "";
          setNotice(makeNotice(`Phenomenon detail ${health.state}${health.reason ? ` · ${health.reason}` : ""}${trace}`, "info"));
        }
        setDetail(payload);
      })
      .catch((e) => {
        if (!alive) return;
        upsertHealth(errorHealth("phenomenon_detail", e));
        setNotice(makeNotice(`Detail load failed: ${formatApiError(e)}`, "error"));
      });
    return () => {
      alive = false;
    };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !detail?.recent_posts?.length) {
      setPostDrilldown([]);
      return;
    }
    let alive = true;
    const targets = detail.recent_posts.slice(0, 4).map((row) => String(row.id || "")).filter(Boolean);
    if (!targets.length) {
      setPostDrilldown([]);
      return;
    }
    void Promise.all(
      targets.map(async (pid) => {
        const [clustersRes, evidenceRes] = await Promise.allSettled([
          api.getClusters(pid),
          api.getEvidence(pid),
        ]);
        if (clustersRes.status === "rejected") {
          upsertHealth(errorHealth("post_clusters", clustersRes.reason));
        } else {
          upsertHealth(payloadHealth("post_clusters", clustersRes.value));
        }
        if (evidenceRes.status === "rejected") {
          upsertHealth(errorHealth("post_evidence", evidenceRes.reason));
        } else {
          upsertHealth({ source: "post_evidence", state: "ready" });
        }

        const clustersData =
          clustersRes.status === "fulfilled"
            ? clustersRes.value
            : ({ clusters: [] as Array<{ cluster_key: number; label: string; size: number }> } as const);
        const evidenceData =
          evidenceRes.status === "fulfilled"
            ? evidenceRes.value
            : ({ items: [] as Array<{ id: string; cluster_key?: number | null; text?: string }> } as const);
        return {
          postId: pid,
          snippet: String(detail.recent_posts.find((row) => String(row.id) === pid)?.snippet || ""),
          clusters: (clustersData.clusters || [])
            .slice(0, 3)
            .map((row) => ({
              clusterKey: Number(row.cluster_key),
              label: String(row.label || `C-${String(row.cluster_key).padStart(3, "0")}`),
              size: Number(row.size || 0),
            })),
          evidence: (evidenceData.items || [])
            .slice(0, 4)
            .map((row) => ({
              id: String(row.id || ""),
              clusterKey: row.cluster_key == null ? null : Number(row.cluster_key),
              text: String(row.text || ""),
            })),
        } as PhenomenonPostDrill;
      })
    )
      .then((rows) => {
        if (!alive) return;
        setPostDrilldown(rows);
      })
      .catch(() => {
        if (!alive) return;
        setPostDrilldown([]);
      });
    return () => {
      alive = false;
    };
  }, [detail?.recent_posts, selectedId, upsertHealth]);

  useEffect(() => {
    if (!selectedId) {
      setSignals(null);
      return;
    }
    let alive = true;
    void api.getPhenomenonSignals(selectedId, "24h")
      .then((payload) => {
        if (!alive) return;
        const health = payloadHealth("phenomenon_signals", payload);
        upsertHealth(health);
        if (health.state !== "ready" && health.state !== "empty") {
          const trace = health.traceId ? ` · trace ${health.traceId}` : "";
          setNotice(makeNotice(`Signals ${health.state}${health.reason ? ` · ${health.reason}` : ""}${trace}`, "info"));
        }
        setSignals(payload || null);
      })
      .catch((e) => {
        if (!alive) return;
        upsertHealth(errorHealth("phenomenon_signals", e));
        setNotice(makeNotice(`Signals load failed: ${formatApiError(e)}`, "error"));
        setSignals(null);
      });
    return () => {
      alive = false;
    };
  }, [selectedId, upsertHealth]);

  const filteredSortedRows = useMemo(() => {
    const list = [...rows];
    list.sort((a, b) => {
      if (sortMode === "count_desc") return b.totalPosts - a.totalPosts;
      const ta = new Date(a.lastSeen).getTime();
      const tb = new Date(b.lastSeen).getTime();
      return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
    });
    return list;
  }, [rows, sortMode]);

  const selectedRow = useMemo(() => {
    return filteredSortedRows.find((row) => row.id === selectedId) || filteredSortedRows[0] || null;
  }, [filteredSortedRows, selectedId]);

  const selectedBridge = useMemo(() => {
    if (!selectedRow) return null;
    const recentPosts = detail?.recent_posts || [];
    const occurrence = buildOccurrenceFromSignals(signals?.occurrence_timeline || []);
    const relatedSignals = (signals?.related_signals || []).map((row) => ({
      id: String(row.signal_id || "-"),
      source: `${row.source_type || "signal"} · ${String(row.source_ref || "").slice(0, 8)}`,
      scorePct: Number(row.strength_pct || 0),
    }));
    const totalLikes = Number(detail?.stats?.total_likes || 0);
    const totalPosts = Number(detail?.stats?.total_posts || selectedRow.totalPosts || 1);
    const confidence = relatedSignals.length
      ? `${Math.round(relatedSignals.reduce((sum, row) => sum + Number(row.scorePct || 0), 0) / relatedSignals.length)}%`
      : "--";
    const impact = (totalLikes / Math.max(1, totalPosts)).toFixed(1);
    const firstObserved = formatIso(firstObservedIso(recentPosts));
    const lastSeen = formatIso(detail?.stats?.last_seen_at || selectedRow.lastSeen);
    const primaryPostId = String(signals?.supporting_refs?.latest_post_id || recentPosts[0]?.id || "").trim();
    return {
      id: selectedRow.id,
      name: selectedRow.name,
      status: detail?.meta?.status || selectedRow.status,
      category: detail?.meta?.description ? "Narrative" : "Anomaly",
      confidence,
      impactScore: `${impact} / 10`,
      firstObserved,
      lastSeen,
      totalPosts,
      totalLikes,
      occurrenceBars: occurrence.bars,
      occurrenceDelta: occurrence.deltaText,
      relatedSignals,
      metadata: {
        firstObserved,
        ingestionNode: primaryPostId ? `post-${primaryPostId}` : "-",
        schemaVersion: "-",
        encryption: "-",
      },
      postDrilldown,
    };
  }, [detail, postDrilldown, selectedRow, signals]);

  const onAction = useCallback(
    async (action: string, meta: StitchActionMeta) => {
      if (action === "library_refresh") {
        await refreshList();
        setNotice(makeNotice("Library refreshed.", "ok"));
        return;
      }

      if (action === "library_export_json") {
        const blob = new Blob([JSON.stringify(filteredSortedRows, null, 2)], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `phenomena-${Date.now()}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setNotice(makeNotice("Phenomena JSON exported.", "ok"));
        return;
      }

      if (action === "library_filter_toggle") {
        const cycle = ["all", "active", "provisional", "unknown"] as const;
        const next = cycle[(cycle.indexOf(filterStatus as (typeof cycle)[number]) + 1) % cycle.length];
        setFilterStatus(next);
        await refreshList({ status: next });
        setNotice(makeNotice(`Filter: ${next}`, "info"));
        return;
      }

      if (action === "library_sort_toggle") {
        const next: SortMode = sortMode === "last_seen_desc" ? "count_desc" : "last_seen_desc";
        setSortMode(next);
        setNotice(makeNotice(`Sort: ${next === "count_desc" ? "occurrence" : "last seen"}`, "info"));
        return;
      }

      if (action === "library_view_cards") {
        setViewMode("cards");
        return;
      }

      if (action === "library_view_table") {
        setViewMode("table");
        return;
      }

      if (action === "library_search") {
        const nextQuery = String(meta.query || meta.searchQuery || "").trim();
        setQuery(nextQuery);
        await refreshList({ q: nextQuery });
        setNotice(makeNotice(nextQuery ? `Search: ${nextQuery}` : "Search reset", "info"));
        return;
      }

      if (action === "select_phenomenon") {
        const nextId = String(meta.phenomenonId || "").trim();
        if (nextId) setSelectedId(nextId);
        return;
      }

      if (action === "library_promote_axis") {
        if (!selectedRow?.id) {
          setNotice(makeNotice("Select a phenomenon first.", "info"));
          return;
        }
        try {
          const promoted = await api.promotePhenomenon(selectedRow.id);
          setNotice(makeNotice(`Promoted ${promoted.id || selectedRow.id} to active`, "ok"));
          await refreshList();
          return;
        } catch (e) {
          setNotice(makeNotice(`Promote failed: ${e instanceof Error ? e.message : String(e)}`, "error"));
          return;
        }
      }

      if (action === "library_share") {
        if (!selectedRow?.id) {
          setNotice(makeNotice("No phenomenon selected.", "info"));
          return;
        }
        const shareText = `Phenomenon ${selectedRow.id}`;
        try {
          await navigator.clipboard.writeText(shareText);
          setNotice(makeNotice("Phenomenon ID copied.", "ok"));
        } catch {
          setNotice(makeNotice(shareText, "info"));
        }
        return;
      }

      if (action === "library_report") {
        if (!selectedRow?.id) {
          setNotice(makeNotice("No phenomenon selected.", "info"));
          return;
        }
        navigate(`/review?phenomenon_id=${encodeURIComponent(selectedRow.id)}`);
        return;
      }

      if (action === "library_open_review") {
        const postId = String(meta.postId || "").trim();
        if (!postId) {
          setNotice(makeNotice("No post linked for this evidence.", "info"));
          return;
        }
        const query = new URLSearchParams({ post_id: postId });
        const clusterKey = String(meta.clusterKey || "").trim();
        const evidenceId = String(meta.evidenceId || "").trim();
        if (clusterKey) query.set("cluster_key", clusterKey);
        if (evidenceId) query.set("evidence_id", evidenceId);
        navigate(`/review?${query.toString()}`);
        return;
      }

      if (action === "library_side_monitoring" || action === "library_side_security" || action === "library_side_folder") {
        setNotice(makeNotice("該側邊頁面尚未建立，已先保留入口。", "info"));
      }
    },
    [filterStatus, filteredSortedRows, navigate, refreshList, selectedRow?.id, sortMode]
  );

  const bridgeData = useMemo(
    () => ({
      page: "library",
      items: filteredSortedRows.slice(0, 50),
      total: filteredSortedRows.length,
      selected: selectedBridge,
      updatedAtText,
      viewMode,
      sourceHealth: Object.values(sourceHealth),
      debugMode,
    }),
    [debugMode, filteredSortedRows, selectedBridge, sourceHealth, updatedAtText, viewMode]
  );

  return (
    <StitchTemplateFrame
      html={templateHtml}
      navMap={navMap}
      title="Phenomenon Registry"
      pageId="library"
      actionMap={actionMap}
      actionSelectorMap={actionSelectorMap}
      bridgeData={bridgeData}
      onAction={onAction}
      notice={notice}
      hideTemplateHeader
    />
  );
}
