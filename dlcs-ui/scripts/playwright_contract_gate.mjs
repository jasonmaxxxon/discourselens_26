import { chromium, request } from "playwright";

const BACKEND = process.env.API_BASE_URL || "http://127.0.0.1:8000";
const UI_TARGETS = (process.env.UI_TARGETS || "http://127.0.0.1:5173,http://127.0.0.1:5174")
  .split(",")
  .map((x) => x.trim())
  .filter(Boolean);
const KNOWN_POST_ID = String(process.env.POST_ID || "440").trim();

function assert(cond, message) {
  if (!cond) throw new Error(message);
}

async function fetchJson(ctx, path) {
  const res = await ctx.get(path, { failOnStatusCode: false });
  const status = res.status();
  const headers = res.headers();
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { status, headers, body };
}

function assertHeader(obj, key, name) {
  const value = obj.headers[key] || obj.headers[key.toLowerCase()];
  assert(Boolean(value), `runtime drift: headers absent (${name})`);
  return String(value);
}

function assertAllowedStatus(path, status) {
  assert([200, 202, 404].includes(status), `${path} returned disallowed status ${status}`);
}

function assertTrace(path, obj) {
  const hasTraceHeader = Boolean(obj.headers["x-request-id"] || obj.headers["X-Request-ID"]);
  const body = obj.body && typeof obj.body === "object" ? obj.body : null;
  const hasTraceBody = Boolean(body && typeof body.trace_id === "string" && body.trace_id.trim());
  assert(hasTraceHeader || hasTraceBody, `${path} missing trace_id / X-Request-ID`);
}

async function probeUiTargets() {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const base of UI_TARGETS) {
      const page = await browser.newPage();
      const res = await page.goto(`${base}/insights`, { waitUntil: "domcontentloaded", timeout: 15000 });
      assert(Boolean(res), `UI unreachable: ${base}`);
      assert(Number(res.status()) < 500, `UI ${base} returned ${res.status()}`);

      const check = await page.evaluate(async (backend) => {
        const r = await fetch(`${backend}/api/_meta/build`);
        return {
          status: r.status,
          headers: {
            requestId: r.headers.get("x-request-id"),
            buildSha: r.headers.get("x-build-sha"),
            env: r.headers.get("x-env"),
          },
        };
      }, BACKEND);
      assert(check.status === 200, `${base}: /api/_meta/build not 200 (${check.status})`);
      assert(Boolean(check.headers.buildSha), `${base}: runtime drift: headers absent (X-Build-SHA)`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
}

async function run() {
  await probeUiTargets();

  const ctx = await request.newContext({ baseURL: BACKEND, extraHTTPHeaders: { "x-request-id": `pw-${Date.now()}` } });
  try {
    const build = await fetchJson(ctx, "/api/_meta/build");
    assert(build.status === 200, `/api/_meta/build expected 200 got ${build.status}`);
    assertHeader(build, "x-build-sha", "X-Build-SHA");
    assertHeader(build, "x-request-id", "X-Request-ID");
    assertHeader(build, "x-env", "X-Env");

    const posts = await fetchJson(ctx, "/api/posts");
    assert(posts.status === 200, `/api/posts expected 200 got ${posts.status}`);
    assertHeader(posts, "x-build-sha", "X-Build-SHA");
    assertHeader(posts, "x-request-id", "X-Request-ID");

    let phenomenonId = "missing-phenomenon";
    const listPh = await fetchJson(ctx, "/api/library/phenomena?limit=1");
    if (listPh.status === 200 && Array.isArray(listPh.body) && listPh.body.length) {
      phenomenonId = String(listPh.body[0]?.id || phenomenonId);
    }

    const targets = [
      `/api/analysis-json/${encodeURIComponent(KNOWN_POST_ID)}`,
      `/api/claims?post_id=${encodeURIComponent(KNOWN_POST_ID)}&limit=5`,
      `/api/evidence?post_id=${encodeURIComponent(KNOWN_POST_ID)}&limit=5`,
      `/api/clusters?post_id=${encodeURIComponent(KNOWN_POST_ID)}&limit=5&sample_limit=3`,
      `/api/clusters/${encodeURIComponent(KNOWN_POST_ID)}/graph`,
      `/api/library/phenomena/${encodeURIComponent(phenomenonId)}?limit=5`,
    ];

    for (const path of targets) {
      const row = await fetchJson(ctx, path);
      assertAllowedStatus(path, row.status);
      assertHeader(row, "x-build-sha", "X-Build-SHA");
      assertHeader(row, "x-request-id", "X-Request-ID");
      assertTrace(path, row);

      if (row.status === 202) {
        assert(row.body && row.body.status === "pending", `${path} 202 must return status=pending`);
        assert(typeof row.body.trace_id === "string" && row.body.trace_id.trim(), `${path} pending missing trace_id`);
      }
      if (row.status === 200 && row.body && typeof row.body === "object" && !Array.isArray(row.body)) {
        assert(
          typeof row.body.trace_id === "string" && row.body.trace_id.trim(),
          `${path} 200 object payload missing trace_id`
        );
      }
    }

    console.log("playwright contract gate passed");
  } finally {
    await ctx.dispose();
  }
}

run().catch((err) => {
  console.error(err?.message || err);
  process.exit(1);
});
