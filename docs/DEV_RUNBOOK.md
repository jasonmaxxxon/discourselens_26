# Dev Runbook

**Prerequisites**
- Python 3.10+ (本機 `python3`)
- Node.js 18+
- Supabase 專案與對應的 URL / Key
- Playwright 依賴 (Linux/Mac 預設即可)

**Environment**
建立 `.env` 並放在 repo root，至少包含：
- `SUPABASE_URL`
- `SUPABASE_KEY` 或 `SUPABASE_SERVICE_ROLE_KEY`
- `GEMINI_API_KEY`（需要 claims-only LLM 時）

**Backend**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn webapp.main:app --reload --port 8000
```

**Frontend**
```bash
cd dlcs-ui
npm install
npm run dev -- --port 5173
```

**One-shot**
```bash
./start.sh
```

**Run Pipeline A (Single URL)**
```bash
python3 scripts/run_fetcher_and_ingest.py "<threads_url>" --headless
```

**Run Preanalysis**
```bash
python3 scripts/run_preanalysis.py --post-id 410 --prefer-sot --persist-assignments
```

**Ops UI**
- Vitals: `http://localhost:5173/ops/vitals`
- Jobs: `http://localhost:5173/ops/jobs`

**Troubleshooting**
Issue: Pipeline B/C ImportError
Resolution: `webapp/services/pipeline_runner.py` 依賴 `pipelines/core.py`, `event_crawler.py`, `home_crawler.py`，這些檔案在此 repo 不存在，必須補齊或移除依賴。

Issue: Supabase RPC Missing
Resolution: Ops API 依賴 RPC：`claim_job_item`, `set_job_item_stage`, `complete_job_item`, `fail_job_item`, `bump_job_counters`, `finalize_job_if_done`，請確認 migrations 已套用。

Issue: LLM 失敗
Resolution: 檢查 `GEMINI_API_KEY`，並確認 `DL_NARRATIVE_MODE=claims_only`。

Issue: Scraper 登入
Resolution: 先跑 `python3 run_login.py` 生成 `auth_threads.json`，Threads 改版可能導致抓取不穩，需更新 parser。
