import { useMemo } from "react";
import { useShallow } from "zustand/react/shallow";
import { fmtDate } from "../lib/format";
import { useIntelligenceStore } from "../store/intelligenceStore";

function shortId(id?: string): string {
  if (!id) return "";
  return `#${String(id).slice(0, 8)}`;
}

export function ContextRail() {
  const { currentPost, phenomenon, lastRun, telemetryDegraded } = useIntelligenceStore(
    useShallow((s) => ({
      currentPost: s.currentPost,
      phenomenon: s.phenomenon,
      lastRun: s.lastRun,
      telemetryDegraded: s.telemetryDegraded,
    }))
  );

  const hasPost = Boolean(currentPost?.id || (currentPost?.snippet || "").trim());
  const hasPhenomenon = Boolean((phenomenon?.name || "").trim());
  const hasRun = Boolean(lastRun?.id);

  const mode = useMemo(() => {
    if (telemetryDegraded) return "degraded";
    if (!hasPost && !hasPhenomenon && !hasRun) return "idle";
    return "tracking";
  }, [hasPhenomenon, hasPost, hasRun, telemetryDegraded]);

  if (mode === "idle") {
    return (
      <aside className="context-rail intelligence" data-testid="context-rail">
        <section className="context-card" data-testid="context-waiting">
          <div className="context-title">System Context</div>
          <p>System idle · Waiting for telemetry...</p>
        </section>
      </aside>
    );
  }

  return (
    <aside className="context-rail intelligence" data-testid="context-rail">
      {mode === "degraded" ? (
        <section className="context-card" data-testid="context-degraded">
          <div className="context-title">Telemetry Status</div>
          <p>Telemetry degraded. Showing latest stable snapshot.</p>
          {hasRun ? (
            <div className="context-kv">
              <span>Last heartbeat</span>
              <strong>{fmtDate(lastRun?.updatedAt || null)}</strong>
            </div>
          ) : null}
        </section>
      ) : null}

      {hasPost ? (
        <section className="context-card" data-testid="context-post">
          <div className="context-title">Current Post</div>
          <p>{(currentPost?.snippet || "").trim() || shortId(currentPost?.id)}</p>
        </section>
      ) : null}

      {hasPhenomenon ? (
        <section className="context-card" data-testid="context-phenomenon">
          <div className="context-title">Phenomenon</div>
          <p>{phenomenon?.name}</p>
        </section>
      ) : null}

      {hasRun ? (
        <section className="context-card" data-testid="context-last-run">
          <div className="context-title">Last Run</div>
          <div className="context-kv">
            <span>ID</span>
            <strong>{shortId(lastRun?.id)}</strong>
          </div>
          <div className="context-kv">
            <span>Status</span>
            <strong>{lastRun?.status || ""}</strong>
          </div>
          <div className="context-kv">
            <span>Updated</span>
            <strong>{fmtDate(lastRun?.updatedAt || null)}</strong>
          </div>
          <div className="context-kv">
            <span>Running / Queued</span>
            <strong>{lastRun?.runningCount ?? 0} / {lastRun?.queuedCount ?? 0}</strong>
          </div>
        </section>
      ) : null}
    </aside>
  );
}
