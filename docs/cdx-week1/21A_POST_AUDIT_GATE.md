# 21A POST: Audit UI Gate

## Goal
Make `npm run audit:ui:gate` a deterministic one-command gate:
1. boot backend + frontend
2. wait for readiness
3. run Playwright suite
4. exit with correct code

## Required Ports
- Frontend: `http://localhost:5173` (fixed for Playwright gate)
- Backend: `VITE_API_BASE_URL` or default `http://127.0.0.1:8000`
- Backend health probe: `GET /api/health` (HTTP 200 required)

## Entry Point
- Command (repo root): `npm run audit:ui:gate`
- Runner script: `/Users/tung/Desktop/DLens_26/scripts/audit_ui_gate.mjs`

## What The Gate Does
1. Starts backend via `uvicorn webapp.main:app --host <api-host> --port <api-port>`
2. Starts frontend in deterministic mode:
   - default: `npm run build` then `npm run preview -- --host localhost --strictPort --port 5173`
   - optional override: `AUDIT_UI_FRONTEND_MODE=dev npm run audit:ui:gate`
3. Waits for:
   - backend `GET /api/health` = 200
   - frontend `GET /` on `http://localhost:5173` (fallback probe `http://127.0.0.1:5173`) = 200
4. Runs `dlcs-ui/scripts/playwright_suite.mjs`
5. Cleans up spawned backend/frontend processes on both success and failure

## Expected Outputs
- Success:
  - Exit code `0`
  - stdout includes `[gate] audit:ui:gate passed`
  - Playwright report path under `artifacts/playwright-audit/suite_<timestamp>/`
- Failure:
  - Non-zero exit code
  - Clear failure reason (readiness timeout or Playwright failure)
  - Hints to logs:
    - `logs/audit_gate_backend.log`
    - `dlcs-ui/logs/audit_gate_frontend.log`

## Fail-Fast Behavior
- `playwright_suite.mjs` now preflights `UI_BASE_URL` before launching browser.
- If unreachable, it throws a clear message:
  - `UI base URL unreachable: http://localhost:5173 ...`
