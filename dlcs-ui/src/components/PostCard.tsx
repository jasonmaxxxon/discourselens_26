import { Link } from "react-router-dom";
import type { PostItem } from "../lib/types";
import { fmtDate, fmtNumber } from "../lib/format";

type Variant = "compact" | "standard" | "dense";

type Props = {
  post: PostItem;
  variant?: Variant;
  showActions?: boolean;
};

function getIntegrity(post: PostItem): string {
  if (post.analysis_is_valid === true) return "pass";
  if (post.analysis_is_valid === false) return "partial";
  return "unknown";
}

export function PostCard({ post, variant = "compact", showActions }: Props) {
  const cls = `postcard ${variant}`;
  const snippet = String(post.snippet || "").trim();
  const canShowActions = typeof showActions === "boolean" ? showActions : variant === "standard";

  return (
    <article className={cls}>
      <div className="postcard-head">
        <div className="postcard-author">{post.author || "unknown"}</div>
        <div className="postcard-time">{fmtDate(post.created_at)}</div>
      </div>
      <p className="postcard-text">{snippet || "-"}</p>
      <div className="postcard-meta">
        <span>Likes <span className="metric-number-inline">{fmtNumber(post.like_count)}</span></span>
        <span>Replies <span className="metric-number-inline">{fmtNumber(post.reply_count)}</span></span>
        <span>Reposts <span className="metric-number-inline">{fmtNumber(post.repost_count ?? 0)}</span></span>
        <span>Shares <span className="metric-number-inline">{fmtNumber(post.share_count ?? 0)}</span></span>
        <span>Views <span className="metric-number-inline">{fmtNumber(post.view_count)}</span></span>
      </div>
      <div className="postcard-foot">
        <span className="pill">integrity {getIntegrity(post)}</span>
        {canShowActions ? (
          <div className="postcard-actions">
            <Link to="/insights">Insights</Link>
            <Link to="/library">Library</Link>
            <Link to="/review">Review</Link>
          </div>
        ) : null}
      </div>
    </article>
  );
}
