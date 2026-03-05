import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import type { JobStatus } from "../lib/types";

const NAV = [
  { to: "/overview", label: "Overview" },
  { to: "/pipeline", label: "Pipeline" },
  { to: "/insights", label: "Insights" },
  { to: "/library", label: "Library" },
  { to: "/review", label: "Review" },
];

function fmtShort(id: string | undefined): string {
  const s = String(id || "").trim();
  return s ? s.slice(0, 8) : "-";
}

function readPinnedJobId(): string {
  if (typeof window === "undefined") return "";
  return String(window.localStorage.getItem("dl.activeRunId") || "").trim();
}

function rankStatus(status: string | undefined): number {
  const s = String(status || "").toLowerCase();
  if (s === "processing" || s === "discovering") return 0;
  if (s === "queued" || s === "pending") return 1;
  if (s === "failed" || s === "stale") return 3;
  if (s === "canceled") return 4;
  return 2;
}

function pickActiveJob(jobs: JobStatus[]): JobStatus | null {
  if (!jobs.length) return null;
  const sorted = [...jobs].sort((a, b) => {
    const byStatus = rankStatus(a.status) - rankStatus(b.status);
    if (byStatus !== 0) return byStatus;
    return new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime();
  });
  return sorted[0] || null;
}

export function StitchGlobalTopBar() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [degraded, setDegraded] = useState(false);
  const [pinnedJobId, setPinnedJobId] = useState<string>(() => readPinnedJobId());
  const [buildSha, setBuildSha] = useState("");
  const [buildEnv, setBuildEnv] = useState("");
  const [metaUnavailable, setMetaUnavailable] = useState(false);
  const isDev = import.meta.env.DEV;

  const refresh = useCallback(async () => {
    try {
      const result = await api.listJobsMeta();
      setJobs(result.data || []);
      setDegraded(Boolean(result.degraded));
    } catch {
      setJobs([]);
      setDegraded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    const onFocus = () => void refresh();
    const onStorage = () => setPinnedJobId(readPinnedJobId());
    window.addEventListener("focus", onFocus);
    window.addEventListener("storage", onStorage);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("storage", onStorage);
    };
  }, [refresh]);

  useEffect(() => {
    if (!isDev) return;
    let alive = true;
    const loadBuildMeta = async () => {
      try {
        const meta = await api.getBuildMeta();
        if (!alive) return;
        setBuildSha(String(meta.build_sha || "").trim());
        setBuildEnv(String(meta.env || "").trim());
        setMetaUnavailable(false);
      } catch {
        if (!alive) return;
        setBuildSha("");
        setBuildEnv("");
        setMetaUnavailable(true);
      }
    };
    void loadBuildMeta();
    const timer = window.setInterval(() => void loadBuildMeta(), 15000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [isDev]);

  const running = useMemo(
    () => jobs.filter((j) => ["processing", "discovering"].includes(String(j.status || "").toLowerCase())).length,
    [jobs]
  );
  const queued = useMemo(
    () => jobs.filter((j) => ["queued", "pending"].includes(String(j.status || "").toLowerCase())).length,
    [jobs]
  );
  const active = useMemo(() => {
    if (pinnedJobId) {
      const match = jobs.find((row) => String(row.id) === pinnedJobId);
      if (match) return match;
    }
    return pickActiveJob(jobs);
  }, [jobs, pinnedJobId]);

  useEffect(() => {
    if (!active?.id) return;
    if (typeof window === "undefined") return;
    window.localStorage.setItem("dl.activeRunId", String(active.id));
    setPinnedJobId(String(active.id));
  }, [active?.id]);

  const onRunChipClick = () => {
    const id = String(active?.id || "").trim();
    if (!id) {
      navigate("/pipeline");
      return;
    }
    navigate(`/pipeline?job_id=${encodeURIComponent(id)}`);
  };

  return (
    <header className="stitch-topbar">
      <div className="stitch-brand" onClick={() => navigate("/overview")} role="button" tabIndex={0}>
        <div className="stitch-brand-mark">DL</div>
        <div>
          <div className="stitch-brand-title">DiscourseLens</div>
          <div className="stitch-brand-sub">Narrative Intelligence Console</div>
        </div>
      </div>

      <nav className="stitch-nav" aria-label="Primary">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `stitch-nav-item${isActive ? " active" : ""}`}
            end={item.to === "/overview"}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="stitch-status">
        <span className="stitch-chip">{running} running • {queued} queued</span>
        <span className={`stitch-chip ${degraded ? "warn" : "ok"}`}>{degraded ? "degraded" : "ops ready"}</span>
        <span className="stitch-live"><i />Live</span>
        {isDev ? (
          metaUnavailable ? (
            <span className="stitch-chip warn" title="Backend build meta unavailable">meta unavailable</span>
          ) : (
            <span className="stitch-chip" title={"build " + (buildSha || "unknown") + " · env " + (buildEnv || "unknown")}>
              {"build " + (buildSha ? buildSha.slice(0, 8) : "unknown")}
            </span>
          )
        ) : null}
        <button type="button" className="stitch-chip" onClick={onRunChipClick}>
          run #{fmtShort(active?.id)} {String(active?.status || "idle")}
        </button>
      </div>
    </header>
  );
}
