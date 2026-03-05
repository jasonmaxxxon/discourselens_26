import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, "..");
const UI_DIR = path.join(ROOT, "dlcs-ui");

const FRONTEND_BIND_URL = "http://localhost:5173";
const FRONTEND_FALLBACK_URL = "http://127.0.0.1:5173";
const BACKEND_URL = process.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const BACKEND_HEALTH_URL = `${BACKEND_URL}/api/health`;

const FRONTEND_PORT = 5173;
const FRONTEND_HOST = "localhost";
const FRONTEND_MODE = process.env.AUDIT_UI_FRONTEND_MODE || "preview";

const backendBind = new URL(BACKEND_URL);
const BACKEND_HOST = backendBind.hostname || "127.0.0.1";
const BACKEND_PORT = Number(backendBind.port || "8000");

const logsDir = path.join(ROOT, "logs");
const uiLogsDir = path.join(UI_DIR, "logs");
const backendLogPath = path.join(logsDir, "audit_gate_backend.log");
const frontendLogPath = path.join(uiLogsDir, "audit_gate_frontend.log");

/** @type {Array<import('node:child_process').ChildProcess>} */
const children = [];
let shuttingDown = false;

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function log(msg) {
  process.stdout.write(`${msg}\n`);
}

function waitForExit(proc, timeoutMs = 4000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      resolve(undefined);
    };
    proc.once("exit", finish);
    setTimeout(finish, timeoutMs);
  });
}

async function stopProcess(proc, name) {
  if (!proc || proc.exitCode !== null || proc.killed || typeof proc.pid !== "number") return;
  try {
    process.kill(-proc.pid, "SIGTERM");
  } catch {
    // ignore
  }
  await waitForExit(proc, 3000);
  if (proc.exitCode === null) {
    try {
      process.kill(-proc.pid, "SIGKILL");
    } catch {
      // ignore
    }
    await waitForExit(proc, 2000);
  }
  log(`[cleanup] ${name} stopped`);
}

async function cleanupAll() {
  if (shuttingDown) return;
  shuttingDown = true;
  const rev = [...children].reverse();
  for (const item of rev) {
    if (item === children[0]) await stopProcess(item, "backend");
    else if (item === children[1]) await stopProcess(item, "frontend");
    else await stopProcess(item, "child");
  }
}

async function waitForHttp200(url, label, timeoutMs = 90000, intervalMs = 700) {
  const started = Date.now();
  let lastStatus = "";
  while (Date.now() - started < timeoutMs) {
    const probe = await probeHttp200(url);
    lastStatus = probe.message;
    if (probe.ok) {
      log(`[ready] ${label}: ${url}`);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`[gate] ${label} not ready at ${url} within ${Math.round(timeoutMs / 1000)}s (${lastStatus})`);
}

async function waitForAnyHttp200(urls, label, timeoutMs = 90000, intervalMs = 700) {
  const started = Date.now();
  let lastStatus = "";
  while (Date.now() - started < timeoutMs) {
    for (const url of urls) {
      const probe = await probeHttp200(url);
      lastStatus = `${url}: ${probe.message}`;
      if (probe.ok) {
        log(`[ready] ${label}: ${url}`);
        return url;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(
    `[gate] ${label} not ready at [${urls.join(", ")}] within ${Math.round(timeoutMs / 1000)}s (${lastStatus})`
  );
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
        timeout: 2500,
        family: parsed.hostname === "localhost" ? 4 : undefined,
      },
      (res) => {
        const status = Number(res.statusCode || 0);
        res.resume();
        resolve({ ok: status === 200, message: `HTTP ${status}` });
      }
    );
    req.on("timeout", () => {
      req.destroy(new Error("timeout"));
    });
    req.on("error", (err) => {
      resolve({ ok: false, message: String(err?.message || err) });
    });
    req.end();
  });
}

function startBackend() {
  ensureDir(logsDir);
  const out = fs.createWriteStream(backendLogPath, { flags: "w" });
  const proc = spawn(
    "uvicorn",
    ["webapp.main:app", "--host", BACKEND_HOST, "--port", String(BACKEND_PORT)],
    {
      cwd: ROOT,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  proc.stdout?.pipe(out);
  proc.stderr?.pipe(out);
  children.push(proc);
  log(`[boot] backend pid=${proc.pid} host=${BACKEND_HOST} port=${BACKEND_PORT}`);
  log(`[boot] backend log=${backendLogPath}`);
  return proc;
}

function runFrontendBuild(logPath) {
  const run = spawnSync("npm", ["run", "build"], {
    cwd: UI_DIR,
    env: { ...process.env, VITE_API_BASE_URL: BACKEND_URL },
    encoding: "utf-8",
  });
  fs.writeFileSync(logPath, `${run.stdout || ""}${run.stderr || ""}`, { flag: "w" });
  if (run.status !== 0) {
    throw new Error(`[gate] frontend build failed with exit code ${run.status}`);
  }
}

function startFrontend() {
  ensureDir(uiLogsDir);
  const out = fs.createWriteStream(frontendLogPath, { flags: "w" });
  let args;
  if (FRONTEND_MODE === "dev") {
    args = ["run", "dev", "--", "--host", FRONTEND_HOST, "--strictPort", "--port", String(FRONTEND_PORT)];
  } else {
    runFrontendBuild(frontendLogPath);
    args = ["run", "preview", "--", "--host", FRONTEND_HOST, "--strictPort", "--port", String(FRONTEND_PORT)];
  }
  const proc = spawn(
    "npm",
    args,
    {
      cwd: UI_DIR,
      env: { ...process.env, VITE_API_BASE_URL: BACKEND_URL },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  proc.stdout?.pipe(out);
  proc.stderr?.pipe(out);
  children.push(proc);
  log(`[boot] frontend pid=${proc.pid} mode=${FRONTEND_MODE} bind=${FRONTEND_BIND_URL}`);
  log(`[boot] frontend log=${frontendLogPath}`);
  return proc;
}

function runPlaywrightSuite(uiBaseUrl) {
  return new Promise((resolve, reject) => {
    const proc = spawn("node", ["scripts/playwright_suite.mjs"], {
      cwd: UI_DIR,
      env: { ...process.env, UI_BASE_URL: uiBaseUrl },
      stdio: "inherit",
    });
    proc.on("error", reject);
    proc.on("exit", (code) => {
      if (code === 0) resolve(undefined);
      else reject(new Error(`[gate] playwright suite failed with exit code ${code}`));
    });
  });
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    await cleanupAll();
    process.exit(130);
  });
}

process.on("uncaughtException", async (err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  await cleanupAll();
  process.exit(1);
});

process.on("unhandledRejection", async (err) => {
  process.stderr.write(`${err?.stack || err}\n`);
  await cleanupAll();
  process.exit(1);
});

async function main() {
  log(`[gate] backend target: ${BACKEND_URL}`);
  log(`[gate] frontend bind target: ${FRONTEND_BIND_URL}`);
  startBackend();
  startFrontend();
  await waitForHttp200(BACKEND_HEALTH_URL, "backend health");
  const reachableUiBase = await waitForAnyHttp200([FRONTEND_BIND_URL, FRONTEND_FALLBACK_URL], "frontend root");
  await runPlaywrightSuite(reachableUiBase);
  log("[gate] audit:ui:gate passed");
}

main()
  .then(async () => {
    await cleanupAll();
    process.exit(0);
  })
  .catch(async (err) => {
    process.stderr.write(`${err?.stack || err}\n`);
    process.stderr.write(`[hint] backend log: ${backendLogPath}\n`);
    process.stderr.write(`[hint] frontend log: ${frontendLogPath}\n`);
    await cleanupAll();
    process.exit(1);
  });
