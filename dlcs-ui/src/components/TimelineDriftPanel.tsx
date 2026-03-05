import { useMemo } from "react";
import type { CommentItem, EvidenceItem } from "../lib/types";
import { buildTimelineModel } from "../lib/timelineEvolve";

export type TimelineCasebookAnnotation = {
  count: number;
  lastAnnotatedAt: string | null;
  noteSnippet?: string | null;
};

type TimelineDriftPanelProps = {
  comments: CommentItem[];
  evidence: EvidenceItem[];
  loading?: boolean;
  degraded?: boolean;
  t0?: string | null;
  t1?: string | null;
  bucketMinutes?: number | "auto";
  annotations?: Record<string, TimelineCasebookAnnotation>;
  onBucketClick?: (payload: { t0: string; t1: string; cluster_key?: number; casebook_only?: boolean }) => void;
};

function shortTime(iso: string): string {
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return iso;
  return ts.toLocaleTimeString("zh-Hant-HK", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

function shortDateTime(iso: string | null): string {
  if (!iso) return "-";
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return iso;
  return ts.toLocaleString("zh-Hant-HK", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function annotationKey(t0: string, t1: string): string {
  return `${t0}|${t1}`;
}

export function TimelineDriftPanel({
  comments,
  evidence,
  loading = false,
  degraded = false,
  t0,
  t1,
  bucketMinutes = "auto",
  annotations = {},
  onBucketClick,
}: TimelineDriftPanelProps) {
  const model = useMemo(
    () =>
      buildTimelineModel({
        comments,
        evidence,
        t0,
        t1,
        bucketMinutes,
      }),
    [bucketMinutes, comments, evidence, t0, t1]
  );

  const hasData = model.buckets.some((bucket) => bucket.comment_count > 0 || bucket.evidence_count > 0);

  return (
    <section className="timeline-drift-panel" data-testid="timeline-drift-panel">
      <header className="timeline-drift-head">
        <div>
          <h4>Timeline Drift</h4>
          <p>
            {model.bucket_minutes}m buckets · {shortTime(model.range.t0)} - {shortTime(model.range.t1)}
          </p>
        </div>
        <div className="timeline-signal-pills">
          <span className={`signal-pill ${model.momentum.trend}`}>momentum {model.momentum.latest_bucket_momentum}</span>
          <span className="signal-pill neutral">drift {model.drift.latest_drift}</span>
          {degraded ? <span className="signal-pill warn">degraded</span> : null}
        </div>
      </header>

      {loading && !hasData ? (
        <div className="timeline-skeleton-grid" aria-label="timeline loading">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={`timeline-sk-${i}`} className="timeline-skeleton-col skeleton-card" />
          ))}
        </div>
      ) : (
        <div className="timeline-bucket-grid">
          {model.buckets.map((bucket) => {
            const key = annotationKey(bucket.bucket_start_iso, bucket.bucket_end_iso);
            const annotation = annotations[key];
            return (
            <button
              key={`${bucket.bucket_start_iso}-${bucket.bucket_end_iso}`}
              type="button"
              className={`timeline-bucket ${bucket.anomaly_flag ? "anomaly" : ""}`}
              onClick={() =>
                onBucketClick?.({
                  t0: bucket.bucket_start_iso,
                  t1: bucket.bucket_end_iso,
                  cluster_key: bucket.top_cluster_key,
                  casebook_only: Boolean(annotation),
                })
              }
            >
              {annotation ? (
                <div className="timeline-casebook-badge-wrap">
                  <span className="timeline-casebook-badge" data-testid="timeline-casebook-badge">
                    {annotation.count}
                  </span>
                  <div className="timeline-casebook-hover">
                    <div>casebook {annotation.count}</div>
                    <div>last {shortDateTime(annotation.lastAnnotatedAt)}</div>
                    {annotation.noteSnippet ? <div>{annotation.noteSnippet}</div> : null}
                  </div>
                </div>
              ) : null}
              <div className="timeline-bars">
                <span className="bar momentum" style={{ height: `${Math.max(8, bucket.momentum_score)}%` }} />
                <span className="bar drift" style={{ height: `${Math.max(8, bucket.drift_score)}%` }} />
              </div>
              <div className="timeline-meta">
                <span>{shortTime(bucket.bucket_start_iso)}</span>
                <strong>
                  {bucket.comment_count}/{bucket.evidence_count}
                </strong>
              </div>
            </button>
            );
          })}
        </div>
      )}

      {!hasData && !loading ? <div className="empty-note">No evidence lock available · Underspecified</div> : null}

      {model.sufficiency.warnings.length ? (
        <div className="timeline-warning-row">
          {model.sufficiency.warnings.map((warning) => (
            <span key={warning} className="warning-chip">
              {warning}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}
