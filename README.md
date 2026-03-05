# DiscourseLens V6 (DLens_26)

Threads 貼文抓取 + Supabase SoT + deterministic preanalysis + claims-only LLM + Ops 儀表板。

這個 repo 同時包含：
- Backend：FastAPI + Jinja console
- Frontend：React (Vite)
- Scraper：Playwright 抓取 Threads
- Analysis：preanalysis + claims-only (audited)
- Supabase schema / migrations

更多設計細節：
- `docs/INDEX.md`
- `docs/SYSTEM_OVERVIEW.md`
- `docs/FLOWS.md`
- `docs/ENDPOINTS.md`
- `docs/CONTRACTS.md`
- `docs/TOPIC_CONTRACT_V1.md`
- `docs/SCHEMA.md`
- `docs/MIGRATIONS.md`
- `docs/ARCHITECTURE.md`
- `docs/DEV_RUNBOOK.md`

**Topic Engine 現況（2026-03-05）**
- 已落地：Topic Contract v1、Phase-2 Topic SoT migration（`topic_runs/topic_posts/topic_meta_clusters/topic_lifecycle_daily`）。
- 已落地（Phase-3 skeleton）：`POST /api/topics/run`、`GET /api/topics/{topic_id}`（immutable registry + deterministic hash + idempotent create）。
- 已落地（Phase-3.5 skeleton）：`POST /api/topics/worker/run-once`（lease lock + deterministic snapshot stats overwrite）。
- 尚未落地：meta-cluster/lifecycle compute、Topic UI 專屬頁面。
- Hash contract/golden check：`python3 scripts/verify_topic_contract_golden.py`

**主流程骨幹（S1–S6）**
1. S1 Fetch + Ingest (SoT)
說明：`scraper/fetcher.py` 產出 artifacts，`scripts/run_fetcher_and_ingest.py` → `webapp/services/ingest_sql.py` 寫入 Supabase，SoT tables 為 `threads_posts` / `threads_comments` / `threads_comment_edges`。

2. S2 Preanalysis (Deterministic)
說明：`analysis/preanalysis_runner.py` 產出 `preanalysis_json`、cluster assignments、reply matrix、physics/golden samples。

3. S3 ISD + CIP (Semantic Layer)
說明：`analysis/diagnostics/isd.py` 計算 label 穩定度 + evidence gate，`analysis/cluster_interpretation.py` 產生 cluster label/summary，語意寫回受到 `database/integrity.py` run_id + allowlist 嚴格限制。

4. S4 Claims-only (LLM)
說明：`analysis/analyst.py` 的 `claims_only` 路徑產生 claims，並經 `analysis/claims/*` audit 後寫入 `threads_claims` / `threads_claim_evidence` / `threads_claim_audits`。

5. S6 Behavior/Risk
說明：`analysis/behavior_sidechannel.py` + `analysis/behavior_budget.py` + `analysis/risk_composer_min.py` 產生風險簡報，寫入 `threads_behavior_audits` / `threads_risk_briefs`。

**Pipeline 概覽（現況）**
- Pipeline A：單貼 URL 抓取 → SoT → preanalysis → claims-only（可選）
- Pipeline B：關鍵字批次抓取（目前依賴缺失模組，見「Known Gaps」）
- Pipeline C：個人主頁批次抓取（目前依賴缺失模組，見「Known Gaps」）

**Ops / Jobs API（Supabase）**
- `webapp/services/job_manager.py` 依賴 Supabase RPC: `claim_job_item`, `set_job_item_stage`, `complete_job_item`, `fail_job_item`, `bump_job_counters`, `finalize_job_if_done`
- REST API：`/api/jobs/*`（取代 legacy in-memory jobs）

**Frontend 路由（React）**
- 主路由（Stitch）：`/overview`, `/pipeline`, `/insights`, `/library`, `/review`
- Legacy 路由：`/legacy/overview`, `/legacy/pipeline`, `/legacy/insights`, `/legacy/library`, `/legacy/review`
- UI 現況：目前仍以 post-centric 工作流為主（Detect/Investigate/Compare），Topic 專屬操作尚未接入。

**Quick Start（macOS / zsh）**
Backend
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
touch .env   # 填入 SUPABASE_URL/SUPABASE_KEY/GEMINI_API_KEY
uvicorn webapp.main:app --reload --port 8000
```

Frontend
```bash
cd dlcs-ui
npm install
npm run dev -- --port 5173
```

一鍵（Backend + Frontend）
```bash
./start.sh
```

**環境變數（常用）**
- `SUPABASE_URL`, `SUPABASE_KEY`（或 `SUPABASE_SERVICE_ROLE_KEY`）
- `GEMINI_API_KEY` / `GOOGLE_API_KEY`
- `DL_NARRATIVE_MODE=claims_only`（只跑 claims）
- `DL_ENABLE_CIP=1`（啟用 CIP）
- `DL_ENABLE_PHENOMENON_ENRICHMENT=false`（預設關閉）

**Fetcher + Ingest（單貼）**
```bash
python3 scripts/run_fetcher_and_ingest.py "<threads_url>" --headless
```

**Preanalysis（deterministic）**
```bash
python3 scripts/run_preanalysis.py --post-id 410 --prefer-sot --persist-assignments
```

**Topic Contract Golden Check**
```bash
PYTHONPATH=. python3 scripts/verify_topic_contract_golden.py
```

**Topic Merge Gates**
```bash
make topic:migration_smoke
make topic:api_contract
make topic:worker_smoke
```

**Docker（fetch + ingest）**
```bash
docker compose run --rm crawler \
  -e URL="https://www.threads.net/@.../post/..."
```

**重要檔案地圖（實際存在）**
- `webapp/`：API + Jinja console
- `analysis/`：preanalysis / claims / diagnostics / risk
- `scraper/`：Playwright fetch + HTML parser
- `database/`：Supabase client + writeback
- `dlcs-ui/`：React 前端
- `supabase/`：schema/migrations
- `scripts/`：CLI runners

**Known Gaps（需補齊或移除）**
以下模組在程式中被引用，但此 repo 內不存在，會導致 Pipeline B/C 失敗：
- `pipelines/core.py`
- `event_crawler.py`
- `home_crawler.py`

若要啟用 B/C，請補齊對應模組或移除相關路徑與功能。
