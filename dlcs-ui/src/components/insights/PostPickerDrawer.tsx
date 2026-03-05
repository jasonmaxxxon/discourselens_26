import { useMemo, useState } from "react";
import type { PostItem } from "../../lib/types";
import { fmtDate, fmtNumber } from "../../lib/format";

type Props = {
  open: boolean;
  posts: PostItem[];
  selectedPostId?: string;
  onSelect: (postId: string) => void;
  onClose: () => void;
};

function textOf(post: PostItem): string {
  return `${post.author || ""} ${post.snippet || ""}`.toLowerCase();
}

export function PostPickerDrawer({ open, posts, selectedPostId, onSelect, onClose }: Props) {
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return posts;
    return posts.filter((p) => textOf(p).includes(needle));
  }, [posts, q]);

  if (!open) return null;

  return (
    <>
      <button className="picker-backdrop" onClick={onClose} aria-label="Close post picker" />
      <aside className="picker-drawer" role="dialog" aria-label="Post picker">
        <header className="picker-head">
          <div className="picker-title">Select Post</div>
          <button type="button" className="job-drawer-close" onClick={onClose}>
            close
          </button>
        </header>
        <div className="picker-search">
          <input
            className="text-input"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search author / content..."
            autoFocus
          />
        </div>
        <div className="picker-list">
          {filtered.map((post) => (
            <button
              key={post.id}
              type="button"
              className={`picker-item ${selectedPostId === post.id ? "active" : ""}`}
              onClick={() => {
                onSelect(post.id);
                onClose();
              }}
            >
              <div className="picker-item-top">
                <span>{post.author || "unknown"}</span>
                <span>{fmtDate(post.created_at)}</span>
              </div>
              <div className="picker-item-snippet">{post.snippet || "-"}</div>
              <div className="picker-item-meta">
                ♥ {fmtNumber(post.like_count)} · 💬 {fmtNumber(post.reply_count)} · 👁 {fmtNumber(post.view_count)}
              </div>
            </button>
          ))}
          {!filtered.length ? <div className="empty-note">No matched posts.</div> : null}
        </div>
      </aside>
    </>
  );
}
