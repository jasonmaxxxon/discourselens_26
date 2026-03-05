# Documentation Index

This folder is the single source of truth for how the system works. A new engineer should be able to understand the whole system without reading code.

## Start Here
- `docs/SYSTEM_OVERVIEW.md` — What the system does, what is in scope, what is disabled, and the key invariants.
- `docs/FLOWS.md` — End-to-end pipeline flows and writebacks.

## API and Contracts
- `docs/ENDPOINTS.md` — All HTTP endpoints (JSON + HTML console).
- `docs/CONTRACTS.md` — JSON contracts for artifacts, preanalysis, analysis_json, claims, risk, and ops KPI.
- `docs/TOPIC_CONTRACT_V1.md` — Deterministic Topic Engine v1 input/output/hash/gate contracts.

## Database
- `docs/SCHEMA.md` — Current tables and columns (derived from migrations and schema export).
- `docs/MIGRATIONS.md` — Current SQL migration index (file-level).

## Build and Operations
- `docs/ARCHITECTURE.md` — Component map and boundaries.
- `docs/DEV_RUNBOOK.md` — Local setup and operator commands.

## Integration Gate Specs (CDX Week1)
- `docs/cdx-week1/01_ENDPOINT_CATALOG.md` — Endpoint-level contract deltas for Topic API skeleton.
- `docs/cdx-week1/03_ANALYSIS_OBJECT_CONTRACT.md` — Request/response payload SoT for Topic registry.
- `docs/cdx-week1/04_DATAFLOW.mmd` — Registry + gate execution flow.
- `docs/cdx-week1/06_GOLDEN_PAYLOADS.json` — Golden payload snapshots for lifecycle/edge-case assertions.
