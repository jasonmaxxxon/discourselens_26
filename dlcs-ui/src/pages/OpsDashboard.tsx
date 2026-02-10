import { useEffect, useMemo, useState } from "react";
import { supabase } from "../lib/supabase";

type OpsSummary = {
  jobs_total?: number | null;
  jobs_success_rate?: number | null;
  jobs_failed?: number | null;
  jobs_inflight?: number | null;
  coverage_avg?: number | null;
  coverage_p50?: number | null;
  coverage_p90?: number | null;
  claims_kept_rate?: number | null;
  claims_audit_fail_rate?: number | null;
  claims_audit_partial_rate?: number | null;
  behavior_availability_rate?: number | null;
  risk_brief_availability_rate?: number | null;
  llm_timeout_rate?: number | null;
  llm_error_rate?: number | null;
  llm_avg_latency_ms?: number | null;
  llm_total_tokens?: number | null;
  llm_tokens_per_post?: number | null;
  llm_token_coverage_rate?: number | null;
};

type OpsTrend = {
  date: string;
  coverage_avg?: number | null;
  job_success_rate?: number | null;
  llm_calls?: number | null;
  llm_timeout_rate?: number | null;
};

type OpsKpi = {
  range_days: number;
  generated_at: string;
  summary: OpsSummary;
  trends: OpsTrend[];
  sources: Record<string, number>;
  truncated?: Record<string, boolean>;
};

const API_URL = "/api/ops/kpi";
const DEFAULT_RANGE = "7d";
const POLL_INTERVAL = Number(import.meta.env.VITE_OPS_POLL_INTERVAL_MS || 60000);

function fmtPct(value?: number | null) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function fmtNum(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Intl.NumberFormat().format(value);
}

function fmtLatency(value?: number | null) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value)} ms`;
}

export default function OpsDashboard() {
  const [range, setRange] = useState(DEFAULT_RANGE);
  const [data, setData] = useState<OpsKpi | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);
  const [realtimeStatus, setRealtimeStatus] = useState("disabled");
  const [lastRealtime, setLastRealtime] = useState<string | null>(null);

  const refresh = () => setRefreshTick((t) => t + 1);

  useEffect(() => {
    let cancelled = false;
    const fetchKpi = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_URL}?range=${encodeURIComponent(range)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = (await res.json()) as OpsKpi;
        if (!cancelled) setData(json);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void fetchKpi();
    return () => {
      cancelled = true;
    };
  }, [range, refreshTick]);

  useEffect(() => {
    if (!supabase) {
      setRealtimeStatus("disabled");
      return;
    }
    const tables = [
      "threads_coverage_audits",
      "threads_behavior_audits",
      "threads_claim_audits",
      "threads_risk_briefs",
      "llm_call_logs",
    ];
    const channel = supabase.channel("ops-kpi-refresh");
    tables.forEach((table) => {
      channel.on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table },
        () => {
          setLastRealtime(new Date().toISOString());
          refresh();
        }
      );
    });
    channel.subscribe((status) => {
      setRealtimeStatus(status);
    });
    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  useEffect(() => {
    if (!POLL_INTERVAL || POLL_INTERVAL < 5000) return;
    const id = window.setInterval(refresh, POLL_INTERVAL);
    return () => window.clearInterval(id);
  }, []);

  const trendRows = useMemo(() => data?.trends ?? [], [data]);
  const summary = data?.summary ?? {};

  return (
    <div className="app-shell p-4">
      <header className="app-header mb-4">
        <div>
          <div className="text-sm font-semibold uppercase tracking-wide text-[var(--text-secondary)]">
            Ops Dashboard
          </div>
          <div className="text-xs text-muted">Ops KPI + Cost logs (no estimation)</div>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">Range</span>
            {["7d", "30d", "90d"].map((opt) => (
              <button
                key={opt}
                className={`btn btn-ghost text-xs ${range === opt ? "border-blue-400 text-blue-600" : ""}`}
                onClick={() => setRange(opt)}
              >
                {opt}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <span className="ops-chip--live">LIVE</span>
            <span className={`ops-heartbeat ${realtimeStatus === "SUBSCRIBED" ? "ops-heartbeat--alive" : "ops-heartbeat--stale"}`} />
            <span className="text-[11px] text-muted">
              {lastRealtime ? new Date(lastRealtime).toLocaleTimeString() : "—"}
            </span>
          </div>
          <button className="btn btn-ghost text-xs" onClick={refresh}>
            <span className={`material-icons-outlined text-sm ${loading ? "animate-spin" : ""}`}>refresh</span>
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="card p-3 mb-4 border border-red-200 text-red-600">
          Failed to load ops KPI: {error}
        </div>
      )}

      <div className="grid grid-cols-4 gap-4 mb-4">
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Pipeline Success</div>
          <div className="text-3xl font-semibold text-[var(--text-secondary)]">
            {fmtPct(summary.jobs_success_rate)}
          </div>
          <div className="text-xs text-muted mt-1">Jobs total: {fmtNum(summary.jobs_total)}</div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Coverage Avg</div>
          <div className="text-3xl font-semibold text-[var(--text-secondary)]">
            {fmtPct(summary.coverage_avg)}
          </div>
          <div className="text-xs text-muted mt-1">
            P50 {fmtPct(summary.coverage_p50)} · P90 {fmtPct(summary.coverage_p90)}
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Claims Kept</div>
          <div className="text-3xl font-semibold text-[var(--text-secondary)]">
            {fmtPct(summary.claims_kept_rate)}
          </div>
          <div className="text-xs text-muted mt-1">
            Audit fail {fmtPct(summary.claims_audit_fail_rate)} · partial {fmtPct(summary.claims_audit_partial_rate)}
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Behavior / Risk</div>
          <div className="text-3xl font-semibold text-[var(--text-secondary)]">
            {fmtPct(summary.behavior_availability_rate)}
          </div>
          <div className="text-xs text-muted mt-1">Risk briefs {fmtPct(summary.risk_brief_availability_rate)}</div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4 mb-4">
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">LLM Timeout</div>
          <div className="text-3xl font-semibold text-danger">{fmtPct(summary.llm_timeout_rate)}</div>
          <div className="text-xs text-muted mt-1">Error {fmtPct(summary.llm_error_rate)}</div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">LLM Latency</div>
          <div className="text-3xl font-semibold text-[var(--text-secondary)]">
            {fmtLatency(summary.llm_avg_latency_ms)}
          </div>
          <div className="text-xs text-muted mt-1">Token coverage {fmtPct(summary.llm_token_coverage_rate)}</div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Tokens/Post</div>
          <div className="text-3xl font-semibold text-[var(--text-secondary)]">
            {fmtNum(summary.llm_tokens_per_post)}
          </div>
          <div className="text-xs text-muted mt-1">Total tokens {fmtNum(summary.llm_total_tokens)}</div>
        </div>
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Jobs Failed</div>
          <div className="text-3xl font-semibold text-danger">{fmtNum(summary.jobs_failed)}</div>
          <div className="text-xs text-muted mt-1">In flight {fmtNum(summary.jobs_inflight)}</div>
        </div>
      </div>

      <div className="card mb-4">
        <div className="card-header">Daily Trend</div>
        <div className="overflow-auto">
          <table className="table">
            <thead>
              <tr>
                <th className="w-28">Date</th>
                <th>Coverage Avg</th>
                <th>Pipeline Success</th>
                <th>LLM Calls</th>
                <th>LLM Timeout</th>
              </tr>
            </thead>
            <tbody className="text-xs font-mono">
              {trendRows.map((row) => (
                <tr key={row.date}>
                  <td>{row.date}</td>
                  <td>{fmtPct(row.coverage_avg)}</td>
                  <td>{fmtPct(row.job_success_rate)}</td>
                  <td>{fmtNum(row.llm_calls)}</td>
                  <td>{fmtPct(row.llm_timeout_rate)}</td>
                </tr>
              ))}
              {trendRows.length === 0 && (
                <tr>
                  <td colSpan={5} className="text-center text-muted py-6">
                    No trend data available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4">
          <div className="text-xs uppercase text-muted mb-2">Sources</div>
          <div className="text-sm text-[var(--text-secondary)] space-y-1">
            {Object.entries(data?.sources || {}).map(([key, val]) => (
              <div key={key} className="flex items-center justify-between">
                <span>{key}</span>
                <span>{fmtNum(val)}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card p-4 col-span-2">
          <div className="text-xs uppercase text-muted mb-2">Notes</div>
          <div className="text-sm text-muted">
            KPI are computed from audit tables and LLM call logs only. Realtime notifications trigger refresh on new
            inserts. Tokens are counted only when provider returns usage metadata.
          </div>
          {data?.truncated && (
            <div className="text-xs text-warning mt-2">
              Truncation: {Object.entries(data.truncated).filter(([, v]) => v).map(([k]) => k).join(", ") || "none"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
