import { useMemo } from "react";
import type { CommentItem } from "../lib/types";
import { filterCommentsByRange } from "../lib/timelineEvolve";
import { fmtDate, fmtNumber } from "../lib/format";

type CommentMomentumPanelProps = {
  comments: CommentItem[];
  loading?: boolean;
  degraded?: boolean;
  t0?: string | null;
  t1?: string | null;
  q?: string;
  author?: string;
  onCommentContext?: (comment: CommentItem, point: { x: number; y: number }) => void;
};

function includesKeyword(text: string, query: string): boolean {
  if (!query.trim()) return true;
  return text.toLowerCase().includes(query.trim().toLowerCase());
}

export function CommentMomentumPanel({
  comments,
  loading = false,
  degraded = false,
  t0,
  t1,
  q = "",
  author = "",
  onCommentContext,
}: CommentMomentumPanelProps) {
  const filtered = useMemo(() => {
    const byRange = filterCommentsByRange(comments, t0, t1);
    const byAuthor = author.trim()
      ? byRange.filter((row) => String(row.author_handle || "").toLowerCase() === author.trim().toLowerCase())
      : byRange;
    const byQuery = byAuthor.filter((row) => includesKeyword(String(row.text || ""), q));
    return [...byQuery].sort((a, b) => {
      const tb = new Date(String(b.created_at || "")).getTime();
      const ta = new Date(String(a.created_at || "")).getTime();
      return (Number.isFinite(tb) ? tb : 0) - (Number.isFinite(ta) ? ta : 0);
    });
  }, [author, comments, q, t0, t1]);

  const hasData = filtered.length > 0;

  return (
    <section className="comment-momentum-panel" data-testid="comment-momentum-panel">
      <header className="comment-momentum-head">
        <div>
          <h4>Comment Momentum</h4>
          <p>{hasData ? `${filtered.length} comments in window` : "No comments in current window"}</p>
        </div>
        {degraded ? <span className="signal-pill warn">degraded</span> : null}
      </header>

      {loading && !hasData ? (
        <div className="skeleton-stack" aria-label="comment momentum loading">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={`cmt-sk-${i}`} className="skeleton-card" />
          ))}
        </div>
      ) : hasData ? (
        <div className="momentum-list" role="list">
          {filtered.slice(0, 40).map((comment) => (
            <article
              key={comment.id}
              className="momentum-item"
              role="listitem"
              onContextMenu={(event) => {
                event.preventDefault();
                onCommentContext?.(comment, { x: event.clientX, y: event.clientY });
              }}
            >
              <div className="momentum-item-head">
                <strong>{comment.author_handle || "anonymous"}</strong>
                <span>{fmtDate(comment.created_at || null)}</span>
              </div>
              <p>{comment.text || "(no text)"}</p>
              <div className="momentum-item-meta">
                <span>{fmtNumber(comment.like_count || 0)} likes</span>
                <span>{fmtNumber(comment.reply_count || 0)} replies</span>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-note">{q || author ? "No comments matched current filters" : "Start by selecting a time bucket or entering a query"}</div>
      )}
    </section>
  );
}
