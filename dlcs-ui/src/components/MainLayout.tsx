import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import clsx from "clsx";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import { useHotkeys } from "react-hotkeys-hook";
import { useShallow } from "zustand/react/shallow";
import { api } from "../lib/api";
import { fmtDate } from "../lib/format";
import { intelligenceSpring, routeTransition, routeVariants, type RouteDirection } from "../lib/motionConfig";
import { useIntelligenceStore } from "../store/intelligenceStore";
import { useTelemetryLoop } from "../hooks/useTelemetryLoop";

const BackgroundShaderLayer = lazy(() =>
  import("./BackgroundShaderLayer").then((module) => ({ default: module.BackgroundShaderLayer }))
);

const NAV = [
  { to: "/overview", label: "Overview" },
  { to: "/pipeline", label: "Pipeline" },
  { to: "/insights", label: "Insights" },
  { to: "/library", label: "Library" },
  { to: "/review", label: "Review" },
];

type Props = { children: React.ReactNode };

const RESET_SCROLL_ROUTES = new Set(NAV.map((item) => item.to));
const ROUTE_CACHE_KEY_PREFIX = "dl.cache.route.";

function getCurrentBundleSrc(): string {
  if (typeof document === "undefined") return "";
  const scripts = Array.from(document.querySelectorAll("script[type='module'][src]"));
  const current = scripts.find((s) => (s as HTMLScriptElement).src.includes("/assets/index-")) as HTMLScriptElement | undefined;
  if (!current) return "";
  return current.getAttribute("src") || current.src || "";
}

function routeIndex(pathname: string): number {
  return NAV.findIndex((item) => pathname === item.to || pathname.startsWith(`${item.to}/`));
}

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return Boolean(el.closest("[contenteditable='true']"));
}

function nextRoute(pathname: string, dir: RouteDirection): string | null {
  const current = routeIndex(pathname);
  if (current < 0) return null;
  const next = dir > 0 ? Math.min(NAV.length - 1, current + 1) : Math.max(0, current - 1);
  if (next === current) return null;
  return NAV[next]?.to || null;
}

export function MainLayout({ children }: Props) {
  const location = useLocation();
  useTelemetryLoop(!location.pathname.startsWith("/pipeline"));
  const disableShaderForAutomation = typeof navigator !== "undefined" && Boolean(navigator.webdriver);

  const navigate = useNavigate();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [actionError, setActionError] = useState("");
  const [routeDirection, setRouteDirection] = useState<RouteDirection>(0);
  const prevPathRef = useRef(location.pathname);
  const bundleSrcRef = useRef(getCurrentBundleSrc());

  const { lastRun, jobs, telemetryDegraded } =
    useIntelligenceStore(
      useShallow((s) => ({
        lastRun: s.lastRun,
        jobs: s.jobs,
        telemetryDegraded: s.telemetryDegraded,
      }))
    );
  const setLastRun = useIntelligenceStore((s) => s.setLastRun);

  const runningJobs = useMemo(
    () => jobs.filter((j) => ["processing", "discovering"].includes(String(j.status || "").toLowerCase())),
    [jobs]
  );
  const queuedJobs = useMemo(
    () => jobs.filter((j) => ["queued", "pending"].includes(String(j.status || "").toLowerCase())),
    [jobs]
  );
  const finishedJobs = useMemo(
    () => jobs.filter((j) => !["processing", "discovering", "queued", "pending"].includes(String(j.status || "").toLowerCase())),
    [jobs]
  );
  const recentFinishedJobs = useMemo(() => finishedJobs.slice(0, 5), [finishedJobs]);

  const queuedCount = queuedJobs.length;
  const runningCount = runningJobs.length;
  const hasActiveJobs = runningCount > 0 || queuedCount > 0;
  const activeJob = useMemo(
    () => {
      const isActive = (status: string) => {
        const s = String(status || "").toLowerCase();
        return s === "processing" || s === "discovering" || s === "queued" || s === "pending";
      };
      if (!hasActiveJobs) return null;
      if (lastRun?.id) {
        const pinned = jobs.find((j) => j.id === lastRun.id);
        if (pinned && isActive(pinned.status)) return pinned;
      }
      return jobs.find((j) => isActive(j.status)) || null;
    },
    [hasActiveJobs, jobs, lastRun?.id]
  );
  const staleActive = useMemo(() => {
    if (!activeJob || activeJob.status !== "processing") return false;
    const updated = new Date(activeJob.updated_at).getTime();
    if (!Number.isFinite(updated)) return false;
    return Date.now() - updated > 5 * 60 * 1000;
  }, [activeJob]);

  useEffect(() => {
    const prev = prevPathRef.current;
    if (prev !== location.pathname) {
      const prevIdx = routeIndex(prev);
      const nextIdx = routeIndex(location.pathname);
      if (prevIdx !== -1 && nextIdx !== -1) {
        setRouteDirection(nextIdx > prevIdx ? 1 : -1);
      } else {
        setRouteDirection(0);
      }
      prevPathRef.current = location.pathname;
    }
  }, [location.pathname]);

  const navigateDirectional = (to: string, direction: RouteDirection) => {
    if (!to || to === location.pathname) return;
    setRouteDirection(direction);
    navigate(to);
    if (RESET_SCROLL_ROUTES.has(to)) {
      window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    }
  };

  useHotkeys(
    "left",
    (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (drawerOpen || isTypingTarget(e.target)) return;
      const to = nextRoute(location.pathname, -1);
      if (!to) return;
      e.preventDefault();
      navigateDirectional(to, -1);
    },
    { enableOnFormTags: false },
    [drawerOpen, location.pathname]
  );

  useHotkeys(
    "right",
    (e) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (drawerOpen || isTypingTarget(e.target)) return;
      const to = nextRoute(location.pathname, 1);
      if (!to) return;
      e.preventDefault();
      navigateDirectional(to, 1);
    },
    { enableOnFormTags: false },
    [drawerOpen, location.pathname]
  );

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!bundleSrcRef.current) return;
    let checking = false;

    const checkForNewBuild = async () => {
      if (checking) return;
      checking = true;
      try {
        const res = await fetch(`/index.html?ts=${Date.now()}`, { cache: "no-store" });
        if (!res.ok) return;
        const html = await res.text();
        const match = html.match(/src="(\/assets\/index-[^"]+\.js)"/);
        const nextSrc = match?.[1] || "";
        if (!nextSrc || nextSrc === bundleSrcRef.current) return;
        for (const key of Object.keys(localStorage)) {
          if (key.startsWith(ROUTE_CACHE_KEY_PREFIX)) localStorage.removeItem(key);
        }
        window.location.reload();
      } catch {
        // ignore transient network issues while checking for a refreshed bundle
      } finally {
        checking = false;
      }
    };

    const timer = window.setInterval(checkForNewBuild, 20000);
    window.addEventListener("focus", checkForNewBuild);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", checkForNewBuild);
    };
  }, []);

  const selectJob = (jobId: string) => {
    localStorage.setItem("dl.activeRunId", jobId);
    const selected = jobs.find((j) => j.id === jobId);
    if (selected) {
      setLastRun({
        id: selected.id,
        status: selected.status,
        updatedAt: selected.updated_at,
        runningCount,
        queuedCount,
      });
    }
  };

  const cancelJob = async (jobId: string) => {
    try {
      setActionError("");
      await api.cancelJob(jobId);
      const meta = await api.listJobsMeta();
      useIntelligenceStore.getState().setJobsSnapshot(meta.data, meta.degraded);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="shell">
      <div className="background-base-layer" aria-hidden />
      {!disableShaderForAutomation ? (
        <Suspense fallback={null}>
          <BackgroundShaderLayer />
        </Suspense>
      ) : null}

      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">DL</div>
          <div>
            <div className="brand-title">DiscourseLens</div>
            <div className="brand-subtitle">Narrative Intelligence Console</div>
          </div>
        </div>

        <LayoutGroup id="main-nav">
          <nav className="nav-pill" aria-label="Primary navigation">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={(e) => {
                  if (e.defaultPrevented) return;
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
                  const current = routeIndex(location.pathname);
                  const next = routeIndex(item.to);
                  if (current === next) return;
                  e.preventDefault();
                  if (current < 0 || next < 0) {
                    navigateDirectional(item.to, 0);
                    return;
                  }
                  navigateDirectional(item.to, next > current ? 1 : -1);
                }}
                className={({ isActive }) => clsx("nav-item", isActive && "active")}
              >
                {({ isActive }) => (
                  <>
                    {isActive ? (
                      <motion.span
                        layoutId="dl-nav-active-pill"
                        transition={intelligenceSpring}
                        className="nav-active-indicator ready"
                        aria-hidden
                      />
                    ) : null}
                    <span className="nav-label">{item.label}</span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </LayoutGroup>

        <div className={clsx("status-block", !hasActiveJobs && "idle")}>
          {activeJob ? (
            <button type="button" className="run-chip clickable" onClick={() => setDrawerOpen(true)}>
              <span>Run #{String(activeJob.id).slice(0, 6)}</span>
              <span className={clsx("run-state", activeJob.status)}>{activeJob.status}</span>
              {staleActive ? <span className="stale-tag">stale</span> : null}
            </button>
          ) : null}
          <button type="button" className="job-drawer-btn" onClick={() => setDrawerOpen((v) => !v)} title="Open run center">
            {runningCount} running · {queuedCount} queued
          </button>
          {telemetryDegraded ? <span className="status-pill stale">degraded</span> : null}
          <div className="live-dot">Live</div>
          <div className="ops-text">Ops ready</div>
        </div>
      </header>

      {drawerOpen ? (
        <>
          <button className="job-drawer-backdrop" aria-label="Close drawer" onClick={() => setDrawerOpen(false)} />
          <aside className="job-drawer" role="dialog" aria-label="Job queue">
            <header className="job-drawer-head">
              <h3>Run Center</h3>
              <button type="button" className="job-drawer-close" onClick={() => setDrawerOpen(false)}>
                close
              </button>
            </header>
            <div className="job-drawer-list">
              {actionError ? <div className="error-banner compact">{actionError}</div> : null}

              <div className="drawer-group-title">Running ({runningJobs.length})</div>
              {runningJobs.map((job) => (
                <article key={job.id} className={clsx("job-drawer-item", activeJob?.id === job.id && "active")}>
                  <button type="button" className="job-select-btn" onClick={() => selectJob(job.id)}>
                    <div className="job-drawer-title">#{String(job.id).slice(0, 8)}</div>
                    <div className="job-drawer-meta">{job.pipeline_type} · {fmtDate(job.updated_at)}</div>
                    <div className="job-drawer-meta">processed {job.processed_count}/{job.total_count}</div>
                  </button>
                  <div className="job-item-actions">
                    <span className={clsx("status-pill", job.status)}>{job.status}</span>
                    <button type="button" className="small-btn danger" onClick={() => cancelJob(job.id)}>Cancel</button>
                  </div>
                </article>
              ))}

              <div className="drawer-group-title">Queued ({queuedJobs.length})</div>
              {queuedJobs.map((job) => (
                <article key={job.id} className={clsx("job-drawer-item", activeJob?.id === job.id && "active")}>
                  <button type="button" className="job-select-btn" onClick={() => selectJob(job.id)}>
                    <div className="job-drawer-title">#{String(job.id).slice(0, 8)}</div>
                    <div className="job-drawer-meta">{job.pipeline_type} · {fmtDate(job.updated_at)}</div>
                    <div className="job-drawer-meta">processed {job.processed_count}/{job.total_count}</div>
                  </button>
                  <div className="job-item-actions">
                    <span className={clsx("status-pill", job.status)}>{job.status}</span>
                    <button type="button" className="small-btn danger" onClick={() => cancelJob(job.id)}>Cancel</button>
                  </div>
                </article>
              ))}

              <div className="drawer-group-title">Recent ({Math.min(5, finishedJobs.length)}/{finishedJobs.length})</div>
              {recentFinishedJobs.map((job) => (
                <article key={job.id} className={clsx("job-drawer-item", activeJob?.id === job.id && "active")}>
                  <button type="button" className="job-select-btn" onClick={() => selectJob(job.id)}>
                    <div className="job-drawer-title">#{String(job.id).slice(0, 8)}</div>
                    <div className="job-drawer-meta">{job.pipeline_type} · {fmtDate(job.updated_at)}</div>
                    <div className="job-drawer-meta">processed {job.processed_count}/{job.total_count}</div>
                  </button>
                  <div className="job-item-actions">
                    <span className={clsx("status-pill", job.status)}>{job.status}</span>
                  </div>
                </article>
              ))}
              {finishedJobs.length > 5 ? <div className="helper-text">Showing latest 5 completed runs.</div> : null}

              {!jobs.length ? <div className="empty-note">暫無作業。</div> : null}
            </div>
          </aside>
        </>
      ) : null}

      <div className="shell-main">
        <main className="content ui-text">
          <div className="route-stack">
            <AnimatePresence mode="wait" initial={false} custom={routeDirection}>
              <motion.div
                key={location.pathname}
                custom={routeDirection}
                variants={routeVariants}
                initial="enter"
                animate="center"
                exit="exit"
                transition={routeTransition}
                className="route-frame"
              >
                {children}
              </motion.div>
            </AnimatePresence>
          </div>
        </main>
      </div>
    </div>
  );
}
