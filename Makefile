.PHONY: topic-migration-smoke topic-api-contract topic-worker-smoke topic\:migration_smoke topic\:api_contract topic\:worker_smoke

TOPIC_API_BASE_URL ?= http://127.0.0.1:8000

topic-migration-smoke topic\:migration_smoke:
	PYTHONPATH=. python3 scripts/migration_smoke_topic_phase2.py

topic-api-contract topic\:api_contract:
	TOPIC_API_BASE_URL=$(TOPIC_API_BASE_URL) PYTHONPATH=. python3 scripts/verify_topic_api_contract.py

topic-worker-smoke topic\:worker_smoke:
	TOPIC_API_BASE_URL=$(TOPIC_API_BASE_URL) PYTHONPATH=. python3 scripts/verify_topic_worker_smoke.py
