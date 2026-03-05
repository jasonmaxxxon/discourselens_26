import type { CompareDeltaWindow } from "../lib/compareDeltaWindows";

type CompareMetric = {
  label: string;
  baseline: number;
  compare: number;
  unit?: string;
};

type CompareBoardProps = {
  baselineLabel: string;
  compareLabel: string;
  metrics: CompareMetric[];
  warnings?: string[];
  topWindows?: CompareDeltaWindow[];
};

function fmt(value: number, unit?: string): string {
  const rounded = Number.isFinite(value) ? Math.round(value * 100) / 100 : 0;
  return `${rounded}${unit || ""}`;
}

function deltaText(base: number, next: number): string {
  const diff = Math.round((next - base) * 100) / 100;
  if (diff > 0) return `+${diff}`;
  return `${diff}`;
}

function hhmm(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleTimeString("zh-Hant-HK", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

export function CompareBoard({ baselineLabel, compareLabel, metrics, warnings = [], topWindows = [] }: CompareBoardProps) {
  return (
    <section className="compare-board" data-testid="compare-board">
      <header className="compare-board-head">
        <h4>Cross-Post Compare</h4>
        <p>
          baseline {baselineLabel} vs {compareLabel}
        </p>
      </header>
      <div className="compare-board-grid">
        <div className="compare-board-row compare-board-row-head">
          <span>Metric</span>
          <span>Baseline</span>
          <span>Compare</span>
          <span>Delta</span>
        </div>
        {metrics.map((metric) => (
          <div key={metric.label} className="compare-board-row">
            <span>{metric.label}</span>
            <strong>{fmt(metric.baseline, metric.unit)}</strong>
            <strong>{fmt(metric.compare, metric.unit)}</strong>
            <span className={metric.compare - metric.baseline >= 0 ? "delta up" : "delta down"}>
              {deltaText(metric.baseline, metric.compare)}
            </span>
          </div>
        ))}
      </div>
      {topWindows.length ? (
        <div className="compare-top3-wrap">
          <div className="ev-kicker">Delta Window Top3</div>
          <div className="compare-top3-grid">
            {topWindows.map((window) => (
              <article key={`${window.t0}-${window.t1}`} className="compare-top3-item">
                <div className="compare-top3-head">
                  <strong>#{window.rank}</strong>
                  <span>{hhmm(window.t0)}-{hhmm(window.t1)}</span>
                </div>
                <div className="compare-top3-metrics">
                  <span>score {window.score}</span>
                  <span>mΔ {window.momentum_delta_pct}%</span>
                  <span>cΔ {window.cluster_share_divergence}</span>
                  <span>eΔ {window.evidence_density_delta}</span>
                </div>
                <div className="compare-top3-support">
                  support c {window.support.baseline_comments}/{window.support.compare_comments} · e{" "}
                  {window.support.baseline_evidence}/{window.support.compare_evidence}
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : null}
      {warnings.length ? (
        <div className="compare-warning-row">
          {warnings.map((warning) => (
            <span key={warning} className="warning-chip">
              {warning}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}
