import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { chromium } from "playwright";

const BASE_URL = process.env.UI_BASE_URL || "http://localhost:5173";
const OUT_ROOT = path.resolve("../artifacts/playwright-audit");
const WHITE_STRIP_RATIO_THRESHOLD = 0.94;

function nowStamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function analyzeImages(imagePaths) {
  const code = `
import json, sys
from PIL import Image, ImageChops, ImageStat

paths = json.loads(sys.argv[1])
imgs = [Image.open(p).convert("RGB") for p in paths]
out = {"white_ratio": {}, "pair_mad": {}}

for p, im in zip(paths, imgs):
    data = list(im.getdata())
    white = sum(1 for r, g, b in data if r > 245 and g > 245 and b > 245)
    out["white_ratio"][p] = white / max(1, len(data))

for i in range(len(paths) - 1):
    a, b = imgs[i], imgs[i + 1]
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    mad = sum(stat.mean) / len(stat.mean)
    out["pair_mad"][f"{paths[i]} -> {paths[i+1]}"] = mad

print(json.dumps(out))
`;
  const run = spawnSync("python3", ["-c", code, JSON.stringify(imagePaths)], { encoding: "utf-8" });
  if (run.status !== 0) {
    return { error: run.stderr || run.stdout || "image analysis failed" };
  }
  try {
    return JSON.parse(run.stdout.trim() || "{}");
  } catch {
    return { error: "invalid image analysis output", raw: run.stdout };
  }
}

function parsePersistedPointers(raw) {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed?.state || null;
  } catch {
    return null;
  }
}

async function readPersistedPointers(page) {
  const raw = await page.evaluate(() => localStorage.getItem("dl.intelligence.pointers.v1"));
  return parsePersistedPointers(raw);
}

async function waitForPersistedState(page, timeoutMs = 4000, intervalMs = 250) {
  const started = Date.now();
  let latest = await readPersistedPointers(page);
  while (Date.now() - started < timeoutMs) {
    if (latest?.currentPost?.id && latest?.lastRun?.id) return latest;
    await page.waitForTimeout(intervalMs);
    latest = await readPersistedPointers(page);
  }
  return latest;
}

async function capture(page, filePath, opts = {}) {
  await page.screenshot({ path: filePath, ...opts });
  return filePath;
}

function probeHttp200(url) {
  return new Promise((resolve) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch (err) {
      resolve({ ok: false, message: `invalid url (${String(err)})` });
      return;
    }
    const client = parsed.protocol === "https:" ? https : http;
    const req = client.request(
      {
        hostname: parsed.hostname,
        port: parsed.port ? Number(parsed.port) : parsed.protocol === "https:" ? 443 : 80,
        path: `${parsed.pathname}${parsed.search}`,
        method: "GET",
        timeout: 3000,
        family: parsed.hostname === "localhost" ? 4 : undefined,
      },
      (res) => {
        const status = Number(res.statusCode || 0);
        res.resume();
        resolve({ ok: status >= 200 && status < 400, message: `HTTP ${status}` });
      }
    );
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", (err) => resolve({ ok: false, message: String(err?.message || err) }));
    req.end();
  });
}

async function ensureBaseUrlReachable(url) {
  const probe = await probeHttp200(url);
  try {
    if (!probe.ok) {
      throw new Error(probe.message);
    }
  } catch (err) {
    throw new Error(`UI base URL unreachable: ${url}. Start frontend first or run "npm run audit:ui:gate" from repo root. (${err?.message || err})`);
  }
}

async function run() {
  await ensureBaseUrlReachable(BASE_URL);

  const runDir = path.join(OUT_ROOT, `suite_${nowStamp()}`);
  const shotsDir = path.join(runDir, "screenshots");
  ensureDir(shotsDir);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1512, height: 982 } });
  const page = await context.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push({ text: msg.text(), location: msg.location() });
    }
  });
  page.on("pageerror", (err) => {
    pageErrors.push({ message: err.message, stack: err.stack });
  });

  await page.addInitScript(() => {
    window.__dl_suite = { cls: 0, shifts: [] };
    const obs = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (!e.hadRecentInput) {
          window.__dl_suite.cls += e.value;
          window.__dl_suite.shifts.push({ value: e.value, startTime: e.startTime });
        }
      }
    });
    obs.observe({ type: "layout-shift", buffered: true });
  });

  // Suite 1: Route Motion Sanity
  const routeShots = [];
  const routeSteps = [];
  await page.goto(`${BASE_URL}/overview`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(1200);
  routeShots.push(await capture(page, path.join(shotsDir, "route_00_overview.png")));

  const keys = ["ArrowRight", "ArrowRight", "ArrowRight", "ArrowRight", "ArrowLeft", "ArrowLeft", "ArrowLeft", "ArrowLeft"];
  for (let i = 0; i < keys.length; i += 1) {
    const key = keys[i];
    const before = page.url();
    const t0 = Date.now();
    await page.keyboard.press(key);
    await page.waitForTimeout(70);
    const immediateRouteFrame = (await page.locator(".route-frame").count()) > 0;
    await page.waitForTimeout(820);
    const after = page.url();
    const shot = await capture(page, path.join(shotsDir, `route_${String(i + 1).padStart(2, "0")}.png`));
    routeShots.push(shot);
    routeSteps.push({
      key,
      before,
      after,
      ms: Date.now() - t0,
      immediateRouteFrame,
    });
  }
  const routeImageMetrics = analyzeImages(routeShots);

  // Suite 2: Phantom Artifact / Overflow Exorcism
  await page.goto(`${BASE_URL}/pipeline`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(1300);
  const iframes = await page.evaluate(() =>
    Array.from(document.querySelectorAll("iframe")).map((x) => ({
      src: x.getAttribute("src") || "",
      id: x.id || "",
      className: x.className || "",
    }))
  );
  const iframeWhitelist = [/^$/, /^about:blank$/, /^https?:\/\/localhost(?::\d+)?\//, /^https?:\/\/127\.0\.0\.1(?::\d+)?\//];
  const disallowedIframes = iframes.filter((f) => !iframeWhitelist.some((re) => re.test(f.src)));

  const overflowState = await page.evaluate(() => {
    const root = document.getElementById("root");
    const shell = document.querySelector(".shell");
    const b = getComputedStyle(document.body);
    const r = root ? getComputedStyle(root) : null;
    const s = shell ? getComputedStyle(shell) : null;
    return {
      body: { overflowX: b.overflowX, overflowY: b.overflowY, minHeight: b.minHeight, height: b.height },
      root: r ? { overflowX: r.overflowX, overflowY: r.overflowY, minHeight: r.minHeight, height: r.height } : null,
      shell: s ? { minHeight: s.minHeight, height: s.height } : null,
    };
  });

  const bottomStripChecks = [];
  for (const route of [
    { name: "overview", path: "/overview" },
    { name: "pipeline", path: "/pipeline" },
    { name: "insights", path: "/insights" },
  ]) {
    await page.goto(`${BASE_URL}${route.path}`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForTimeout(1300);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(500);
    const vp = page.viewportSize();
    const shot = await capture(page, path.join(shotsDir, `bottom_50px_${route.name}.png`), {
      clip: { x: 0, y: Math.max(0, (vp?.height || 982) - 50), width: vp?.width || 1512, height: 50 },
    });
    const metrics = analyzeImages([shot]);
    const whiteRatio = metrics?.white_ratio?.[shot] ?? null;
    bottomStripChecks.push({
      route: route.name,
      screenshot: shot,
      whiteRatio,
      suspect: Number(whiteRatio || 0) > WHITE_STRIP_RATIO_THRESHOLD,
    });
  }

  // Suite 3: Topbar state persistence
  const c2 = await browser.newContext({ viewport: { width: 1512, height: 982 } });
  await c2.addInitScript(() => localStorage.clear());
  const p2 = await c2.newPage();
  await p2.goto(`${BASE_URL}/overview`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await p2.waitForTimeout(1200);
  const topbarQueueTextFresh = (await p2.locator(".job-drawer-btn").first().textContent()) || "";

  await p2.goto(`${BASE_URL}/insights`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await p2.waitForTimeout(2400);
  const stateAfterInsights = await readPersistedPointers(p2);
  const rerunBtn = p2.locator("button:has-text('Re-run Analysis')").first();
  if (await rerunBtn.count()) {
    if (await rerunBtn.isEnabled()) {
      await rerunBtn.click();
      await p2.waitForTimeout(2600);
    }
  }
  const stateBeforeReload = await readPersistedPointers(p2);

  await p2.reload({ waitUntil: "domcontentloaded" });
  await p2.waitForTimeout(1200);
  const stateAfterReload = await waitForPersistedState(p2);

  const postPersisted = Boolean(
    stateBeforeReload?.currentPost?.id &&
      stateAfterReload?.currentPost?.id &&
      stateBeforeReload.currentPost.id === stateAfterReload.currentPost.id
  );
  const lastRunPersisted = Boolean(stateBeforeReload?.lastRun?.id && stateAfterReload?.lastRun?.id);
  const lastRunChangedAfterReload = Boolean(
    stateBeforeReload?.lastRun?.id &&
      stateAfterReload?.lastRun?.id &&
      stateBeforeReload.lastRun.id !== stateAfterReload.lastRun.id
  );

  await capture(p2, path.join(shotsDir, "topbar_after_reload.png"));
  await c2.close();

  // Pipeline idle-state assertion: no stale/current-run when 0 running + 0 queued
  await page.goto(`${BASE_URL}/pipeline`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(1200);
  const queueText = ((await page.locator(".job-drawer-btn").first().textContent()) || "").toLowerCase();
  const queueMatch = queueText.match(/(\d+)\s*running\s*·\s*(\d+)\s*queued/);
  const runningCount = queueMatch ? Number(queueMatch[1]) : null;
  const queuedCount = queueMatch ? Number(queueMatch[2]) : null;
  const stageVerboseElementCount = await page.locator(".liquid-stage-note, .liquid-stage-db").count();
  const stageNodeCount = await page.locator(".pipeline-stage-node").count();
  const pipelineIdleCheck = {
    checked: runningCount === 0 && queuedCount === 0,
    runningCount,
    queuedCount,
    runChipVisible: false,
    staleTagVisible: false,
    idleReadyVisible: false,
    pass: true,
    details: "",
  };
  if (pipelineIdleCheck.checked) {
    pipelineIdleCheck.runChipVisible = (await page.locator(".run-chip").count()) > 0;
    pipelineIdleCheck.staleTagVisible = (await page.locator(".run-chip .stale-tag").count()) > 0;
    pipelineIdleCheck.idleReadyVisible = (await page.locator("[data-testid='pipeline-idle-ready']").count()) > 0;
    pipelineIdleCheck.pass =
      !pipelineIdleCheck.runChipVisible &&
      !pipelineIdleCheck.staleTagVisible &&
      pipelineIdleCheck.idleReadyVisible;
    if (!pipelineIdleCheck.pass) {
      pipelineIdleCheck.details = "Expected idle-ready without stale/current-run chip for 0/0";
    }
  } else {
    pipelineIdleCheck.details = "Skipped idle assertion because active/queued jobs exist";
  }

  // Deep-link assertion: Detect bucket -> Library with filters
  await page.goto(`${BASE_URL}/overview`, { waitUntil: "domcontentloaded", timeout: 60000 });
  for (let i = 0; i < 10; i += 1) {
    if ((await page.locator("button:has-text('Open Investigate Window')").count()) > 0) break;
    await page.waitForTimeout(300);
  }
  for (let i = 0; i < 20; i += 1) {
    if ((await page.locator(".timeline-bucket").count()) > 0) break;
    await page.waitForTimeout(300);
  }
  await page.waitForTimeout(400);
  const timelineBucket = page.locator(".timeline-bucket").first();
  const hasTimelineBucket = (await timelineBucket.count()) > 0;
  const bucketEnabled = hasTimelineBucket ? await timelineBucket.isEnabled() : false;
  let deepLinkBucketNavigatesToLibraryWithFilters = false;
  let deepLinkDetails = { hasTimelineBucket, bucketEnabled, urlAfterClick: "", params: {} };
  if (hasTimelineBucket && bucketEnabled) {
    await timelineBucket.click();
    await page.waitForTimeout(1000);
    const urlAfterClick = page.url();
    const u = new URL(urlAfterClick);
    const params = {
      post_id: u.searchParams.get("post_id"),
      t0: u.searchParams.get("t0"),
      t1: u.searchParams.get("t1"),
      cluster_key: u.searchParams.get("cluster_key"),
    };
    deepLinkBucketNavigatesToLibraryWithFilters = Boolean(
      u.pathname.includes("/library") && params.post_id && params.t0 && params.t1
    );
    deepLinkDetails = { hasTimelineBucket, urlAfterClick, params };
  }

  // Casebook consistency assertion: save -> summary -> export -> DB snapshot parity
  let casebookSnapshotConsistency = {
    attempted: false,
    saved: false,
    summaryVisible: false,
    partialDisclaimerVisible: null,
    exportDownloaded: false,
    exportMatchesDbSnapshot: false,
    overlayBadgeVisible: false,
    mismatchedFields: [],
    latestCasebookId: null,
    exportedCasebookId: null,
    details: "",
  };
  if (deepLinkBucketNavigatesToLibraryWithFilters) {
    const deepLinkPostId = deepLinkDetails?.params?.post_id || null;
    await page.waitForTimeout(1200);
    const firstMomentumItem = page.locator(".momentum-item").first();
    if ((await firstMomentumItem.count()) > 0) {
      casebookSnapshotConsistency.attempted = true;
      await firstMomentumItem.click({ button: "right" });
      const saveToCasebookBtn = page.locator(".context-menu-lite button:has-text('Save to Casebook')").first();
      if ((await saveToCasebookBtn.count()) > 0 && (await saveToCasebookBtn.isEnabled())) {
        await saveToCasebookBtn.click();
        casebookSnapshotConsistency.saved = true;
        await page.waitForTimeout(1200);

        const summaryLines = await page.locator(".casebook-summary-snapshot .row-sub").allInnerTexts();
        casebookSnapshotConsistency.summaryVisible = summaryLines.length > 0;

        let latestDbItem = null;
        if (deepLinkPostId) {
          const dbResp = await context.request.get(
            `${BASE_URL}/api/casebook?post_id=${encodeURIComponent(deepLinkPostId)}&limit=1`
          );
          if (dbResp.ok()) {
            const dbJson = await dbResp.json();
            latestDbItem = dbJson?.items?.[0] || null;
            casebookSnapshotConsistency.latestCasebookId = latestDbItem?.id || null;
          } else {
            casebookSnapshotConsistency.details = `DB read failed: HTTP ${dbResp.status()}`;
          }
        }

        const exportPath = path.join(runDir, "casebook_export.json");
        try {
          const downloadPromise = page.waitForEvent("download", { timeout: 15000 });
          await page.locator("button:has-text('Export JSON')").first().click();
          const download = await downloadPromise;
          await download.saveAs(exportPath);
          casebookSnapshotConsistency.exportDownloaded = true;

          const parsedExport = JSON.parse(fs.readFileSync(exportPath, "utf-8"));
          const exportedFirst = parsedExport?.items?.[0] || null;
          casebookSnapshotConsistency.exportedCasebookId = exportedFirst?.id || null;
          if (exportedFirst?.coverage?.is_truncated === true) {
            casebookSnapshotConsistency.partialDisclaimerVisible = summaryLines.some((line) =>
              line.toLowerCase().includes("partial dataset")
            );
          }
          if (latestDbItem && exportedFirst) {
            const fields = [
              "id",
              "post_id",
              "evidence_id",
              "comment_id",
              "evidence_text",
              "captured_at",
              "bucket",
              "metrics_snapshot",
              "coverage",
              "summary_version",
              "filters",
              "analyst_note",
            ];
            const mismatches = fields.filter(
              (field) => JSON.stringify(exportedFirst[field] ?? null) !== JSON.stringify(latestDbItem[field] ?? null)
            );
            casebookSnapshotConsistency.mismatchedFields = mismatches;
            casebookSnapshotConsistency.exportMatchesDbSnapshot = mismatches.length === 0;
          } else {
            casebookSnapshotConsistency.details = "Missing DB row or exported row for parity check";
          }
        } catch (e) {
          casebookSnapshotConsistency.details = `Export flow failed: ${e?.message || e}`;
        }

        await page.goto(`${BASE_URL}/overview`, { waitUntil: "domcontentloaded", timeout: 60000 });
        for (let i = 0; i < 20; i += 1) {
          if ((await page.locator(".timeline-bucket").count()) > 0) break;
          await page.waitForTimeout(250);
        }
        await page.waitForTimeout(500);
        casebookSnapshotConsistency.overlayBadgeVisible =
          (await page.locator("[data-testid='timeline-casebook-badge']").count()) > 0;
      } else {
        casebookSnapshotConsistency.details = "Save to Casebook button unavailable";
      }
    } else {
      casebookSnapshotConsistency.details = "No momentum items available for context menu save";
    }
  } else {
    casebookSnapshotConsistency.details = "Skipped because deep-link bucket assertion did not reach library";
  }

  // Insights duplicate removal check: rendered keys must be unique.
  await page.goto(`${BASE_URL}/insights`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(2200);
  const renderedTimelineKeys = await page
    .locator("[data-testid='insights-timeline-item']")
    .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-evidence-key") || ""));
  const nonEmptyTimelineKeys = renderedTimelineKeys.filter((key) => key.trim().length > 0);
  const uniqueTimelineKeys = new Set(nonEmptyTimelineKeys);
  const dedupeBadgeText =
    (await page.locator("[data-testid='insights-duplicates-removed']").first().textContent().catch(() => "")) || "";
  const insightsDuplicateRemovalCheck = {
    checked: nonEmptyTimelineKeys.length > 0,
    renderedCount: renderedTimelineKeys.length,
    nonEmptyKeyCount: nonEmptyTimelineKeys.length,
    uniqueKeyCount: uniqueTimelineKeys.size,
    duplicateCount: nonEmptyTimelineKeys.length - uniqueTimelineKeys.size,
    dedupeBadgeText: dedupeBadgeText.trim(),
    pass: true,
    details: "",
  };
  if (insightsDuplicateRemovalCheck.checked) {
    insightsDuplicateRemovalCheck.pass = insightsDuplicateRemovalCheck.duplicateCount === 0;
    if (!insightsDuplicateRemovalCheck.pass) {
      insightsDuplicateRemovalCheck.details = "Duplicate evidence keys rendered in insights timeline list";
    }
  } else {
    insightsDuplicateRemovalCheck.details = "Skipped because timeline list is empty";
  }

  const report = {
    generatedAt: new Date().toISOString(),
    baseUrl: BASE_URL,
    suiteDir: runDir,
    routeMotionSanity: {
      steps: routeSteps,
      imageMetrics: routeImageMetrics,
      allImmediateRouteFrame: routeSteps.every((x) => x.immediateRouteFrame),
    },
    phantomArtifactOverflowExorcism: {
      iframeCount: iframes.length,
      disallowedIframes,
      overflowState,
      bottomStripChecks,
      hasBottomWhiteStripSuspect: bottomStripChecks.some((item) => item.suspect),
      whiteStripThreshold: WHITE_STRIP_RATIO_THRESHOLD,
    },
    topbarIntelligence: {
      topbarQueueTextFresh: topbarQueueTextFresh.trim(),
      postPersistedAfterReload: postPersisted,
      lastRunPersistedAfterReload: lastRunPersisted,
      lastRunChangedAfterReload,
      persistedStateSample: {
        afterInsights: stateAfterInsights,
        beforeReload: stateBeforeReload,
        afterReload: stateAfterReload,
      },
    },
    pipelineIdleCheck,
    pipelineStageMinimality: {
      stageNodeCount,
      stageVerboseElementCount,
      pass: stageVerboseElementCount === 0,
    },
    deepLinkAssertion: {
      deepLinkBucketNavigatesToLibraryWithFilters,
      details: deepLinkDetails,
    },
    casebookSnapshotConsistency,
    insightsDuplicateRemovalCheck,
    runtimeSignals: {
      cls: await page.evaluate(() => window.__dl_suite?.cls || 0),
      layoutShiftCount: await page.evaluate(() => (window.__dl_suite?.shifts || []).length),
      consoleErrorCount: consoleErrors.length,
      pageErrorCount: pageErrors.length,
      consoleErrors,
      pageErrors,
    },
  };

  const reportPath = path.join(runDir, "suite_report.json");
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  const gateFailures = [];
  if (report.phantomArtifactOverflowExorcism.hasBottomWhiteStripSuspect) {
    gateFailures.push("Bottom white strip suspected on one or more pages.");
  }
  if (!report.pipelineStageMinimality.pass) {
    gateFailures.push("Pipeline stage area still contains verbose note/db blocks.");
  }
  if (report.pipelineIdleCheck.checked && !report.pipelineIdleCheck.pass) {
    gateFailures.push(`Pipeline idle check failed: ${report.pipelineIdleCheck.details}`);
  }
  if (report.insightsDuplicateRemovalCheck.checked && !report.insightsDuplicateRemovalCheck.pass) {
    gateFailures.push(`Insights duplicate removal check failed: ${report.insightsDuplicateRemovalCheck.details}`);
  }
  if (gateFailures.length) {
    await browser.close();
    throw new Error(`[audit:ui] ${gateFailures.join(" | ")} (report: ${reportPath})`);
  }
  await browser.close();
  process.stdout.write(`${reportPath}\n`);
}

run().catch((err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  process.exit(1);
});
