.PHONY: topic-migration-smoke topic-api-contract topic\:migration_smoke topic\:api_contract

topic-migration-smoke topic\:migration_smoke:
	PYTHONPATH=. python3 scripts/migration_smoke_topic_phase2.py

topic-api-contract topic\:api_contract:
	PYTHONPATH=. python3 scripts/verify_topic_api_contract.py
