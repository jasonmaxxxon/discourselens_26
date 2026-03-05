import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

export type StitchActionMeta = Record<string, unknown>;
export type StitchActionHandler = (action: string, meta: StitchActionMeta) => void | Promise<void>;

export type StitchNotice = {
  message: string;
  kind?: "info" | "ok" | "error";
  nonce: number;
};

type NavMap = Record<string, string>;
type ActionMap = Record<string, string>;
type ActionSelectorMap = Record<string, string>;

type Props = {
  html: string;
  title: string;
  navMap?: NavMap;
  actionMap?: ActionMap;
  actionSelectorMap?: ActionSelectorMap;
  onAction?: StitchActionHandler;
  bridgeData?: Record<string, unknown>;
  notice?: StitchNotice | null;
  pageId?: "overview" | "pipeline" | "insights" | "library" | "review";
  hideTemplateHeader?: boolean;
};

function normalizeMap(map: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(map).map(([k, v]) => [String(k).trim().toLowerCase(), v]));
}

const BRIDGE_CACHE_PREFIX = "dl.stitch.bridge.v1.";

function hasMeaningfulValue(value: unknown): boolean {
  if (value == null) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    return text.length > 0 && text !== "-" && text !== "idle" && text !== "unknown";
  }
  if (typeof value === "number") return Number.isFinite(value) && value !== 0;
  if (typeof value === "boolean") return value;
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>).some((entry) => hasMeaningfulValue(entry));
  }
  return false;
}

function hasBridgePayload(pageId: string, payload: Record<string, unknown> | null | undefined): boolean {
  if (!payload) return false;
  if (pageId === "overview") {
    return (
      Number(payload.activeCount || 0) > 0 ||
      Number(payload.queuedCount || 0) > 0 ||
      Number(payload.failedCount || 0) > 0 ||
      hasMeaningfulValue(payload.currentRunId) ||
      hasMeaningfulValue(payload.events) ||
      hasMeaningfulValue(payload.timeline)
    );
  }
  if (pageId === "pipeline") {
    return (
      hasMeaningfulValue(payload.activeJob) ||
      hasMeaningfulValue(payload.queuedJobs) ||
      hasMeaningfulValue(payload.logs) ||
      hasMeaningfulValue(payload.stageLabel)
    );
  }
  if (pageId === "insights") {
    return (
      Number(payload.nodes || 0) > 0 ||
      Number(payload.edges || 0) > 0 ||
      hasMeaningfulValue(payload.stack) ||
      hasMeaningfulValue(payload.axis) ||
      hasMeaningfulValue(payload.stability)
    );
  }
  if (pageId === "library") {
    return hasMeaningfulValue(payload.items) || hasMeaningfulValue(payload.selected) || Number(payload.total || 0) > 0;
  }
  if (pageId === "review") {
    return hasMeaningfulValue(payload.cards) || hasMeaningfulValue(payload.selected) || Number(payload.total || 0) > 0;
  }
  return hasMeaningfulValue(payload);
}

function readBridgePayloadCache(pageId: string): Record<string, unknown> | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(`${BRIDGE_CACHE_PREFIX}${pageId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { payload?: Record<string, unknown> } | Record<string, unknown>;
    if (parsed && typeof parsed === "object") {
      const payload = "payload" in parsed ? parsed.payload : parsed;
      if (payload && typeof payload === "object") return payload as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}

function writeBridgePayloadCache(pageId: string, payload: Record<string, unknown>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      `${BRIDGE_CACHE_PREFIX}${pageId}`,
      JSON.stringify({ updatedAt: new Date().toISOString(), payload })
    );
  } catch {
    // ignore localStorage errors
  }
}

function buildSrcDoc(
  html: string,
  opts: {
    navMap: NavMap;
    actionMap: ActionMap;
    actionSelectorMap: ActionSelectorMap;
    pageId: string;
    hideTemplateHeader: boolean;
  }
): string {
  const routeJson = JSON.stringify(normalizeMap(opts.navMap));
  const actionJson = JSON.stringify(normalizeMap(opts.actionMap));
  const selectorJson = JSON.stringify(opts.actionSelectorMap || {});
  const pageIdJson = JSON.stringify(opts.pageId);
  const hideTemplateHeaderJson = JSON.stringify(Boolean(opts.hideTemplateHeader));

  const bridge = `
<script>
(() => {
  const routeMap = ${routeJson};
  const actionByText = ${actionJson};
  const actionBySelector = ${selectorJson};
  const pageId = ${pageIdJson};
  const hideTemplateHeader = ${hideTemplateHeaderJson};
  const normalize = (s) => String(s || "").trim().replace(/\s+/g, " ").toLowerCase();

  const style = document.createElement("style");
  const styleLines = [
    ":root { --stitch-ease: cubic-bezier(0.22, 1, 0.36, 1); }",
    "html, body { min-height: 100%; overflow-x: hidden !important; }",
    "body { overflow-y: auto !important; }",
    "body { transition: opacity 190ms var(--stitch-ease), transform 240ms var(--stitch-ease); }",
    "body.stitch-route-leave { opacity: 0; transform: translateY(8px) scale(0.996); }",
    "a[href], button, [data-stitch-action], tr[data-phen-id] { transition: transform 150ms var(--stitch-ease), box-shadow 180ms var(--stitch-ease), filter 160ms var(--stitch-ease); }",
    "[data-stitch-press='1'] { transform: scale(0.975); }",
    ".stitch-demo-badge { position: fixed; right: 20px; top: 20px; z-index: 9999; background: rgba(245,158,11,0.95); color: #3f2f00; border: 1px solid rgba(120,53,15,0.25); border-radius: 999px; padding: 6px 10px; font: 700 11px/1.2 Inter,sans-serif; letter-spacing: 0.06em; text-transform: uppercase; box-shadow: 0 8px 22px rgba(15,23,42,0.16); }",
    ".stitch-toast { position: fixed; left: 50%; bottom: 18px; transform: translateX(-50%); z-index: 9999; border-radius: 12px; padding: 8px 12px; font: 600 12px/1.3 Inter,sans-serif; box-shadow: 0 12px 30px rgba(15,23,42,0.2); border: 1px solid rgba(255,255,255,0.55); backdrop-filter: blur(8px); }",
    ".stitch-toast.info { background: rgba(248,250,252,0.9); color: #334155; }",
    ".stitch-toast.ok { background: rgba(220,252,231,0.92); color: #166534; }",
    ".stitch-toast.error { background: rgba(254,226,226,0.92); color: #991b1b; }",
    ".stitch-run-status { position:relative; overflow:hidden; display:flex; flex-direction:column; align-items:stretch; gap:6px; margin-top:10px; padding:7px 10px; border-radius:10px; border:1px solid rgba(148,163,184,0.22); background:rgba(255,255,255,0.56); font: 700 11px/1.2 Inter,sans-serif; letter-spacing:0.02em; color:#334155; }",
    ".stitch-run-status .stitch-run-row { display:flex; align-items:center; gap:8px; }",
    ".stitch-run-status .stitch-run-track { position:relative; display:block; width:100%; height:4px; border-radius:999px; background:rgba(148,163,184,0.24); overflow:hidden; }",
    ".stitch-run-status .stitch-run-track-bar { position:absolute; inset:0 auto 0 0; width:32%; border-radius:999px; background:linear-gradient(90deg, rgba(99,102,241,0.35), rgba(99,102,241,0.92), rgba(129,140,248,0.35)); animation: stitch-loader-slide 1.05s ease-in-out infinite; }",
    ".stitch-run-status[data-tone='loading'] { color:#4338ca; border-color: rgba(129,140,248,0.35); background: rgba(224,231,255,0.65); }",
    ".stitch-run-status[data-tone='done'] { color:#166534; border-color: rgba(74,222,128,0.38); background: rgba(220,252,231,0.72); }",
    ".stitch-run-status[data-tone='warn'] { color:#92400e; border-color: rgba(251,191,36,0.4); background: rgba(254,243,199,0.8); }",
    ".stitch-run-status[data-tone='warn'] .stitch-run-track-bar { background:linear-gradient(90deg, rgba(245,158,11,0.25), rgba(245,158,11,0.95), rgba(251,191,36,0.2)); }",
    ".stitch-run-status[data-tone='error'] { color:#991b1b; border-color: rgba(248,113,113,0.35); background: rgba(254,226,226,0.72); }",
    ".stitch-run-spinner { width: 11px; height: 11px; border-radius: 999px; border: 2px solid rgba(99,102,241,0.25); border-top-color: rgba(99,102,241,0.95); animation: stitch-spin 0.7s linear infinite; }",
    ".stitch-run-pulse { width:8px; height:8px; border-radius:999px; background:#22c55e; box-shadow:0 0 0 rgba(34,197,94,0.55); animation: stitch-pulse 1.3s ease-out infinite; }",
    "@keyframes stitch-spin { to { transform: rotate(360deg); } }",
    "@keyframes stitch-pulse { 0% { box-shadow:0 0 0 0 rgba(34,197,94,0.55); } 100% { box-shadow:0 0 0 10px rgba(34,197,94,0); } }",
    "@keyframes stitch-loader-slide { 0% { transform: translateX(-120%); } 100% { transform: translateX(320%); } }"
  ];
  if (hideTemplateHeader) {
    styleLines.push("body > header { display: none !important; }");
  }
  style.textContent = styleLines.join("\\n");
  document.head.appendChild(style);

  let toastTimer = null;

  const qs = (selector, root) => {
    try {
      return (root || document).querySelector(selector);
    } catch {
      return null;
    }
  };
  const qsa = (selector, root) => {
    try {
      return Array.from((root || document).querySelectorAll(selector));
    } catch {
      return [];
    }
  };

  const escapeHtml = (value) => String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");

  const post = (payload) => window.parent.postMessage(payload, "*");
  const reportHeight = () => {
    const htmlH = document.documentElement ? document.documentElement.scrollHeight : 0;
    const bodyH = document.body ? document.body.scrollHeight : 0;
    const h = Math.max(htmlH, bodyH, window.innerHeight || 0);
    post({ type: "stitch:height", height: h });
  };

  const unlockPageScroll = () => {
    document.body.classList.remove("overflow-hidden", "h-screen");
    document.body.style.overflowY = "auto";
    document.body.style.overflowX = "hidden";
    const main = document.querySelector("main.overflow-hidden");
    if (main instanceof HTMLElement) {
      main.classList.remove("overflow-hidden");
      if (!main.classList.contains("overflow-y-auto")) {
        main.classList.add("overflow-y-auto");
      }
    }
  };

  const pulse = (el) => {
    if (!el) return;
    el.setAttribute("data-stitch-press", "1");
    setTimeout(() => el.removeAttribute("data-stitch-press"), 130);
  };

  const showDemoBadge = (text) => {
    const label = String(text || "").trim();
    if (!label) return;
    let badge = document.getElementById("stitch-demo-badge");
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "stitch-demo-badge";
      badge.className = "stitch-demo-badge";
      document.body.appendChild(badge);
    }
    badge.textContent = label;
  };

  const showToast = (message, kind) => {
    const msg = String(message || "").trim();
    if (!msg) return;
    let el = document.getElementById("stitch-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "stitch-toast";
      document.body.appendChild(el);
    }
    el.className = "stitch-toast " + (kind || "info");
    el.textContent = msg;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      if (el) el.remove();
      toastTimer = null;
    }, 1900);
  };

  const collectMeta = () => {
    const endpointEl = qs("input[placeholder*='https://'], input[type='text'][value*='threads']");
    const envChecked = qs("input[name='env']:checked");
    const envLabel = envChecked ? (envChecked.closest("label") || envChecked.parentElement) : null;
    const selectEl = qs("select");
    const searchEl = qs(
      "input[placeholder*='Search hash, ID, or tag'], input[placeholder*='Search hash, keyword, or ID'], input[placeholder*='Search Node ID']"
    );
    return {
      endpointUrl: endpointEl ? endpointEl.value || "" : "",
      environment: envLabel ? String(envLabel.textContent || "").trim() : "",
      extractionMode: selectEl ? selectEl.value || "" : "",
      searchQuery: searchEl ? searchEl.value || "" : "",
      selectedPhenomenonId: document.body.dataset.selectedPhenomenonId || null,
      pageId,
    };
  };

  const markAsAction = (el, action) => {
    if (!el || !action) return;
    el.setAttribute("data-stitch-action", action);
    el.style.cursor = "pointer";
  };

  const applyInsightsChromeActions = () => {
    if (pageId !== "insights") return;
    const stackHeading = qsa("h2").find((el) => {
      const key = normalize(el.textContent);
      return key === "narrative stack" || key === "cluster detail";
    });
    if (!stackHeading) return;
    const dotsWrap = stackHeading.parentElement ? stackHeading.parentElement.querySelector("div.flex.gap-2") : null;
    if (!dotsWrap) return;
    const dots = Array.from(dotsWrap.children).filter((el) => el instanceof HTMLElement);
    if (dots[0]) markAsAction(dots[0], "insights_stack_primary");
    if (dots[1]) markAsAction(dots[1], "insights_stack_secondary");
  };

  const applyRoutesAndActions = () => {
    qsa("a[href='#']").forEach((anchor) => {
      const key = normalize(anchor.textContent);
      const routeEntry = Object.entries(routeMap).find(([label]) => {
        if (!label) return false;
        return key === label || key.includes(label);
      });
      const route = routeEntry ? routeEntry[1] : "";
      if (route) {
        anchor.setAttribute("href", route);
        anchor.setAttribute("data-stitch-route", route);
      }
    });

    qsa("button,a,[role='button']").forEach((el) => {
      const key = normalize(el.textContent);
      const actionEntry = Object.entries(actionByText).find(([label]) => {
        if (!label) return false;
        return key === label || key.includes(label);
      });
      const action = actionEntry ? actionEntry[1] : "";
      if (action) {
        el.setAttribute("data-stitch-action", action);
        el.style.cursor = "pointer";
      }
    });

    Object.entries(actionBySelector).forEach(([selector, action]) => {
      qsa(selector).forEach((el) => {
        el.setAttribute("data-stitch-action", action);
        el.style.cursor = "pointer";
      });
    });

    applyInsightsChromeActions();
  };

  const routeLeave = (href) => {
    document.body.classList.add("stitch-route-leave");
    setTimeout(() => {
      post({ type: "stitch:navigate", href });
      requestAnimationFrame(() => document.body.classList.remove("stitch-route-leave"));
    }, 120);
  };

  const fmtPct = (value) => {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return "0%";
    const pct = n <= 1 ? n * 100 : n;
    return Math.max(0, Math.min(100, pct)).toFixed(0) + "%";
  };

  const fmtShortId = (id) => {
    const s = String(id || "");
    return s ? s.slice(0, 8) : "-";
  };

  const statusTone = (status) => {
    const s = normalize(status);
    if (s.includes("stable") || s.includes("active") || s.includes("ok")) return "good";
    if (s.includes("degrad") || s.includes("risk") || s.includes("warn")) return "warn";
    if (s.includes("fail") || s.includes("critical")) return "bad";
    return "neutral";
  };

  const fmtClockUtc = (isoText) => {
    if (!isoText) return "-";
    const d = new Date(String(isoText));
    if (!Number.isFinite(d.getTime())) return "-";
    return d.toISOString().slice(11, 19) + " UTC";
  };

  const fmtDuration = (fromIso, toIso, nowMs) => {
    const a = new Date(String(fromIso || ""));
    if (!Number.isFinite(a.getTime())) return "--:--:--";
    const b = toIso ? new Date(String(toIso)) : (Number.isFinite(Number(nowMs)) ? new Date(Number(nowMs)) : new Date());
    const end = Number.isFinite(b.getTime()) ? b : new Date();
    let sec = Math.max(0, Math.floor((end.getTime() - a.getTime()) / 1000));
    const hh = String(Math.floor(sec / 3600)).padStart(2, "0");
    sec %= 3600;
    const mm = String(Math.floor(sec / 60)).padStart(2, "0");
    const ss = String(sec % 60).padStart(2, "0");
    return hh + ":" + mm + ":" + ss;
  };

  const fmtSince = (isoText, nowMs) => {
    if (!isoText) return "-";
    const d = new Date(String(isoText));
    if (!Number.isFinite(d.getTime())) return "-";
    const base = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    const diff = Math.max(0, Math.floor((base - d.getTime()) / 1000));
    if (diff < 60) return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    return Math.floor(diff / 3600) + "h ago";
  };

  const setPipelineMetric = (label, value, tone) => {
    const labelEl = qsa("span").find((el) => normalize(el.textContent) === normalize(label));
    if (!labelEl) return;
    const card = labelEl.closest("div.flex.flex-col");
    if (!card) return;
    const valueEl = card.querySelector("span.text-sm");
    if (!valueEl) return;
    valueEl.textContent = String(value || "-");
    valueEl.classList.remove("text-emerald-600", "text-slate-700");
    valueEl.classList.add(tone === "ok" ? "text-emerald-600" : "text-slate-700");
  };

  const renderPipelineQueued = (rows, selectedId) => {
    const list = Array.isArray(rows) ? rows : [];
    return list.map((row) => {
      const rowId = String(row.id || "");
      const name = escapeHtml(row.name || row.pipeline_type || "Queued_Run");
      const id = escapeHtml(fmtShortId(row.id));
      const status = escapeHtml(String(row.status || "queued"));
      const selected = rowId && String(selectedId || "") === rowId;
      const shellClass = selected
        ? "group flex items-center gap-3 p-3 rounded-xl border border-indigo-300/70 ring-1 ring-indigo-300/50 transition-all cursor-pointer shadow-sm bg-indigo-50/40"
        : "group flex items-center gap-3 p-3 rounded-xl hover:bg-white/60 border border-transparent hover:border-white/60 transition-all cursor-pointer shadow-sm hover:shadow-md bg-white/20";
      return "<div class='" + shellClass + "' data-queued-id='" + escapeHtml(rowId) + "'>"
        + "<div class='size-9 rounded-lg bg-white/50 flex items-center justify-center border border-white/50 text-slate-400 group-hover:text-indigo-500 shadow-sm transition-colors'><span class='material-symbols-outlined text-[18px]'>schedule</span></div>"
        + "<div class='flex-1 min-w-0'><div class='flex justify-between items-center mb-0.5'><span class='text-xs font-bold text-slate-700 truncate'>" + name + "</span></div>"
        + "<div class='flex justify-between items-center'><span class='text-[10px] text-slate-400 font-mono'>ID: " + id + "</span><span class='text-[10px] text-slate-500 font-medium'>" + status + "</span></div></div></div>";
    }).join("");
  };

  const renderPipelineLogs = (lines) => {
    const arr = Array.isArray(lines) ? lines : [];
    return arr.map((line, idx) => {
      const raw = String(line || "");
      const parts = raw.split("|");
      const time = escapeHtml(parts[0] || "--:--:--");
      const msg = escapeHtml(parts.slice(1).join("|") || raw);
      const dot = idx % 5 === 0 ? "bg-amber-400" : "bg-emerald-400";
      return "<div class='group grid grid-cols-[80px_1fr_24px] gap-2 px-3 py-2 hover:bg-white/40 rounded-lg items-start transition-colors'>"
        + "<span class='text-slate-400 text-[11px]'>" + time + "</span>"
        + "<span class='text-slate-700 break-words font-medium'>" + msg + "</span>"
        + "<div class='flex justify-center mt-1'><span class='size-2 rounded-full " + dot + " shadow-sm'></span></div></div>";
    }).join("");
  };

  const updatePipeline = (payload) => {
    const data = payload || {};
    if (data.demoLabel) showDemoBadge(data.demoLabel);

    const endpointLabel = qsa("label").find((el) => normalize(el.textContent) === "endpoint url");
    if (endpointLabel) endpointLabel.textContent = "Threads Link";
    const endpoint = qs("input[placeholder*='https://']");
    if (endpoint && typeof data.endpointUrl === "string") endpoint.value = data.endpointUrl;
    if (endpoint) endpoint.setAttribute("placeholder", "https://www.threads.com/@user/post/...");
    const envLabel = qsa("label").find((el) => normalize(el.textContent) === "environment");
    const envBlock = envLabel ? envLabel.closest("div.flex.flex-col.gap-2") : null;
    if (envBlock) envBlock.style.display = "none";

    const active = data.activeJob || null;
    const display = data.displayJob || active || null;
    const isLive = Boolean(active && display && String(active.id || "") === String(display.id || ""));
    document.body.dataset.selectedJobId = String(data.selectedJobId || display?.id || "");
    const title = qs("section h3.text-2xl");
    if (title) {
      title.textContent = display ? "Run_" + fmtShortId(display.id) : "No_Active_Run";
    }

    const statusBadge = qs("section span.uppercase.tracking-wide");
    if (statusBadge) {
      statusBadge.textContent = display ? String(data.uiStatus || display.status || "running") : "idle";
    }

    const idEl = qsa("section p.font-mono").find((el) => normalize(el.textContent).startsWith("id:"));
    if (idEl) {
      idEl.textContent = "ID: " + (display ? fmtShortId(display.id) : "-");
    }

    const stageText = String(data.stageLabel || (display ? String(display.status || "processing") : "idle"));
    const stageLabel = qsa("section span").find((el) => normalize(el.textContent).startsWith("stage"));
    if (stageLabel) {
      stageLabel.textContent = display ? stageText : "Stage 0/5: idle";
    }

    const runCard = title ? title.closest("div.w-full.max-w-2xl") : null;
    if (runCard instanceof HTMLElement) {
      let statusRail = runCard.querySelector("[data-pipeline-run-status='1']");
      if (!statusRail) {
        statusRail = document.createElement("div");
        statusRail.setAttribute("data-pipeline-run-status", "1");
        statusRail.className = "stitch-run-status";
        const idRow = qsa("p", runCard).find((el) => normalize(el.textContent).startsWith("id:"));
        if (idRow && idRow.parentElement) {
          idRow.parentElement.appendChild(statusRail);
        } else {
          runCard.insertBefore(statusRail, runCard.firstChild);
        }
      }
      const rawStatus = String(data.uiStatus || display?.status || "idle").toLowerCase();
      const isFetching = Boolean(data.isFetching);
      const isComplete = Boolean(data.isComplete);
      const isError = rawStatus.includes("fail") || rawStatus.includes("error") || rawStatus.includes("cancel");
      const isStalled = Boolean(data.maybeStuck);
      const lagMs = Number(data.heartbeatLagMs || 0);
      const lagMin = Math.max(1, Math.floor(lagMs / 60000));
      if (isFetching) {
        statusRail.setAttribute("data-tone", "loading");
        statusRail.innerHTML = "<div class='stitch-run-row'><span class='stitch-run-spinner'></span><span>" + escapeHtml(Boolean(data.isStarting) ? "Starting crawler..." : "Fetching status...") + "</span></div><span class='stitch-run-track'><span class='stitch-run-track-bar'></span></span>";
      } else if (isComplete) {
        statusRail.setAttribute("data-tone", "done");
        statusRail.innerHTML = "<div class='stitch-run-row'><span class='stitch-run-pulse'></span><span>Completed · Ready for Insights</span></div>";
      } else if (isError) {
        statusRail.setAttribute("data-tone", "error");
        statusRail.innerHTML = "<div class='stitch-run-row'><span class='material-symbols-outlined text-[14px]'>error</span><span>Run ended with errors</span></div>";
      } else if (isStalled && display) {
        statusRail.setAttribute("data-tone", "warn");
        statusRail.innerHTML = "<div class='stitch-run-row'><span class='material-symbols-outlined text-[14px]'>warning</span><span>No heartbeat for " + escapeHtml(String(lagMin)) + "m · still retrying...</span></div><span class='stitch-run-track'><span class='stitch-run-track-bar'></span></span>";
      } else if (display) {
        statusRail.setAttribute("data-tone", "loading");
        statusRail.innerHTML = "<div class='stitch-run-row'><span class='stitch-run-spinner'></span><span>Running · " + escapeHtml(String(display.status || "processing")) + "</span></div><span class='stitch-run-track'><span class='stitch-run-track-bar'></span></span>";
      } else {
        statusRail.remove();
      }

      let ctaRow = runCard.querySelector("[data-pipeline-cta='1']");
      if (!ctaRow) {
        ctaRow = document.createElement("div");
        ctaRow.setAttribute("data-pipeline-cta", "1");
        ctaRow.className = "mt-3 pt-3 border-t border-white/50 flex items-center justify-end";
        runCard.appendChild(ctaRow);
      }
      const postId = String(data.insightsPostId || "");
      if (Boolean(data.canOpenInsights)) {
        ctaRow.innerHTML = "<button type='button' data-stitch-action='pipeline_open_insights' data-post-id='" + escapeHtml(postId) + "' class='px-3 py-1.5 rounded-lg text-[10px] uppercase font-bold bg-primary/15 border border-primary/30 text-primary hover:bg-primary/20 transition-colors'>Open Insights</button>";
      } else {
        ctaRow.innerHTML = "";
      }
    }

    const contextHeading = qsa("h2").find((el) => normalize(el.textContent).includes("current context"));
    const contextTools = contextHeading?.parentElement?.querySelector("div.flex.items-center.gap-3");
    if (contextTools) contextTools.style.display = "none";

    const pctEl = qsa("section span").find((el) => /%$/.test(String(el.textContent || "").trim()));
    const processed = Number((display && display.processed_count) || 0);
    const total = Number((display && display.total_count) || 0);
    const progress = total > 0 ? processed / total : 0;
    if (pctEl) pctEl.textContent = fmtPct(progress);
    const bar = qs("div.liquid-progress");
    if (bar) bar.style.width = fmtPct(progress);

    const stopButton = runCard ? runCard.querySelector("button") : qsa("button").find((el) => normalize(el.textContent).includes("stop task"));
    if (stopButton) {
      stopButton.style.opacity = isLive ? "1" : "0";
      stopButton.style.pointerEvents = isLive ? "auto" : "none";
    }

    setPipelineMetric("Start Time", display ? fmtClockUtc(display.created_at) : "-");
    setPipelineMetric("Duration", display ? fmtDuration(display.created_at, display.finished_at || undefined, data.clientNowMs) : "--:--:--");
    setPipelineMetric("Heartbeat", display ? fmtSince(display.updated_at || display.created_at, data.clientNowMs) : "-", "ok");

    const queuedRows = Array.isArray(data.queuedJobs) ? data.queuedJobs : [];
    const queuedTitle = qsa("h3").find((el) => normalize(el.textContent) === "queued runs");
    const queuedPane = queuedTitle ? queuedTitle.closest("div") && queuedTitle.closest("div").parentElement : null;
    const queuedListWrap =
      (queuedPane && queuedPane.querySelector(".overflow-y-auto")) ||
      qsa("div.overflow-y-auto").find((el) => String(el.parentElement?.className || "").includes("h-1/3"));
    const queuedBadge =
      (queuedPane && queuedPane.querySelector("span")) ||
      (queuedTitle && queuedTitle.parentElement && queuedTitle.parentElement.querySelector("span"));
    if (queuedBadge) queuedBadge.textContent = String(queuedRows.length);
    if (queuedTitle) {
      let hint = queuedTitle.parentElement ? queuedTitle.parentElement.querySelector("[data-queued-hint='1']") : null;
      if (!hint) {
        hint = document.createElement("span");
        hint.setAttribute("data-queued-hint", "1");
        hint.className = "ml-2 text-[10px] font-medium text-amber-700 bg-amber-100/80 border border-amber-200 rounded-full px-2 py-0.5";
        queuedTitle.parentElement?.appendChild(hint);
      }
      hint.textContent = String(data.queuedHint || "SAMPLE / TEST DATA FOR UI PURPOSE");
    }
    if (queuedListWrap) {
      queuedListWrap.innerHTML = queuedRows.length
        ? renderPipelineQueued(queuedRows.slice(0, 6), data.selectedJobId || "")
        : "<div class='p-4 text-xs text-slate-500'>No historical runs.</div>";
    }

    const logsWrap = qsa("div.overflow-y-auto").find((el) => el.classList.contains("font-mono") && el.classList.contains("text-xs"));
    const logLines = Array.isArray(data.logs) ? data.logs : [];
    if (logsWrap) {
      logsWrap.innerHTML = logLines.length
        ? renderPipelineLogs(logLines.slice(-18))
        : "<div class='p-4 text-xs text-slate-500'>No logs yet.</div>";
    }
    reportHeight();
  };

  const renderLibraryRows = (items) => {
    const rows = Array.isArray(items) ? items : [];
    return rows.map((item) => {
      const status = String(item.status || "unknown");
      const tone = statusTone(status);
      const dotClass = tone === "good" ? "bg-accent-green" : tone === "warn" ? "bg-accent-amber" : tone === "bad" ? "bg-accent-red" : "bg-slate-400";
      const risk = tone === "bad" ? "Critical" : tone === "warn" ? "Medium" : "Low";
      const riskClass = tone === "bad" ? "bg-accent-red/10 text-accent-red border-accent-red/20" : tone === "warn" ? "bg-accent-amber/10 text-accent-amber border-accent-amber/20" : "bg-slate-200 text-slate-500 border-slate-300";
      const pid = escapeHtml(String(item.id || ""));
      return "<tr class='glass-row group cursor-pointer' data-phen-id='" + pid + "'>"
        + "<td class='px-6 py-3'><input class='rounded border-slate-300 bg-white/50 text-primary focus:ring-offset-0 focus:ring-primary/30 h-3.5 w-3.5' type='checkbox'></td>"
        + "<td class='px-6 py-3 font-medium text-slate-700'><div class='flex items-center gap-2'><span class='material-symbols-outlined text-[14px] text-slate-400'>token</span>"
        + escapeHtml(item.name || "-") + "</div></td>"
        + "<td class='px-6 py-3 font-mono text-slate-500 text-right'>" + escapeHtml(String(item.totalPosts || 0)) + "</td>"
        + "<td class='px-6 py-3 font-mono text-slate-500'>" + escapeHtml(item.lastSeen || "-") + "</td>"
        + "<td class='px-6 py-3 font-mono text-slate-400'>" + escapeHtml(item.fingerprint || "-") + "</td>"
        + "<td class='px-6 py-3'><div class='flex items-center gap-2'><div class='h-2 w-2 rounded-full " + dotClass + " status-dot'></div><span class='text-slate-600 font-medium'>"
        + escapeHtml(status) + "</span></div></td>"
        + "<td class='px-6 py-3 text-right'><span class='inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold " + riskClass + " border shadow-sm'>"
        + risk + "</span></td>"
        + "<td class='px-6 py-3 text-right opacity-0 group-hover:opacity-100 transition-opacity'><span class='material-symbols-outlined text-[16px] text-slate-400 hover:text-slate-700'>more_horiz</span></td></tr>";
    }).join("");
  };

  const renderLibraryCards = (items, selectedId) => {
    const rows = Array.isArray(items) ? items : [];
    return rows.map((item) => {
      const status = String(item.status || "unknown");
      const tone = statusTone(status);
      const dotClass = tone === "good" ? "bg-accent-green" : tone === "warn" ? "bg-accent-amber" : tone === "bad" ? "bg-accent-red" : "bg-slate-400";
      const risk = tone === "bad" ? "Critical" : tone === "warn" ? "Medium" : "Low";
      const pid = escapeHtml(String(item.id || ""));
      const active = selectedId && String(item.id || "") === String(selectedId);
      return "<article class='group relative rounded-2xl bg-white/45 border p-4 shadow-sm hover:shadow-md transition-all cursor-pointer "
        + (active ? "border-primary/35 ring-1 ring-primary/25 bg-primary/5" : "border-white/70")
        + "' data-phen-id='"
        + pid + "'>"
        + "<div class='absolute top-3 right-3 size-2 rounded-full " + dotClass + " shadow-sm'></div>"
        + "<div class='font-mono text-[10px] text-slate-400 mb-2'>#" + pid + "</div>"
        + "<h4 class='text-sm font-bold text-slate-800 leading-tight mb-2 line-clamp-2'>" + escapeHtml(item.name || "-") + "</h4>"
        + "<p class='text-[11px] text-slate-500 mb-4'>Last seen: " + escapeHtml(item.lastSeen || "-") + "</p>"
        + "<div class='h-24 rounded-xl border border-white/60 bg-white/30 mb-4 flex items-center justify-center text-slate-300'>"
        + "<span class='material-symbols-outlined text-2xl'>token</span></div>"
        + "<div class='flex items-center justify-between text-[11px]'>"
        + "<span class='px-2 py-1 rounded-lg bg-white/70 border border-white/80 text-slate-700 font-semibold'>" + risk + "</span>"
        + "<span class='text-slate-500'>posts: <span class='font-mono text-slate-700'>" + escapeHtml(String(item.totalPosts || 0)) + "</span></span></div>"
        + "<div class='mt-3 pt-3 border-t border-white/60 text-[10px] text-slate-500 font-medium'>Click card to inspect post/cluster evidence</div>"
        + "</article>";
    }).join("");
  };

  const renderRelatedSignals = (items) => {
    const rows = Array.isArray(items) ? items : [];
    return rows.map((item) => {
      const score = Number(item.scorePct || 0);
      const scoreText = Math.max(0, Math.min(99, Math.round(score))) + "%";
      let scoreClass = "text-slate-500 bg-slate-100 border-slate-200";
      if (score >= 75) scoreClass = "text-accent-amber bg-amber-50 border-amber-100";
      else if (score >= 45) scoreClass = "text-accent-green bg-emerald-50 border-emerald-100";
      return "<div class='flex items-center justify-between p-3 bg-white/40 border border-white/50 hover:bg-white/70 rounded-xl transition-all cursor-pointer group shadow-sm'>"
        + "<div class='flex flex-col'>"
        + "<span class='text-xs text-slate-700 font-mono font-medium group-hover:text-primary transition-colors'>" + escapeHtml(String(item.id || "-")) + "</span>"
        + "<span class='text-[10px] text-slate-500'>Source: " + escapeHtml(String(item.source || "-")) + "</span>"
        + "</div>"
        + "<span class='text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border " + scoreClass + "'>" + scoreText + "</span>"
        + "</div>";
    }).join("");
  };

  const renderSourceHealthBadges = (items) => {
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) return "";
    return rows.map((row) => {
      const source = escapeHtml(String(row.source || "source"));
      const state = String(row.state || "ready").toLowerCase();
      const reason = escapeHtml(String(row.reason || ""));
      const trace = escapeHtml(String(row.traceId || ""));
      const tone =
        state === "ready"
          ? "bg-emerald-50 border-emerald-200 text-emerald-700"
          : state === "empty"
            ? "bg-slate-100 border-slate-200 text-slate-600"
            : state === "pending"
              ? "bg-amber-50 border-amber-200 text-amber-700"
              : "bg-rose-50 border-rose-200 text-rose-700";
      const suffix = reason ? (" · " + reason) : "";
      const traceSuffix = trace ? (" · " + trace.slice(0, 10)) : "";
      return "<span class='inline-flex items-center px-2 py-1 rounded-full text-[10px] border font-mono " + tone + "'>"
        + source + ":" + escapeHtml(state.toUpperCase()) + suffix + traceSuffix + "</span>";
    }).join("");
  };

  const setMetadataGridValue = (label, value) => {
    const labelEl = qsa("div").find((el) => normalize(el.textContent) === normalize(label));
    if (!labelEl) return;
    const valueEl = labelEl.nextElementSibling;
    if (valueEl) {
      valueEl.textContent = String(value || "-");
    }
  };

  const setFieldCardValue = (label, value) => {
    const labelKey = normalize(label);
    const labelEl = qsa("span,div").find((el) => normalize(el.textContent) === labelKey);
    if (!labelEl) return;
    const field = labelEl.parentElement;
    if (!field) return;
    const valueHost = labelEl.nextElementSibling;
    if (!valueHost) return;
    const text = String(value || "-");
    const directText = Array.from(valueHost.childNodes).find((node) => node.nodeType === Node.TEXT_NODE);
    if (directText) {
      directText.textContent = text;
      return;
    }
    const valueEl =
      valueHost.querySelector("span.text-base") ||
      valueHost.querySelector("span.text-sm") ||
      valueHost.querySelector("div.text-sm") ||
      valueHost.querySelector("div.text-xs") ||
      valueHost.querySelector("span.font-mono") ||
      valueHost.querySelector("div.font-mono");
    if (valueEl) {
      valueEl.textContent = text;
      return;
    }
    valueHost.textContent = text;
  };

  const updateLibrary = (payload) => {
    const data = payload || {};
    const debugMode = Boolean(data.debugMode);
    if (data.demoLabel) showDemoBadge(data.demoLabel);

    const items = Array.isArray(data.items) ? data.items : [];
    const countBadge = qsa("h2").find((el) => normalize(el.textContent) === "active phenomena")
      ?.parentElement?.querySelector("span");
    if (countBadge) countBadge.textContent = Number(data.total || items.length).toLocaleString();

    const updatedLabel = qsa("span").find((el) => normalize(el.textContent).startsWith("last updated:"));
    if (updatedLabel && data.updatedAtText) {
      updatedLabel.textContent = "Last updated: " + String(data.updatedAtText);
    }
    const sourceHealthRows = Array.isArray(data.sourceHealth) ? data.sourceHealth : [];
    const mainHeader = qs("main > div.h-14");
    if (mainHeader instanceof HTMLElement) {
      let healthHost = mainHeader.querySelector("[data-library-source-health='1']");
      if (!healthHost) {
        healthHost = document.createElement("div");
        healthHost.setAttribute("data-library-source-health", "1");
        healthHost.className = "ml-3 flex flex-wrap items-center gap-1.5";
        mainHeader.appendChild(healthHost);
      }
      if (debugMode && sourceHealthRows.length) {
        healthHost.innerHTML = renderSourceHealthBadges(sourceHealthRows);
        healthHost.style.display = "flex";
      } else {
        healthHost.innerHTML = "";
        healthHost.style.display = "none";
      }
    }

    const tbody = qs("table tbody");
    if (tbody) {
      tbody.innerHTML = items.length ? renderLibraryRows(items) : "<tr><td colspan='8' class='px-6 py-8 text-xs text-slate-500'>No phenomena available.</td></tr>";
    }

    const mainHeaderRight =
      (updatedLabel ? updatedLabel.parentElement : null) ||
      qsa("button")
        .find((el) => {
          const icon = el.querySelector("span.material-symbols-outlined");
          return normalize(icon?.textContent) === "refresh";
        })
        ?.parentElement ||
      (mainHeader ? mainHeader.lastElementChild : null) ||
      null;
    const switcherHost = mainHeaderRight || mainHeader || qs("main");
    if (switcherHost) {
      let switcher = switcherHost.querySelector("[data-library-view-switch='1']");
      if (!switcher) {
        switcher = document.createElement("div");
        switcher.setAttribute("data-library-view-switch", "1");
        switcher.className = "inline-flex items-center gap-1 p-1 rounded-xl bg-white/40 border border-white/60";
        switcher.innerHTML = ""
          + "<button data-stitch-action='library_view_table' class='px-2.5 py-1 text-[10px] font-bold rounded-lg text-slate-600 hover:bg-white/70 transition-colors'>Table</button>"
          + "<button data-stitch-action='library_view_cards' class='px-2.5 py-1 text-[10px] font-bold rounded-lg text-slate-600 hover:bg-white/70 transition-colors'>Cards</button>";
        const first = switcherHost.firstChild;
        if (first) switcherHost.insertBefore(switcher, first);
        else switcherHost.appendChild(switcher);
      }
      const mode = String(data.viewMode || "table").toLowerCase();
      const buttons = Array.from(switcher.querySelectorAll("button"));
      buttons.forEach((btn) => {
        const isTable = btn.getAttribute("data-stitch-action") === "library_view_table";
        const active = (mode === "table" && isTable) || (mode === "cards" && !isTable);
        btn.classList.toggle("bg-primary/15", active);
        btn.classList.toggle("text-primary", active);
      });
    }

    const scrollWrap = qs("main .flex-1.overflow-auto.custom-scrollbar");
    const table = qs("table", scrollWrap || undefined);
    if (scrollWrap && table) {
      let cards = scrollWrap.querySelector("[data-library-cards='1']");
      if (!cards) {
        cards = document.createElement("div");
        cards.setAttribute("data-library-cards", "1");
        cards.className = "p-4 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4";
        scrollWrap.appendChild(cards);
      }
      const mode = String(data.viewMode || "table").toLowerCase();
      if (mode === "cards") {
        cards.innerHTML = items.length
          ? renderLibraryCards(items.slice(0, 36), String(data.selected?.id || ""))
          : "<div class='text-xs text-slate-500 p-4'>No phenomena available.</div>";
        table.style.display = "none";
        cards.style.display = "grid";
      } else {
        table.style.display = "";
        cards.style.display = "none";
      }
    }

    const footer = qsa("div").find((el) => {
      if (!el.classList.contains("text-slate-500")) return false;
      const fs = (el.style && el.style.fontSize) || "";
      return normalize(el.textContent).startsWith("showing") || fs === "11px";
    });
    if (footer) {
      const total = Number(data.total || items.length || 0);
      const shown = Math.min(total, items.length || 0);
      footer.innerHTML = "Showing <span class='font-bold text-slate-700'>1-" + shown + "</span> of <span class='font-bold text-slate-700'>" + total.toLocaleString() + "</span>";
    }

    const selected = data.selected || null;
    if (selected) {
      document.body.dataset.selectedPhenomenonId = String(selected.id || "");
      const heading = qs("aside h3.text-lg");
      if (heading) heading.textContent = String(selected.name || selected.id || "-");
      const idChip = qsa("aside span.font-mono").find((el) => normalize(el.textContent).startsWith("id:"));
      if (idChip) idChip.textContent = "ID: " + String(selected.id || "-");
      setFieldCardValue("Category", selected.category || "Anomaly");
      setFieldCardValue("Confidence", selected.confidence || "--");
      setFieldCardValue("Status", selected.status || "unknown");
      setFieldCardValue("Impact Score", selected.impactScore || "--");

      const occHeading = qsa("h4").find((el) => normalize(el.textContent).startsWith("occurrence timeline"));
      if (occHeading) {
        const occWrap = occHeading.closest("div.space-y-4");
        const deltaBadge = occHeading.parentElement ? occHeading.parentElement.querySelector("span") : null;
        if (deltaBadge) deltaBadge.textContent = String(selected.occurrenceDelta || "-");
        const barsWrap = occWrap ? occWrap.querySelector("div.w-full.h-full.flex.items-end") : null;
        const bars = barsWrap ? Array.from(barsWrap.querySelectorAll("div")) : [];
        const values = Array.isArray(selected.occurrenceBars) ? selected.occurrenceBars.map((x) => Number(x || 0)) : [];
        if (bars.length) {
          if (values.length && values.some((x) => x > 0)) {
            const max = Math.max(...values, 1);
            bars.forEach((bar, idx) => {
              const raw = Number(values[idx % values.length] || 0);
              const pct = Math.max(8, Math.round((raw / max) * 95));
              bar.style.height = pct + "%";
              bar.style.opacity = raw > 0 ? "1" : "0.25";
            });
          } else {
            bars.forEach((bar) => {
              bar.style.height = "8%";
              bar.style.opacity = "0.22";
            });
          }
        }
      }

      const signalHeading = qsa("h4").find((el) => normalize(el.textContent) === "related signals");
      if (signalHeading) {
        const signalWrap = signalHeading.closest("div.space-y-3");
        const listWrap = signalWrap ? signalWrap.querySelector("div.space-y-2") : null;
        const signals = Array.isArray(selected.relatedSignals) ? selected.relatedSignals : [];
        if (listWrap) {
          listWrap.innerHTML = signals.length
            ? renderRelatedSignals(signals.slice(0, 6))
            : "<div class='text-[11px] text-slate-500 px-1 py-2'>No related signals from backend yet.</div>";
        }
      }

      const meta = selected.metadata || {};
      setMetadataGridValue("First Observed", meta.firstObserved || selected.firstObserved || "-");
      setMetadataGridValue("Ingestion Node", meta.ingestionNode || "-");
      setMetadataGridValue("Schema Version", meta.schemaVersion || "-");
      setMetadataGridValue("Encryption", meta.encryption || "-");

      const asidePanel =
        qs("aside .flex-1.overflow-y-auto") ||
        qs("aside .p-6.space-y-6.overflow-y-auto") ||
        qs("aside");
      if (asidePanel instanceof HTMLElement) {
        let drill = asidePanel.querySelector("[data-library-drill='1']");
        if (!drill) {
          drill = document.createElement("section");
          drill.setAttribute("data-library-drill", "1");
          drill.className = "space-y-3";
          const insertBefore = qsa("button").find((el) => normalize(el.textContent).includes("promote to axis"))?.closest("div");
          if (insertBefore && insertBefore.parentElement === asidePanel) {
            asidePanel.insertBefore(drill, insertBefore);
          } else {
            asidePanel.appendChild(drill);
          }
        }
        const drillRows = Array.isArray(selected.postDrilldown) ? selected.postDrilldown : [];
        drill.innerHTML = ""
          + "<div class='flex items-center justify-between'><h4 class='text-sm font-bold text-slate-700'>Post Clusters & Evidence</h4><span class='text-[10px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-600 border border-indigo-200'>"
          + drillRows.length + " posts</span></div>"
          + (drillRows.length
            ? ("<div class='space-y-2 max-h-64 overflow-auto'>" + drillRows.map((post) => {
                const postId = escapeHtml(String(post.postId || ""));
                const snippet = escapeHtml(String(post.snippet || ""));
                const clusters = Array.isArray(post.clusters) ? post.clusters : [];
                const evidence = Array.isArray(post.evidence) ? post.evidence : [];
                return "<article class='rounded-xl border border-white/70 bg-white/45 p-3 space-y-2'>"
                  + "<div class='text-[10px] font-mono text-slate-500'>post " + postId.slice(0, 8) + "</div>"
                  + "<p class='text-xs text-slate-700 line-clamp-2'>" + snippet + "</p>"
                  + "<div class='flex flex-wrap gap-1'>" + clusters.map((cl) =>
                      "<span class='text-[10px] px-2 py-0.5 rounded-full bg-white/70 border border-white/80 text-slate-600'>"
                      + escapeHtml(String(cl.label || "cluster")) + " · " + escapeHtml(String(cl.size || 0)) + "</span>"
                    ).join("") + "</div>"
                  + "<div class='space-y-1'>" + evidence.map((ev) => {
                      const evidenceId = escapeHtml(String(ev.id || ""));
                      const clusterKey = ev.clusterKey == null ? "" : escapeHtml(String(ev.clusterKey));
                      return "<button type='button' data-stitch-action='library_open_review' data-post-id='" + postId + "' data-evidence-id='" + evidenceId + "' data-cluster-key='" + clusterKey + "' class='w-full text-left text-[11px] text-slate-600 rounded-lg bg-white/65 border border-white/80 px-2 py-1.5 hover:bg-white transition-colors line-clamp-2'>"
                        + escapeHtml(String(ev.text || "Open evidence in review")) + "</button>";
                    }).join("") + "</div>"
                  + "</article>";
              }).join("") + "</div>")
            : "<div class='text-[11px] text-slate-500'>NO_DATA_YET · waiting for post-cluster wiring.</div>");
      }
    }

    const promoteBtn = qsa("button").find((el) => normalize(el.textContent).includes("promote to axis"));
    if (promoteBtn) {
      const canPromote = Boolean(selected && selected.id);
      promoteBtn.disabled = !canPromote;
      if (canPromote) {
        promoteBtn.classList.remove("cursor-not-allowed");
        promoteBtn.classList.add("cursor-pointer", "hover:bg-slate-50");
      }
    }
    reportHeight();
  };

  const setMetricByLabel = (label, value) => {
    const labelEl = qsa("span,div,p").find((el) => normalize(el.textContent) === normalize(label));
    if (!labelEl) return;
    const box = labelEl.closest("div");
    if (!box) return;
    const valEl = box.querySelector("span.text-base, span.text-lg, span.text-sm.font-mono, div.text-xl, div.text-3xl");
    if (valEl) valEl.textContent = String(value || "0");
  };

  const ensureInsightsGraphHost = () => {
    const headingList = Array.from(document.getElementsByTagName("h2"));
    const title = headingList.find((el) => normalize(el.textContent).includes("cluster explorer")) || headingList[0] || null;
    if (!title) {
      document.body.dataset.graphDebug = "no-title";
      return null;
    }
    const titleCard = title.closest("div");
    const panel = (titleCard && titleCard.parentElement) || title.closest("section");
    if (!(panel instanceof HTMLElement)) {
      document.body.dataset.graphDebug = "no-panel";
      return null;
    }
    let graphWrap =
      panel.querySelector("div.bg-gradient-to-br") ||
      Array.from(panel.querySelectorAll("div")).find((el) => {
        const classText = String(el.className || "");
        return classText.includes("flex-1") && classText.includes("overflow-hidden");
      });
    if (!(graphWrap instanceof HTMLElement)) {
      const bubble = Array.from(panel.querySelectorAll("div")).find((el) => normalize(el.textContent).startsWith("c-"));
      if (bubble instanceof HTMLElement) {
        const bubbleLayer = bubble.closest("div.absolute.inset-0");
        graphWrap = (bubbleLayer && bubbleLayer.parentElement) || bubble.closest("div");
      }
    }
    if (!(graphWrap instanceof HTMLElement)) {
      document.body.dataset.graphDebug = "no-graph-wrap";
      return null;
    }
    if (getComputedStyle(graphWrap).position === "static") {
      graphWrap.style.position = "relative";
    }

    let host = graphWrap.querySelector("[data-insights-graph='1']");
    if (!(host instanceof HTMLElement)) {
      graphWrap.innerHTML = "";

      const grid = document.createElement("div");
      grid.className = "absolute inset-0 opacity-[0.03]";
      grid.style.backgroundImage = "linear-gradient(#334155 1px, transparent 1px), linear-gradient(90deg, #334155 1px, transparent 1px)";
      grid.style.backgroundSize = "40px 40px";
      graphWrap.appendChild(grid);

      const linksSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      linksSvg.setAttribute("class", "absolute inset-0 w-full h-full pointer-events-none");
      linksSvg.setAttribute("data-insights-graph-links", "1");
      linksSvg.style.zIndex = "30";
      graphWrap.appendChild(linksSvg);

      host = document.createElement("div");
      host.setAttribute("data-insights-graph", "1");
      host.className = "absolute inset-0";
      host.style.zIndex = "31";
      graphWrap.appendChild(host);
    }
    document.body.dataset.graphDebug = "ok";
    return host;
  };

  const renderInsightsGraph = (payload) => {
    const host = ensureInsightsGraphHost();
    if (!host) return;
    const graphWrap = host.parentElement;
    if (!graphWrap) return;
    const linksSvg = graphWrap.querySelector("[data-insights-graph-links='1']");
    if (!(linksSvg instanceof SVGElement)) return;

    const nodes = Array.isArray(payload.graphNodes)
      ? payload.graphNodes.filter((row) => row && Number.isFinite(Number(row.x)) && Number.isFinite(Number(row.y)))
      : [];
    const links = Array.isArray(payload.graphLinks) ? payload.graphLinks : [];
    const selectedKey = Number(payload.selectedClusterKey);
    const selected = Number.isFinite(selectedKey) ? selectedKey : null;

    host.innerHTML = "";
    linksSvg.innerHTML = "";

    if (!nodes.length) {
      const empty = document.createElement("div");
      empty.className = "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-[11px] font-medium text-slate-500 bg-white/65 border border-white/80 rounded-lg px-3 py-2 shadow-sm";
      empty.textContent = "NO_RELATION_EDGES_YET";
      host.appendChild(empty);
      return;
    }

    if (!links.length) {
      const empty = document.createElement("div");
      empty.className = "absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 text-[11px] font-medium text-slate-600 bg-white/70 border border-white/80 rounded-lg px-3 py-2 shadow-sm";
      empty.textContent = "NO_RELATION_EDGES_YET";
      host.appendChild(empty);
    }

    const maxSize = Math.max(
      1,
      ...nodes.map((row) => {
        const size = Number(row.size || 0);
        return Number.isFinite(size) ? size : 0;
      })
    );

    const radiusByNodeId = new Map();
    nodes.forEach((row) => {
      const rawSize = Number(row.size || 0);
      const scale = Math.sqrt(Math.max(0, rawSize) / Math.max(1, maxSize));
      const radius = Math.max(8, Math.min(28, 8 + scale * 20));
      radiusByNodeId.set(String(row.id || ""), radius);
    });

    const nodeById = new Map();
    nodes.forEach((row) => {
      const id = String(row.id || "");
      if (id) nodeById.set(id, row);
    });

    const box = graphWrap.getBoundingClientRect();
    const width = Math.max(1, box.width || graphWrap.clientWidth || 1);
    const height = Math.max(1, box.height || graphWrap.clientHeight || 1);
    const padX = Math.max(20, Math.round(width * 0.055));
    const padY = Math.max(20, Math.round(height * 0.07));
    const innerW = Math.max(1, width - padX * 2);
    const innerH = Math.max(1, height - padY * 2);
    linksSvg.setAttribute("viewBox", "0 0 " + width + " " + height);
    linksSvg.setAttribute("preserveAspectRatio", "none");
    linksSvg.style.overflow = "visible";

    const ns = "http://www.w3.org/2000/svg";
    const nodePointById = new Map();
    nodes.forEach((row) => {
      const id = String(row.id || "");
      const x = padX + Math.max(0, Math.min(1, Number(row.x))) * innerW;
      const y = padY + Math.max(0, Math.min(1, Number(row.y))) * innerH;
      nodePointById.set(id, { x, y });
    });
    links.forEach((row) => {
      const source = nodeById.get(String(row.source || ""));
      const target = nodeById.get(String(row.target || ""));
      if (!source || !target) return;
      const sourcePoint = nodePointById.get(String(source.id || ""));
      const targetPoint = nodePointById.get(String(target.id || ""));
      if (!sourcePoint || !targetPoint) return;
      const sx = sourcePoint.x;
      const sy = sourcePoint.y;
      const tx = targetPoint.x;
      const ty = targetPoint.y;
      const dx = tx - sx;
      const dy = ty - sy;
      const dist = Math.max(0.001, Math.hypot(dx, dy));
      const sr = Number(radiusByNodeId.get(String(source.id || "")) || 10) / 2 + 2;
      const tr = Number(radiusByNodeId.get(String(target.id || "")) || 10) / 2 + 2;
      if (dist <= sr + tr + 2) return;
      const x1 = sx + (dx / dist) * sr;
      const y1 = sy + (dy / dist) * sr;
      const x2 = tx - (dx / dist) * tr;
      const y2 = ty - (dy / dist) * tr;

      const midX = (x1 + x2) / 2;
      const midY = (y1 + y2) / 2;
      const perpX = dist === 0 ? 0 : -dy / dist;
      const perpY = dist === 0 ? 0 : dx / dist;
      const curve = Math.min(18, dist * 0.12);
      const cx = midX + perpX * curve;
      const cy = midY + perpY * curve;

      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", "M " + x1.toFixed(2) + " " + y1.toFixed(2) + " Q " + cx.toFixed(2) + " " + cy.toFixed(2) + " " + x2.toFixed(2) + " " + y2.toFixed(2));
      path.setAttribute("stroke", "rgba(99, 102, 241, 0.46)");
      const weight = Number(row.weight || 0);
      path.setAttribute("stroke-width", String(Math.max(1.2, Math.min(3.8, 1.1 + Math.sqrt(Math.max(1, weight)) * 0.62))));
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("fill", "none");
      path.setAttribute("opacity", "0.9");
      linksSvg.appendChild(path);
    });

    nodes.forEach((row) => {
      const key = Number(row.cluster_key);
      const isSelected = selected != null ? key === selected : Boolean(row.id && row.id === nodes[0].id);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "absolute -translate-x-1/2 -translate-y-1/2 flex flex-col items-center group/node";
      const point = nodePointById.get(String(row.id || ""));
      const px = point ? point.x : width * Number(row.x);
      const py = point ? point.y : height * Number(row.y);
      btn.style.left = ((px / width) * 100).toFixed(2) + "%";
      btn.style.top = ((py / height) * 100).toFixed(2) + "%";
      btn.style.pointerEvents = "auto";
      btn.dataset.clusterKey = String(key);

      const dot = document.createElement("div");
      const radius = Number(radiusByNodeId.get(String(row.id || "")) || 10);
      dot.style.width = radius + "px";
      dot.style.height = radius + "px";
      if (isSelected) {
        dot.className = "rounded-full bg-primary border-4 border-white/80 ring-4 ring-primary/20 shadow-[0_0_25px_rgba(139,92,246,0.6)] transition-all";
      } else {
        dot.className = "rounded-full bg-white border-2 border-slate-300 shadow-sm transition-colors group-hover/node:bg-primary group-hover/node:border-primary";
      }
      btn.appendChild(dot);

      const label = document.createElement("span");
      label.className = isSelected
        ? "mt-2 text-[9px] font-bold text-slate-700 bg-white/75 px-1.5 py-0.5 rounded-md backdrop-blur-sm shadow-sm"
        : "mt-2 text-[9px] font-bold text-slate-500 bg-white/50 px-1.5 py-0.5 rounded-md backdrop-blur-sm opacity-0 group-hover/node:opacity-100 transition-opacity";
      label.textContent = String(row.label || ("C-" + String(key).padStart(3, "0")));
      btn.appendChild(label);

      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        pulse(btn);
        post({
          type: "stitch:action",
          action: "insights_select_cluster",
          meta: Object.assign(collectMeta(), { clusterKey: key }),
        });
      });
      host.appendChild(btn);
    });
  };

  const forceInsightsCounter = (label, value) => {
    const targetLabel = normalize(label);
    qsa("span")
      .filter((el) => normalize(el.textContent) === targetLabel)
      .forEach((labelEl) => {
        const card = labelEl.closest("div");
        if (!card) return;
        const valueSpan = Array.from(card.querySelectorAll("span")).find(
          (el) => el !== labelEl && /\d/.test(String(el.textContent || ""))
        );
        if (valueSpan) {
          valueSpan.textContent = String(value);
          return;
        }
        const fallback = labelEl.nextElementSibling;
        if (fallback) fallback.textContent = String(value);
      });
  };

  const setInsightsModeDots = (mode) => {
    const heading = qsa("h2").find((el) => {
      const key = normalize(el.textContent);
      return key === "narrative stack" || key === "cluster detail";
    });
    if (!heading || !heading.parentElement) return;
    const dots = Array.from(heading.parentElement.querySelectorAll("div.flex.gap-2 > span"));
    dots.forEach((dot, idx) => {
      const active = (mode === "narrative" && idx === 0) || (mode === "evidence" && idx === 1);
      dot.classList.remove("bg-primary", "shadow-glow", "bg-slate-300");
      dot.classList.add(active ? "bg-primary" : "bg-slate-300");
      if (active) dot.classList.add("shadow-glow");
    });
  };

  const renderOverviewEvents = (rows) => {
    const list = Array.isArray(rows) ? rows : [];
    return list.map((row) => {
      const state = String(row.state || "neutral");
      const dot = state === "good" ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]"
        : state === "warn" ? "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.5)]"
          : state === "bad" ? "bg-rose-400 shadow-[0_0_8px_rgba(251,113,133,0.5)]"
            : "bg-slate-400";
      return "<tr class='hover:bg-white/40 transition-colors group cursor-pointer'>"
        + "<td class='px-6 py-3.5 text-center'><div class='size-2.5 mx-auto rounded-full " + dot + " border border-white'></div></td>"
        + "<td class='px-6 py-3.5 font-mono font-medium text-slate-600 group-hover:text-primary transition-colors'>" + escapeHtml(row.user || "-") + "</td>"
        + "<td class='px-6 py-3.5 font-medium'>" + escapeHtml(row.message || "-") + "</td>"
        + "<td class='px-6 py-3.5 text-right font-mono text-slate-500'>" + escapeHtml(row.time || "--:--:--") + "</td></tr>";
    }).join("");
  };

  const updateOverview = (payload) => {
    const data = payload || {};
    const kpiRows = qsa("span").filter((el) => {
      const cls = String(el.className || "");
      return cls.includes("uppercase");
    });
    kpiRows.forEach((kpi) => {
      const key = normalize(kpi.textContent);
      const valueEl = kpi.parentElement ? kpi.parentElement.querySelector("span.text-base") : null;
      if (!valueEl) return;
      if (key === "active") valueEl.textContent = String(Number(data.activeCount || 0));
      if (key === "queued") valueEl.textContent = String(Number(data.queuedCount || 0));
      if (key === "failed") valueEl.textContent = String(Number(data.failedCount || 0));
      if (key === "degraded") valueEl.textContent = Number(data.degradedCount || 0) > 0 ? "1" : "0";
    });

    const runLabel = qsa("span.text-slate-600.font-medium.font-mono").find((el) => normalize(el.textContent).startsWith("current run"));
    if (runLabel) {
      runLabel.innerHTML = "Current Run: <span class='text-slate-800 font-bold'>Batch #" + escapeHtml(fmtShortId(data.currentRunId)) + "</span>";
      markAsAction(runLabel, "overview_open_active_run");
      runLabel.setAttribute("data-job-id", String(data.currentRunId || ""));
    }

    const statusBadge = qsa("span").find((el) => normalize(el.textContent).startsWith("status:"));
    if (statusBadge) {
      statusBadge.textContent = "Status: " + String(data.currentStatus || "Idle");
      markAsAction(statusBadge, "overview_open_active_run");
      statusBadge.setAttribute("data-job-id", String(data.currentRunId || ""));
    }

    const progress = Math.max(0, Math.min(100, Number(data.progressPct || 0)));
    const progressBar = qs("div.h-2.w-full > div.h-full");
    if (progressBar instanceof HTMLElement) {
      progressBar.style.width = progress.toFixed(0) + "%";
    }

    const timelineHeading = qsa("h3").find((el) => normalize(el.textContent) === "timeline drift");
    if (timelineHeading) {
      let flag = timelineHeading.parentElement?.querySelector("[data-timeline-pending='1']");
      if (!flag) {
        flag = document.createElement("span");
        flag.setAttribute("data-timeline-pending", "1");
        flag.className = "mt-2 inline-flex items-center text-[10px] font-bold uppercase tracking-wide text-amber-700 bg-amber-100/80 border border-amber-200 rounded-full px-2 py-0.5";
        timelineHeading.parentElement?.appendChild(flag);
      }
      flag.textContent = String(data.timelineState === "live" ? "LIVE DATA" : "MOCK · PENDING WIRING");
    }

    const timelineBars = qsa("section .absolute.inset-0.flex.items-end.justify-between > div");
    const timeline = Array.isArray(data.timeline) ? data.timeline : [];
    if (timelineBars.length) {
      if (timeline.length) {
        const max = Math.max(...timeline.map((x) => Number(x || 0)), 1);
        timelineBars.forEach((bar, idx) => {
          const raw = Number(timeline[idx % timeline.length] || 0);
          const pct = Math.round((raw / max) * 78 + 12);
          bar.style.height = pct + "%";
          bar.style.opacity = "1";
        });
      } else {
        timelineBars.forEach((bar) => {
          bar.style.height = "8%";
          bar.style.opacity = "0.22";
        });
        const driftValue = qsa("div.text-3xl.font-bold").find((el) => /%/.test(String(el.textContent || "")));
        if (driftValue) driftValue.textContent = "--";
        qsa("div").forEach((el) => {
          if (normalize(el.textContent).startsWith("anomaly detected")) {
            el.remove();
          }
        });
      }
    }
    const shiftLabel = qsa("div,span").find((el) => {
      const raw = String(el.textContent || "");
      const cls = String(el.className || "");
      return raw.includes("Last Shift:") && cls.includes("font-mono") && el.children.length === 0;
    });
    if (shiftLabel && data.timelineState !== "live") shiftLabel.textContent = "Last Shift: -";

    const tbody = qs("table tbody");
    if (tbody) {
      const events = Array.isArray(data.events) ? data.events : [];
      tbody.innerHTML = events.length
        ? renderOverviewEvents(events.slice(0, 24))
        : "<tr><td colspan='4' class='px-6 py-6 text-xs text-slate-500'>SAMPLE / TEST DATA (UI ONLY) · Comment momentum pending wiring.</td></tr>";
    }

    const momentumHeading = qsa("h3").find((el) => normalize(el.textContent).includes("comment momentum"));
    if (momentumHeading) {
      let tag = momentumHeading.parentElement?.querySelector("[data-momentum-pending='1']");
      if (!tag) {
        tag = document.createElement("span");
        tag.setAttribute("data-momentum-pending", "1");
        tag.className = "ml-2 inline-flex items-center text-[10px] font-bold uppercase tracking-wide text-amber-700 bg-amber-100/80 border border-amber-200 rounded-full px-2 py-0.5";
        momentumHeading.appendChild(tag);
      }
      tag.textContent = String(data.momentumState === "live" ? "LIVE DATA" : "MOCK · PENDING WIRING");
    }

    if (data.context) {
      const ctx = data.context;
      const panelTitle = qsa("h3").find((el) => normalize(el.textContent) === "current context");
      if (panelTitle) {
        const subtitle = panelTitle.parentElement?.querySelector("p");
        if (subtitle) subtitle.textContent = "MOCK · PENDING WIRING";
      }
      setFieldCardValue("Phenomenon ID", ctx.phenomenonId || "PH-XXXX");
      setFieldCardValue("Stability State", ctx.stability || "TBD");
      setFieldCardValue("ISO Timestamp", ctx.isoTimestamp || "TBD");
      setFieldCardValue("Latency", ctx.latency || "TBD");
      setFieldCardValue("Load", ctx.load || "TBD");
      setFieldCardValue("Uptime", ctx.uptime || "TBD");
      setFieldCardValue("Threads", ctx.threads || "TBD");
      const contextPanel = panelTitle ? panelTitle.closest("aside") : null;
      if (contextPanel) {
        const stabilityLabel = qsa("span,div", contextPanel).find((el) => normalize(el.textContent) === "stability state");
        const stabilityValue = stabilityLabel ? stabilityLabel.nextElementSibling : null;
        if (stabilityValue) {
          const icon = stabilityValue.querySelector(".material-symbols-outlined");
          if (icon) icon.remove();
          stabilityValue.textContent = "TBD";
        }
        const topoFooter = qsa("div", contextPanel).find((el) => {
          const cls = String(el.className || "");
          return cls.includes("flex items-center justify-between mt-2 text-xs font-mono");
        });
        if (topoFooter instanceof HTMLElement) {
          const left = topoFooter.children[0];
          const right = topoFooter.children[1];
          if (left) left.textContent = "Cluster: TBD";
          if (right) right.textContent = "TBD";
        }
        const topoClusterValue = qsa("div,span", contextPanel).find((el) => {
          const raw = String(el.textContent || "").trim();
          return raw.toLowerCase().startsWith("cluster:") && el.children.length === 0;
        });
        if (topoClusterValue) topoClusterValue.textContent = "Cluster: TBD";
        const topoLiveValue = qsa("div,span", contextPanel).find((el) => {
          return normalize(el.textContent) === "live" && el.children.length === 0;
        });
        if (topoLiveValue) topoLiveValue.textContent = "TBD";
      }
    }
    reportHeight();
  };

  const updateInsights = (payload) => {
    const data = payload || {};
    const debugMode = Boolean(data.debugMode);
    const mode = String(data.stackMode || "narrative") === "evidence" ? "evidence" : "narrative";
    const postItems = Array.isArray(data.posts) ? data.posts : [];
    document.body.dataset.insightsUpdatedAt = String(Date.now());
    document.body.dataset.insightsNodesWanted = String(data.nodes ?? "");
    document.body.dataset.insightsEdgesWanted = String(data.edges ?? "");
    document.body.dataset.insightsMode = mode;
    renderInsightsGraph(data);
    setInsightsModeDots(mode);
    const searchInput = qs("input[placeholder*='Search Node ID']");
    if (searchInput) {
      searchInput.setAttribute("placeholder", "Search Threads URL / Post ID");
      searchInput.classList.add("w-80");
      if (typeof data.postId === "string" && data.postId && document.activeElement !== searchInput) {
        searchInput.value = data.postId;
      }
    }
    const breadcrumbWrap = qsa("div.flex.items-center.gap-2.text-xs.font-medium.glass-panel")[0];
    if (breadcrumbWrap) breadcrumbWrap.remove();

    if (searchInput && searchInput.parentElement) {
      const searchWrap = searchInput.parentElement;
      searchWrap.classList.add("min-w-[22rem]");
      let panel = searchWrap.querySelector("[data-insights-post-list='1']");
      if (!panel) {
        panel = document.createElement("div");
        panel.setAttribute("data-insights-post-list", "1");
        panel.className = "absolute left-0 right-0 top-[calc(100%+8px)] bg-white/95 border border-white/70 rounded-xl shadow-xl backdrop-blur-md p-2 z-[80] hidden max-h-72 overflow-auto";
        searchWrap.appendChild(panel);
      }
      const current = String(data.postId || "");
      const query = String(searchInput.value || "").trim().toLowerCase();
      const filtered = postItems.filter((row) => {
        if (!query) return true;
        const hay = [row.id, row.snippet, row.url, row.author].map((x) => String(x || "").toLowerCase()).join(" ");
        return hay.includes(query);
      });
      panel.innerHTML = ""
        + "<div class='px-2 pt-1 pb-2 text-[10px] uppercase tracking-widest text-slate-500 font-bold'>Recent Posts</div>"
        + (filtered.length
            ? filtered.slice(0, 14).map((row) => {
                const pid = String(row.id || "");
                const active = pid === current;
                return "<button type='button' data-stitch-action='insights_select_post' data-post-id='" + escapeHtml(pid) + "' class='w-full text-left rounded-lg px-2.5 py-2 border mb-1 transition-all "
                  + (active ? "bg-primary/10 border-primary/25" : "bg-white/75 border-white/60 hover:bg-white")
                  + "'>"
                  + "<div class='flex items-center justify-between gap-2'><span class='text-[11px] font-mono font-bold "
                  + (active ? "text-primary" : "text-slate-700") + "'>" + escapeHtml(pid.slice(0, 10)) + "</span>"
                  + "<span class='text-[10px] text-slate-400'>" + escapeHtml(String(row.createdAt || "").slice(0, 16).replace("T", " ")) + "</span></div>"
                  + "<div class='text-[11px] text-slate-600 line-clamp-2 mt-1'>" + escapeHtml(String(row.snippet || "")) + "</div>"
                  + "</button>";
              }).join("")
            : "<div class='px-2 py-2 text-[11px] text-slate-500'>No posts matched.</div>");
      if (!searchInput.dataset.postPickerBound) {
        searchInput.dataset.postPickerBound = "1";
        searchInput.addEventListener("focus", () => panel.classList.remove("hidden"));
        searchInput.addEventListener("input", () => panel.classList.remove("hidden"));
        searchInput.addEventListener("blur", () => {
          window.setTimeout(() => panel.classList.add("hidden"), 150);
        });
      }
    }

    const header = qs("header");
    const topToolbar = qsa("body > div")[0];
    const main = qs("main");
    if (header && main) {
      const leftColumn = qsa("main > div").find((el) => String(el.className || "").includes("flex-[1.4]"));
      if (leftColumn instanceof HTMLElement) {
        const sections = Array.from(leftColumn.children).filter((el) => el instanceof HTMLElement);
        if (sections[0]) sections[0].style.height = "54%";
      }
      let postCard = qs("[data-insights-post-card='1']");
      if (!postCard) {
        postCard = document.createElement("section");
        postCard.setAttribute("data-insights-post-card", "1");
        postCard.className = "mx-6 mb-4 glass-panel rounded-2xl p-4";
        if (topToolbar && topToolbar.parentElement) {
          topToolbar.parentElement.insertBefore(postCard, main);
        } else {
          header.parentElement?.insertBefore(postCard, main);
        }
      }
      const pc = data.postCard || {};
      const metric = (value) => (value == null || value === "" ? "—" : String(value));
      const sourceHealthRows = Array.isArray(data.sourceHealth) ? data.sourceHealth : [];
      postCard.innerHTML = ""
        + "<div class='flex items-start justify-between gap-4'>"
        + "<div class='min-w-0 flex-1'>"
        + "<div class='flex items-center gap-2 mb-1'><span class='text-[10px] uppercase tracking-widest text-slate-500 font-bold'>Threads Anchor</span>"
        + "<span class='text-[10px] font-mono px-2 py-0.5 rounded-full bg-white/60 border border-white/70 text-slate-600'>post "
        + escapeHtml(String(pc.postId || "").slice(0, 10) || "-") + "</span></div>"
        + "<div class='text-sm font-bold text-slate-800 mb-1'>" + escapeHtml(String(pc.author || "Unknown Author")) + "</div>"
        + "<p class='text-[12px] text-slate-700 leading-relaxed line-clamp-3'>" + escapeHtml(String(pc.text || "No post content available.")) + "</p>"
        + "<div class='mt-2 text-[10px] text-slate-500 font-mono'>" + escapeHtml(String(pc.createdAt || "-").replace("T", " ").slice(0, 19)) + "</div>"
        + "</div>"
        + "<div class='w-36 h-24 rounded-xl border border-white/70 bg-white/35 flex items-center justify-center text-slate-400 text-[10px] font-bold uppercase tracking-wide'>"
        + escapeHtml(String(pc.mediaHint || "Media"))
        + "</div>"
        + "</div>"
        + "<div class='mt-3 pt-3 border-t border-white/50 flex items-center justify-between gap-3 flex-wrap'>"
        + "<div class='flex items-center gap-2 text-[11px] text-slate-600 font-medium'>"
        + "<span class='px-2 py-1 rounded-lg bg-white/60 border border-white/70'>♥ " + escapeHtml(metric(pc.likes)) + "</span>"
        + "<span class='px-2 py-1 rounded-lg bg-white/60 border border-white/70'>↩ " + escapeHtml(metric(pc.replies)) + "</span>"
        + "<span class='px-2 py-1 rounded-lg bg-white/60 border border-white/70'>⟲ " + escapeHtml(metric(pc.reposts)) + "</span>"
        + "<span class='px-2 py-1 rounded-lg bg-white/60 border border-white/70'>⇪ " + escapeHtml(metric(pc.shares)) + "</span>"
        + "</div>"
        + "<div class='flex items-center gap-2'>"
        + "<button type='button' data-stitch-action='insights_open_threads' data-post-id='" + escapeHtml(String(pc.postId || "")) + "' data-threads-url='" + escapeHtml(String(pc.threadsUrl || "")) + "' class='px-3 py-1.5 rounded-lg text-[10px] uppercase font-bold bg-white/60 border border-white/70 text-slate-600 hover:text-primary hover:bg-white transition-colors'>Open in Threads</button>"
        + "<button type='button' data-stitch-action='insights_copy_post_id' data-post-id='" + escapeHtml(String(pc.postId || "")) + "' class='px-3 py-1.5 rounded-lg text-[10px] uppercase font-bold bg-white/60 border border-white/70 text-slate-600 hover:text-primary hover:bg-white transition-colors'>Copy post_id</button>"
        + "<button type='button' data-stitch-action='insights_go_review' data-post-id='" + escapeHtml(String(pc.postId || "")) + "' class='px-3 py-1.5 rounded-lg text-[10px] uppercase font-bold bg-primary/15 border border-primary/30 text-primary hover:bg-primary/20 transition-colors'>Go Review</button>"
        + "</div>"
        + "</div>"
        + "<div class='mt-3 pt-3 border-t border-white/50 flex items-center gap-2'>"
        + "<label class='text-[10px] uppercase tracking-wide text-slate-500 font-bold'>Post</label>"
        + "<select data-stitch-action-change='insights_select_post' class='min-w-[220px] max-w-[420px] px-2.5 py-1.5 rounded-lg border border-white/70 bg-white/70 text-[11px] font-mono text-slate-700'>"
        + (postItems.length
            ? postItems.map((row) => {
                const pid = escapeHtml(String(row.id || ""));
                const active = String(row.id || "") === String(data.postId || "");
                const label = escapeHtml(String(row.id || "").slice(0, 10));
                const snippet = escapeHtml(String(row.snippet || "")).slice(0, 56);
                return "<option value='" + pid + "'" + (active ? " selected" : "") + ">post " + label + " · " + snippet + "</option>";
              }).join("")
            : "<option value=''>No posts available</option>")
        + "</select>"
        + "</div>"
        + (debugMode && sourceHealthRows.length
            ? ("<div class='mt-2 pt-2 border-t border-white/40 flex flex-wrap gap-1.5'>" + renderSourceHealthBadges(sourceHealthRows) + "</div>")
            : "");
    }
    if (data.nodeId) {
      const badge = qsa("span").find((el) => normalize(el.textContent).startsWith("node c-"));
      if (badge) badge.textContent = String(data.nodeId);
      const explorer = qsa("span.text-lg.font-bold").find((el) => normalize(el.textContent).startsWith("node c-"));
      if (explorer) explorer.textContent = String(data.nodeId);
      const centerBubble = qsa("div").find((el) => {
        const cls = String(el.className || "");
        return cls.includes("font-mono") && cls.includes("font-bold") && normalize(el.textContent).startsWith("c-");
      });
      if (centerBubble) centerBubble.innerHTML = escapeHtml(String(data.nodeId)) + " <span class='text-primary ml-1'>" + escapeHtml(String(data.centerShare || "0%")) + "</span>";
    }

    const nodesValue = Number.isFinite(Number(data.nodes)) ? Number(data.nodes) : 0;
    const edgesValue = Number.isFinite(Number(data.edges)) ? Number(data.edges) : 0;
    forceInsightsCounter("nodes", nodesValue);
    forceInsightsCounter("edges", edgesValue);
    requestAnimationFrame(() => {
      forceInsightsCounter("nodes", nodesValue);
      forceInsightsCounter("edges", edgesValue);
      document.body.dataset.insightsNodesApplied = String(nodesValue);
      document.body.dataset.insightsEdgesApplied = String(edgesValue);
    });
    setTimeout(() => {
      forceInsightsCounter("nodes", nodesValue);
      forceInsightsCounter("edges", edgesValue);
    }, 120);

    const stackPrimary = Array.isArray(data.stack) ? data.stack : [];
    const stackAlt = Array.isArray(data.stackAlt) ? data.stackAlt : [];
    const stack = stackPrimary.length ? stackPrimary : stackAlt;
    const selectedSummary = data.selectedClusterSummary || null;
    const selectedKey = Number(data.selectedClusterKey);
    const activeClusterKey = Number.isFinite(selectedKey)
      ? selectedKey
      : Number(selectedSummary?.clusterKey ?? stack[0]?.clusterKey ?? NaN);
    const activeRow =
      stack.find((row) => Number(row.clusterKey) === activeClusterKey) ||
      stack[0] ||
      null;
    const keyAttr = Number.isFinite(activeClusterKey) ? String(activeClusterKey) : "";
    const evidenceRows = Array.isArray(data.evidencePreview) ? data.evidencePreview : [];

    const rightCol = qsa("main > div").find((el) => String(el.className || "").includes("lg:w-[40%]"));
    const stackHeading = qsa("h2").find((el) => {
      const key = normalize(el.textContent);
      return key === "narrative stack" || key === "cluster detail";
    });
    if (stackHeading) stackHeading.textContent = "Cluster Detail";

    const stackPanel = (stackHeading ? stackHeading.closest("div.glass-panel") : null)
      || (rightCol instanceof HTMLElement ? rightCol.querySelector("div.glass-panel") : null);
    if (stackPanel instanceof HTMLElement) {
      if (!stackHeading) {
        const heading = stackPanel.querySelector("h2");
        if (heading) heading.textContent = "Cluster Detail";
      }
      Array.from(stackPanel.querySelectorAll("div.glass-card")).forEach((card) => {
        if (!(card instanceof HTMLElement)) return;
        if (card.matches("[data-insights-cluster-detail='1']")) return;
        card.remove();
      });
      let detailCard = stackPanel.querySelector("[data-insights-cluster-detail='1']");
      if (!(detailCard instanceof HTMLElement)) {
        detailCard = document.createElement("div");
        detailCard.setAttribute("data-insights-cluster-detail", "1");
        detailCard.className = "glass-card relative flex flex-col gap-4 p-4 mb-3";
        stackPanel.appendChild(detailCard);
      }
      detailCard.style.display = "";
      const keyLabel = Number.isFinite(activeClusterKey)
        ? ("C-" + String(activeClusterKey).padStart(3, "0"))
        : "Cluster";
      const compareLeftTitle = String(data.comparePanel?.leftTitle || "").trim();
      const clusterTitle = String(data.selectedClusterLabel || selectedSummary?.title || compareLeftTitle || "")
        .trim() || keyLabel;
      const summaryFallback = evidenceRows.length ? String(evidenceRows[0]?.text || "") : "";
      const summaryText = String(selectedSummary?.summary || activeRow?.brief || activeRow?.subtitle || summaryFallback || "No cluster summary yet.");
      const riskText = String(selectedSummary?.risk || activeRow?.status || "Unknown");
      const penetrationText = String(activeRow?.penetration || (String(selectedSummary?.commentsCount || 0) + " comments"));
      const clusterSizeText = String(activeRow?.clusterSize || (String(selectedSummary?.claimsCount || 0) + " claims"));
      const barWidth = activeRow?.penetrationPct != null ? fmtPct(activeRow.penetrationPct) : "12%";
      const engagement = String(activeRow?.engagement || "0");
      const commentsCount = String(selectedSummary?.commentsCount ?? data.comparePanel?.leftCount ?? activeRow?.commentsCount ?? 0);
      const claimsCount = String(selectedSummary?.claimsCount ?? activeRow?.claimsCount ?? 0);

      const detailsHtml = mode === "evidence"
        ? (evidenceRows.length
            ? ("<div class='space-y-2 max-h-44 overflow-auto' data-cluster-details='1'>" + evidenceRows.slice(0, 10).map((ev) =>
                "<button type='button' data-stitch-action='insights_open_comment_review' data-comment-id='" + escapeHtml(String(ev.id || "")) + "' data-post-id='" + escapeHtml(String(ev.postId || data.postId || "")) + "' data-cluster-key='" + escapeHtml(keyAttr) + "' class='w-full text-left rounded-lg border border-white/70 bg-white/65 px-2.5 py-2 hover:bg-white transition-colors'>"
                + "<div class='text-[10px] text-slate-500 mb-1'>" + escapeHtml(String(ev.author || "-")) + " · ♥" + escapeHtml(String(ev.likes || 0)) + "</div>"
                + "<div class='text-[11px] text-slate-700 line-clamp-2'>" + escapeHtml(String(ev.text || "")) + "</div>"
                + "</button>"
              ).join("") + "</div>")
            : "<div class='text-[11px] text-slate-500' data-cluster-details='1'>No evidence comments for this cluster.</div>")
        : ("<div class='grid grid-cols-3 gap-2 text-[10px]' data-cluster-details='1'>"
          + "<div class='rounded-lg border border-white/65 bg-white/55 px-2 py-1.5'><div class='text-slate-400 uppercase tracking-wide text-[9px]'>Engagement</div><div class='text-slate-700 font-mono font-bold'>" + escapeHtml(engagement) + "</div></div>"
          + "<div class='rounded-lg border border-white/65 bg-white/55 px-2 py-1.5'><div class='text-slate-400 uppercase tracking-wide text-[9px]'>Comments</div><div class='text-slate-700 font-mono font-bold'>" + escapeHtml(commentsCount) + "</div></div>"
          + "<div class='rounded-lg border border-white/65 bg-white/55 px-2 py-1.5'><div class='text-slate-400 uppercase tracking-wide text-[9px]'>Claims</div><div class='text-slate-700 font-mono font-bold'>" + escapeHtml(claimsCount) + "</div></div>"
          + "</div>");

      detailCard.innerHTML = ""
        + "<div class='flex items-start justify-between gap-3'>"
        + "<div><h3 class='text-sm font-bold text-slate-800'>Cluster: " + escapeHtml(clusterTitle || "Cluster") + "</h3>"
        + "<p class='text-[11px] text-text-muted mt-1 line-clamp-3'>" + escapeHtml(summaryText) + "</p></div>"
        + "<span class='px-2 py-0.5 rounded-full text-[10px] font-bold border bg-white/70'>" + escapeHtml(riskText) + "</span>"
        + "</div>"
        + "<div class='grid grid-cols-2 gap-3'>"
        + "<div><div class='text-[10px] uppercase tracking-wide text-slate-500 font-bold'>Penetration</div><div class='text-lg font-mono font-bold text-slate-800'>" + escapeHtml(penetrationText) + "</div></div>"
        + "<div><div class='text-[10px] uppercase tracking-wide text-slate-500 font-bold'>Cluster Size</div><div class='text-lg font-mono font-bold text-slate-800'>" + escapeHtml(clusterSizeText) + "</div></div>"
        + "</div>"
        + "<div class='w-full bg-slate-200 rounded-full h-2'><div class='h-full bg-primary rounded-full' style='width:" + escapeHtml(barWidth) + "'></div></div>"
        + "<div class='mt-1 pt-2 border-t border-white/50 flex items-center gap-2'>"
        + "<button type='button' data-stitch-action='" + (mode === "evidence" ? "insights_open_summary" : "insights_open_evidence") + "' data-cluster-key='" + escapeHtml(keyAttr) + "' class='size-6 rounded-full border border-primary/35 bg-primary/15 text-primary text-[11px] font-black flex items-center justify-center' title='Flip card'>•</button>"
        + "<button type='button' data-stitch-action='insights_open_summary' data-cluster-key='" + escapeHtml(keyAttr) + "' class='px-2 py-1 text-[10px] font-bold rounded-lg border border-white/70 bg-white/60 text-slate-600 hover:bg-white/90 transition-colors'>Summary</button>"
        + "<button type='button' data-stitch-action='insights_open_evidence' data-cluster-key='" + escapeHtml(keyAttr) + "' class='px-2 py-1 text-[10px] font-bold rounded-lg border border-primary/30 bg-primary/10 text-primary hover:bg-primary/15 transition-colors'>Show Evidence (10)</button>"
        + "</div>"
        + "<div class='mt-1 pt-2 border-t border-white/50'>"
        + detailsHtml
        + "</div>";
    }

    if (rightCol) {
      let compareCard = rightCol.querySelector("[data-insights-compare='1']");
      if (!compareCard) {
        compareCard = document.createElement("div");
        compareCard.setAttribute("data-insights-compare", "1");
        compareCard.className = "glass-panel rounded-2xl p-4";
        rightCol.appendChild(compareCard);
      }
      const cmp = data.comparePanel || {};
      const compareCandidates = Array.isArray(cmp.candidates) ? cmp.candidates : [];
      const similarCases = Array.isArray(cmp.similarCases) ? cmp.similarCases : [];
      const selectedComparePostId = String(cmp.rightPostId || "");
      compareCard.innerHTML = ""
        + "<div class='flex items-center justify-between mb-3'><h3 class='text-[10px] font-bold uppercase tracking-widest text-text-muted'>Similarity Compare</h3>"
        + "<span class='text-[10px] px-2 py-0.5 rounded-full bg-amber-100 border border-amber-200 text-amber-700 font-bold'>SAMPLE</span></div>"
        + "<div class='grid grid-cols-2 gap-3 text-xs'>"
        + "<div class='rounded-lg border border-white/60 bg-white/45 p-3'><div class='text-[10px] text-slate-500 mb-1'>Current Cluster</div><div class='font-bold text-slate-700'>"
        + escapeHtml(String(cmp.leftTitle || "-")) + "</div><div class='text-[10px] text-slate-500 mt-1'>" + escapeHtml(String(cmp.leftCount || 0)) + " comments</div></div>"
        + "<div class='rounded-lg border border-white/60 bg-white/45 p-3'><div class='text-[10px] text-slate-500 mb-1'>Compare Post</div><button type='button' data-stitch-action='insights_compare_post' data-post-id='"
        + escapeHtml(String(cmp.rightPostId || "")) + "' class='font-bold text-primary hover:underline'>" + escapeHtml(String(cmp.rightPostId || "-")) + "</button><div class='text-[10px] text-slate-500 mt-1 line-clamp-2'>" + escapeHtml(String(cmp.rightSnippet || "")) + "</div></div>"
        + "</div><div class='mt-3 text-xs text-slate-600'>Similarity: <span class='font-mono font-bold text-slate-800'>" + escapeHtml(String(cmp.similarity || 0)) + "%</span></div>"
        + "<div class='mt-3 pt-3 border-t border-white/50 space-y-2'>"
        + "<div class='text-[10px] uppercase tracking-wide font-bold text-slate-500'>Compare Target</div>"
        + (compareCandidates.length
            ? ("<div class='flex flex-wrap gap-1.5'>" + compareCandidates.slice(0, 6).map((candidate) => {
                const candidateId = String(candidate.id || "");
                const active = candidateId && candidateId === selectedComparePostId;
                return "<button type='button' data-stitch-action='insights_compare_post' data-post-id='" + escapeHtml(candidateId) + "' class='px-2 py-1 rounded-full border text-[10px] font-mono transition-all "
                  + (active ? "bg-primary/15 text-primary border-primary/30" : "bg-white/60 border-white/70 text-slate-500 hover:bg-white")
                  + "' title='" + escapeHtml(String(candidate.snippet || candidateId)) + "'>" + escapeHtml(candidateId.slice(0, 8) || "post") + "</button>";
              }).join("") + "</div>")
            : "<div class='text-[11px] text-slate-500'>No compare candidates from backend yet.</div>")
        + "</div>"
        + "<div class='mt-3 pt-3 border-t border-white/50 space-y-2'>"
        + "<div class='text-[10px] uppercase tracking-wide font-bold text-slate-500'>Similar Cases</div>"
        + (similarCases.length
            ? ("<div class='space-y-1.5 max-h-52 overflow-auto'>" + similarCases.slice(0, 8).map((item) =>
                "<div class='rounded-lg border border-white/70 bg-white/55 px-2.5 py-2'>"
                + "<div class='flex items-center justify-between gap-2'><span class='text-[10px] font-mono font-bold text-slate-700'>" + escapeHtml(String(item.postId || "").slice(0, 10)) + "</span>"
                + "<span class='text-[10px] font-mono text-primary font-bold'>" + escapeHtml(String(item.similarityPct || 0)) + "%</span></div>"
                + "<div class='text-[11px] text-slate-600 line-clamp-2 mt-1'>" + escapeHtml(String(item.snippet || "")) + "</div>"
                + "<div class='text-[10px] text-slate-500 mt-1 line-clamp-1'>" + escapeHtml(String((item.overlapSignals || []).join(" · "))) + "</div>"
                + "<div class='mt-2 flex items-center gap-1.5'><button type='button' data-stitch-action='insights_compare_post' data-post-id='" + escapeHtml(String(item.postId || "")) + "' class='px-2 py-1 rounded-lg text-[10px] font-bold bg-primary/10 border border-primary/25 text-primary hover:bg-primary/15'>Compare</button>"
                + "<button type='button' data-stitch-action='insights_go_review' data-post-id='" + escapeHtml(String(item.postId || "")) + "' class='px-2 py-1 rounded-lg text-[10px] font-bold bg-white/70 border border-white/75 text-slate-600 hover:bg-white'>Open Review</button></div>"
                + "</div>"
              ).join("") + "</div>")
            : "<div class='text-[11px] text-slate-500'>No similar cases yet.</div>")
        + "</div>"
        + ((cmp.drawerOpen && cmp.drawer)
            ? ("<div class='mt-3 pt-3 border-t border-white/50'><div class='rounded-xl border border-primary/25 bg-primary/5 p-3'>"
              + "<div class='flex items-center justify-between mb-1'><div class='text-[10px] uppercase tracking-wide text-primary font-bold'>Side-by-side Compare</div>"
              + "<button type='button' data-stitch-action='insights_close_compare' class='text-[10px] font-bold text-slate-500 hover:text-primary'>Close</button></div>"
              + "<div class='text-[11px] font-mono font-bold text-slate-700 mb-1'>Post " + escapeHtml(String(cmp.drawer.postId || "").slice(0, 10)) + "</div>"
              + "<div class='text-[11px] text-slate-600 line-clamp-2'>" + escapeHtml(String(cmp.drawer.snippet || "")) + "</div>"
              + "<div class='mt-2 flex items-center gap-2 text-[10px] text-slate-500'><span>♥ " + escapeHtml(String(cmp.drawer.likes ?? "—")) + "</span><span>↩ " + escapeHtml(String(cmp.drawer.replies ?? "—")) + "</span><span>⟲ " + escapeHtml(String(cmp.drawer.reposts ?? "—")) + "</span><span>⇪ " + escapeHtml(String(cmp.drawer.shares ?? "—")) + "</span></div>"
              + "</div></div>")
            : "");
    }

    const stabilityPanel = qsa("h2").find((el) => normalize(el.textContent).includes("isd stability diagnostics"))?.closest("div.glass-panel");
    if (stabilityPanel) {
      const evidenceCard = stabilityPanel.querySelector("[data-insights-evidence='1']");
      if (evidenceCard) evidenceCard.remove();
    }

    if (data.axis) {
      const axisHeading = qsa("h2").find((el) => normalize(el.textContent).includes("axis alignment"));
      if (axisHeading) {
        let mock = axisHeading.parentElement?.querySelector("[data-mock-axis='1']");
        if (!mock) {
          mock = document.createElement("span");
          mock.setAttribute("data-mock-axis", "1");
          mock.className = "ml-2 text-[10px] px-2 py-0.5 rounded-full bg-slate-100 border border-slate-200 text-slate-500 font-bold";
          axisHeading.appendChild(mock);
        }
        mock.textContent = "MOCK · SAMPLE (UI ONLY)";
      }
      setMetricByLabel("Semantic Match", data.axis.semanticMatch || "-");
      setMetricByLabel("Temporal Drift", data.axis.temporalDrift || "-");
      setMetricByLabel("Volume Impact", data.axis.volumeImpact || "-");
      setMetricByLabel("Reach Delta", data.axis.reachDelta || "-");
    }

    if (data.stability) {
      const isdHeading = qsa("h2").find((el) => normalize(el.textContent).includes("isd stability diagnostics"));
      if (isdHeading) {
        let mock = isdHeading.parentElement?.querySelector("[data-mock-isd='1']");
        if (!mock) {
          mock = document.createElement("span");
          mock.setAttribute("data-mock-isd", "1");
          mock.className = "ml-2 text-[10px] px-2 py-0.5 rounded-full bg-slate-100 border border-slate-200 text-slate-500 font-bold";
          isdHeading.appendChild(mock);
        }
        mock.textContent = "MOCK · SAMPLE (UI ONLY)";
      }
      const score = String(data.stability.scorePct || "0%");
      const scoreEl = qsa("span.text-success.font-black.text-lg").find((el) => /%/.test(String(el.textContent || "")));
      if (scoreEl) scoreEl.textContent = score;
      const verdict = qsa("div.text-\\[28px\\].font-black").find((el) => /stable|risk|degraded/i.test(String(el.textContent || "")));
      if (verdict) verdict.textContent = String(data.stability.verdict || "UNKNOWN");
      setMetricByLabel("Entropy", data.stability.entropy || "-");
      setMetricByLabel("Drift Score", data.stability.driftScore || "-");
    }
    reportHeight();
  };

  const renderReviewCards = (rows) => {
    const list = Array.isArray(rows) ? rows : [];
    return list.map((row) => {
      const tag = escapeHtml(String(row.tag || "#POST"));
      const title = escapeHtml(String(row.title || "Untitled"));
      const summary = escapeHtml(String(row.summary || ""));
      const risk = escapeHtml(String(row.risk || "Unknown"));
      const reliability = escapeHtml(String(row.reliability || "-"));
      const eng = escapeHtml(String(row.engagement || "-"));
      const dot = String(row.dotClass || "bg-status-cyan");
      const rid = escapeHtml(String(row.id || ""));
      return "<article class='group relative flex flex-col bg-glass-surface backdrop-blur-sm border border-white/60 rounded-3xl hover:shadow-glass-hover hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden shadow-glass' data-review-id='" + rid + "'>"
        + "<div class='absolute top-4 right-4 size-2 rounded-full " + dot + " shadow-[0_0_10px_rgba(6,182,212,0.6)]'></div>"
        + "<div class='p-5 flex flex-col h-full relative z-10'>"
        + "<div class='flex justify-between items-center mb-3'><span class='font-mono text-[10px] text-text-muted tracking-wide bg-white/60 px-2 py-1 rounded-lg'>" + tag + "</span></div>"
        + "<h3 class='text-sm font-bold text-text-main mb-1 leading-tight'>" + title + "</h3>"
        + "<p class='text-xs text-text-muted line-clamp-2 mb-4 font-medium leading-relaxed'>" + summary + "</p>"
        + "<div class='h-28 w-full bg-white/30 rounded-2xl border border-white/50 mb-4 flex items-center justify-center text-text-muted/40 shadow-inner'>"
        + "<span class='material-symbols-outlined text-4xl'>description</span></div>"
        + "<div class='mt-auto flex items-center justify-between pt-3 border-t border-gray-200/30'>"
        + "<div class='flex items-center gap-2'><span class='px-2 py-1 rounded-lg bg-white/80 text-[10px] font-bold border border-white/80'>" + risk + "</span>"
        + "<span class='text-[10px] text-text-muted font-medium'>Reliability " + reliability + "</span></div>"
        + "<span class='text-[10px] font-mono text-text-main font-bold bg-white/50 px-2 py-1 rounded-lg'>" + eng + "</span></div></div></article>";
    }).join("");
  };

  const updateReview = (payload) => {
    const data = payload || {};
    const indexed = qsa("div.text-xs.text-text-muted.font-mono").find((el) => normalize(el.textContent).startsWith("indexed:"));
    if (indexed) {
      indexed.innerHTML = "Indexed: <span class='text-text-main font-semibold bg-white/50 px-2 py-1 rounded-lg border border-white/50'>" + Number(data.total || 0).toLocaleString() + "</span>";
    }

    const grid = qs("main > div.grid");
    const cards = Array.isArray(data.cards) ? data.cards : [];
    if (grid) {
      grid.innerHTML = cards.length
        ? renderReviewCards(cards)
        : "<div class='col-span-full p-6 text-xs text-slate-500'>No evidence available for current filters.</div>";
    }

    const selected = data.selected || null;
    if (selected) {
      const idText = qsa("span.font-mono.text-\\[10px\\]").find((el) => normalize(el.textContent).startsWith("id:"));
      if (idText) idText.textContent = "ID: " + String(selected.id || "-");
      const riskTag = qsa("span.inline-flex.items-center.rounded-lg").find((el) => /risk/i.test(String(el.textContent || "")));
      if (riskTag) riskTag.textContent = String(selected.risk || "Unknown");
      const title = qs("aside h3.text-xl.font-bold");
      if (title) title.textContent = String(selected.title || "-");
      const context = qsa("p.text-xs.text-text-muted.font-medium").find((el) => normalize(el.textContent).startsWith("inspector context"));
      if (context) context.textContent = "Inspector Context: " + String(selected.context || "-");
      setFieldCardValue("Timestamp", selected.timestamp || "-");
      setFieldCardValue("Payload Size", selected.payloadSize || "-");
      setFieldCardValue("Entity Count", selected.entityCount || "-");
      setFieldCardValue("Hash Status", selected.hashStatus || "-");
      const fragment = qs("aside pre.font-mono");
      if (fragment) fragment.textContent = String(selected.fragment || "");
      const confidence = qsa("span").find((el) => normalize(el.textContent).includes("confidence"));
      if (confidence) confidence.textContent = String(selected.confidence || confidence.textContent || "");
      const chipsWrap = qsa("label").find((el) => normalize(el.textContent).startsWith("top linked entities"))?.parentElement?.querySelector("div.flex.flex-wrap");
      if (chipsWrap) {
        const entities = Array.isArray(selected.entities) ? selected.entities : [];
        chipsWrap.innerHTML = entities.map((x) =>
          "<span class='inline-flex items-center rounded-full bg-white/60 border border-white/80 px-3 py-1 text-xs font-medium text-text-muted shadow-sm'>"
          + escapeHtml(String(x || "")) + "</span>"
        ).join("");
      }
    }
    reportHeight();
  };

  const applyUpdate = (payload) => {
    const data = payload || {};
    const page = String(data.page || pageId || "");
    document.body.dataset.stitchPage = page;
    document.body.dataset.bridgeNodes = data.nodes != null ? String(data.nodes) : "";
    document.body.dataset.bridgeEdges = data.edges != null ? String(data.edges) : "";
    document.body.dataset.bridgeGraphNodes = Array.isArray(data.graphNodes) ? String(data.graphNodes.length) : "";
    document.body.dataset.bridgeGraphLinks = Array.isArray(data.graphLinks) ? String(data.graphLinks.length) : "";
    if (page === "overview") {
      updateOverview(data);
      return;
    }
    if (page === "pipeline") {
      updatePipeline(data);
      return;
    }
    if (page === "insights") {
      updateInsights(data);
      return;
    }
    if (page === "library") {
      updateLibrary(data);
      return;
    }
    if (page === "review") {
      updateReview(data);
      return;
    }
    if (data.demoLabel) showDemoBadge(data.demoLabel);
    reportHeight();
  };

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const phenNode = target.closest("[data-phen-id]");
    if (phenNode && !target.closest("input[type='checkbox']")) {
      const phenId = phenNode.getAttribute("data-phen-id") || "";
      if (phenId) {
        event.preventDefault();
        pulse(phenNode);
        post({ type: "stitch:action", action: "select_phenomenon", meta: Object.assign(collectMeta(), { phenomenonId: phenId }) });
        return;
      }
    }

    const reviewCard = target.closest("[data-review-id]");
    if (reviewCard) {
      const reviewId = reviewCard.getAttribute("data-review-id") || "";
      if (reviewId) {
        event.preventDefault();
        pulse(reviewCard);
        post({ type: "stitch:action", action: "select_review_item", meta: Object.assign(collectMeta(), { reviewId }) });
        return;
      }
    }

    const queuedCard = target.closest("[data-queued-id]");
    if (queuedCard) {
      const jobId = queuedCard.getAttribute("data-queued-id") || "";
      if (jobId) {
        event.preventDefault();
        pulse(queuedCard);
        post({ type: "stitch:action", action: "pipeline_select_run", meta: Object.assign(collectMeta(), { jobId }) });
        return;
      }
    }

    const anchor = target.closest("a[href]");
    if (anchor instanceof HTMLAnchorElement) {
      const href = anchor.getAttribute("href") || "";
      if (href.startsWith("/")) {
        event.preventDefault();
        pulse(anchor);
        routeLeave(href);
        return;
      }
    }

    const actionEl = target.closest("[data-stitch-action]");
    if (actionEl instanceof HTMLElement) {
      const action = actionEl.getAttribute("data-stitch-action") || "";
      if (action) {
        event.preventDefault();
        pulse(actionEl);
        const meta = collectMeta();
        const dataJobId = actionEl.getAttribute("data-job-id");
        const dataPostId = actionEl.getAttribute("data-post-id");
        const dataClusterKey = actionEl.getAttribute("data-cluster-key");
        const dataCommentId = actionEl.getAttribute("data-comment-id");
        const dataEvidenceId = actionEl.getAttribute("data-evidence-id");
        if (dataJobId) meta.jobId = dataJobId;
        if (dataPostId) {
          meta.postId = dataPostId;
          if (!meta.query) meta.query = dataPostId;
        }
        if (dataClusterKey) meta.clusterKey = dataClusterKey;
        if (dataCommentId) meta.commentId = dataCommentId;
        if (dataEvidenceId) meta.evidenceId = dataEvidenceId;
        post({ type: "stitch:action", action, meta });
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    const ph = normalize(target.placeholder || "");
    if (ph.includes("search hash")) {
      post({ type: "stitch:action", action: "library_search", meta: Object.assign(collectMeta(), { query: target.value || "" }) });
      return;
    }
    if (ph.includes("search node id") || ph.includes("search threads url") || ph.includes("post id")) {
      post({ type: "stitch:action", action: "insights_select_post", meta: Object.assign(collectMeta(), { query: target.value || "" }) });
    }
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const action = target.getAttribute("data-stitch-action-change");
    if (!action) return;
    const meta = collectMeta();
    if (target instanceof HTMLSelectElement) {
      meta.postId = target.value || "";
      meta.query = target.value || "";
    }
    post({ type: "stitch:action", action, meta });
  });

  window.addEventListener("message", (event) => {
    const data = event.data || {};
    if (data.type === "stitch:update") {
      applyUpdate(data.payload || {});
      return;
    }
    if (data.type === "stitch:notify") {
      showToast(data.message || "", data.kind || "info");
    }
  });

  unlockPageScroll();
  applyRoutesAndActions();
  reportHeight();
  window.addEventListener("resize", reportHeight);
  const observer = new MutationObserver(() => reportHeight());
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
  setInterval(reportHeight, 1200);
  post({ type: "stitch:ready", page: pageId });
})();
</script>`;

  if (html.includes("</body>")) {
    return html.replace("</body>", `${bridge}</body>`);
  }
  return `${html}${bridge}`;
}

export function StitchTemplateFrame({
  html,
  title,
  navMap = {},
  actionMap = {},
  actionSelectorMap = {},
  onAction,
  bridgeData,
  notice,
  pageId = "overview",
  hideTemplateHeader = true,
}: Props) {
  const navigate = useNavigate();
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const [frameReady, setFrameReady] = useState(false);
  const viewportFrameHeight = () => (typeof window === "undefined" ? 900 : Math.max(window.innerHeight - 76, 560));
  const [frameHeight, setFrameHeight] = useState<number>(() =>
    viewportFrameHeight()
  );
  const cachedBridgeData = useMemo(() => readBridgePayloadCache(pageId), [pageId]);
  const effectiveBridgeData = useMemo(() => {
    if (hasBridgePayload(pageId, bridgeData)) return bridgeData;
    if (hasBridgePayload(pageId, cachedBridgeData)) return cachedBridgeData;
    return bridgeData || cachedBridgeData || undefined;
  }, [bridgeData, cachedBridgeData, pageId]);

  const srcDoc = useMemo(
    () =>
      buildSrcDoc(html, {
        navMap,
        actionMap,
        actionSelectorMap,
        pageId,
        hideTemplateHeader,
      }),
    [html, navMap, actionMap, actionSelectorMap, pageId, hideTemplateHeader]
  );

  useEffect(() => {
    setFrameReady(false);
  }, [srcDoc]);

  useEffect(() => {
    if (!bridgeData || !hasBridgePayload(pageId, bridgeData)) return;
    writeBridgePayloadCache(pageId, bridgeData);
  }, [bridgeData, pageId]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!frameRef.current || event.source !== frameRef.current.contentWindow) return;
      const data = event.data as {
        type?: string;
        href?: string;
        action?: string;
        meta?: Record<string, unknown>;
        height?: number;
      };
      if (data?.type === "stitch:navigate" && data.href && data.href.startsWith("/")) {
        navigate(data.href);
        return;
      }
      if (data?.type === "stitch:height") {
        setFrameHeight((prev) => {
          const next = viewportFrameHeight();
          return Math.abs(next - prev) > 2 ? next : prev;
        });
        return;
      }
      if (data?.type === "stitch:ready") {
        setFrameReady(true);
        return;
      }
      if (data?.type === "stitch:action" && data.action) {
        void onAction?.(data.action, data.meta || {});
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [navigate, onAction]);

  useEffect(() => {
    if (!frameReady || !frameRef.current?.contentWindow || !effectiveBridgeData) return;
    frameRef.current.contentWindow.postMessage({ type: "stitch:update", payload: effectiveBridgeData }, "*");
  }, [effectiveBridgeData, frameReady]);

  useEffect(() => {
    if (!frameReady || !frameRef.current?.contentWindow || !notice) return;
    frameRef.current.contentWindow.postMessage(
      { type: "stitch:notify", message: notice.message, kind: notice.kind || "info", nonce: notice.nonce },
      "*"
    );
  }, [frameReady, notice]);

  useEffect(() => {
    const onResize = () => {
      setFrameHeight(viewportFrameHeight());
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const onLoad = () => {
    if (!frameRef.current?.contentWindow) return;
    if (effectiveBridgeData) {
      setTimeout(() => {
        frameRef.current?.contentWindow?.postMessage({ type: "stitch:update", payload: effectiveBridgeData }, "*");
      }, 40);
    }
  };

  return (
    <div style={{ width: "100%", height: "calc(100dvh - 76px)", minHeight: 560, overflow: "hidden" }}>
      <iframe
        ref={frameRef}
        title={title}
        srcDoc={srcDoc}
        onLoad={onLoad}
        style={{ width: "100%", height: `${frameHeight}px`, minHeight: "100%", border: "0", display: "block" }}
        sandbox="allow-scripts allow-forms allow-popups allow-downloads"
      />
    </div>
  );
}
