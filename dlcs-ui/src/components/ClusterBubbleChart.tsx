import { useMemo } from "react";
import clsx from "clsx";
import type { ClusterItem } from "../lib/types";
import { fmtPct } from "../lib/format";

type SizeMode = "volume" | "engagement";

type Props = {
  clusters: ClusterItem[];
  selectedKey?: number;
  onSelect: (clusterKey: number) => void;
  sizeMode?: SizeMode;
};

type Bubble = {
  key: number;
  label: string;
  share?: number | null;
  x: number;
  y: number;
  r: number;
  heat: number;
  unstable: boolean;
};

const W = 880;
const H = 340;

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, v));
}

function buildBubbles(clusters: ClusterItem[], sizeMode: SizeMode): Bubble[] {
  const values = clusters.map((c) => {
    if (sizeMode === "engagement") {
      return (c.engagement?.likes || 0) + (c.engagement?.replies || 0);
    }
    return c.size || 0;
  });

  const maxValue = Math.max(...values, 1);
  const minR = 22;
  const maxR = 80;

  const bubbles: Bubble[] = clusters.map((c, i) => {
    const value = values[i];
    const ratio = Math.sqrt(value / maxValue);
    const r = clamp(minR + ratio * (maxR - minR), minR, maxR);
    const x = W * (0.16 + i * (0.68 / Math.max(1, clusters.length - 1)));
    const y = H * (0.5 + (i % 2 === 0 ? -1 : 1) * 0.13);

    return {
      key: c.cluster_key,
      label: c.label || `C${c.cluster_key}`,
      share: c.share,
      x,
      y,
      r,
      heat: clamp(ratio, 0, 1),
      unstable: Boolean(c.cip?.label_unstable),
    };
  });

  // Simple collision relaxation to keep bubbles separated.
  for (let step = 0; step < 260; step += 1) {
    for (let i = 0; i < bubbles.length; i += 1) {
      const a = bubbles[i];
      for (let j = i + 1; j < bubbles.length; j += 1) {
        const b = bubbles[j];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 0.0001;
        const minDist = a.r + b.r + 8;
        if (dist < minDist) {
          const push = (minDist - dist) * 0.5;
          const ux = dx / dist;
          const uy = dy / dist;
          a.x -= ux * push;
          a.y -= uy * push;
          b.x += ux * push;
          b.y += uy * push;
        }
      }
      a.x = clamp(a.x, a.r + 8, W - a.r - 8);
      a.y = clamp(a.y, a.r + 8, H - a.r - 8);
    }
  }

  return bubbles.sort((a, b) => a.x - b.x);
}

export function ClusterBubbleChart({ clusters, selectedKey, onSelect, sizeMode = "engagement" }: Props) {
  const bubbles = useMemo(() => buildBubbles(clusters, sizeMode), [clusters, sizeMode]);

  if (!bubbles.length) {
    return <div className="empty-note">此貼文未產生 cluster。</div>;
  }

  return (
    <div className="bubble-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="bubble-svg" role="img" aria-label="cluster map">
        {bubbles.slice(0, -1).map((a, idx) => {
          const b = bubbles[idx + 1];
          return <line key={`line-${a.key}-${b.key}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className="bubble-edge" />;
        })}

        {bubbles.map((b) => {
          const active = b.key === selectedKey;
          return (
            <g
              key={b.key}
              className={clsx("bubble-node", active && "is-active", b.unstable && "is-unstable")}
              onClick={() => onSelect(b.key)}
            >
              <circle cx={b.x} cy={b.y} r={b.r + 1.5} className="bubble-ring" />
              <circle
                cx={b.x}
                cy={b.y}
                r={b.r}
                className="bubble-circle"
                style={{ ["--heat" as string]: String(b.heat) }}
              />
              <text x={b.x} y={b.y - 5} className="bubble-text bubble-title" textAnchor="middle">
                C{b.key}
              </text>
              <text x={b.x} y={b.y + 14} className="bubble-text bubble-sub" textAnchor="middle">
                {fmtPct(b.share)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
