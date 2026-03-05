import type { ReactNode } from "react";

type Props = {
  label: string;
  value: ReactNode;
  hint?: string;
};

export function MetricCard({ label, value, hint }: Props) {
  return (
    <article className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value metric-number">{value}</div>
      {hint ? <div className="metric-hint">{hint}</div> : null}
    </article>
  );
}
