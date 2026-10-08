# Delivery Plan

The phases below are ordered to produce one demonstrable vertical slice at a time. Tasks outside `SCOPE.md` are not eligible for scheduling.

## Phase 0 — Scope and Design

- [x] Freeze the product contract in `SCOPE.md`.
- [x] Record rejected ideas in `BACKLOG.md`.
- [x] Define component architecture and reliability boundaries.
- [x] Plan the relational schema.
- [x] Define the v1 HTTP contract.
- [x] Break the build into acceptance-driven phases.

Acceptance: a contributor can explain what will and will not be built without making a product decision.

## Phase 1 — Runnable Foundation

- [x] Scaffold `backend`, `worker`, `sdk`, and `frontend`.
- [x] Add local PostgreSQL, Temporal, and Temporal UI services.
- [x] Add a FastAPI application with typed settings and health endpoints.
- [x] Add container definitions and environment examples.
- [x] Add a minimal dashboard shell and package scaffolds.
- [x] Add foundation tests and developer commands.

Acceptance: the infrastructure stack starts, the API reports liveness/readiness, and each product component has an explicit build boundary.

## Phase 2 — Projects and API Keys

- [x] Add Alembic and project/API-key migrations.
- [x] Implement admin-key protection.
- [x] Implement project CRUD and API-key create/list/revoke.
- [x] Hash secrets, reveal plaintext once, and test project isolation.

Acceptance: a project can be created and a project-scoped API key authenticates requests.

## Phase 3 — Tracing and Python SDK

- [x] Add trace/span models and migrations with project ownership constraints.
- [x] Implement idempotent single-trace and single-span ingestion.
- [x] Add span batch ingestion with item-level errors.
- [x] Implement trace list/detail APIs.
- [ ] Implement SDK trace/span context managers, batching, and bounded retry.
- [ ] Capture input/output, model, tokens, latency, status, and errors.

Acceptance: a sample Python call creates one visible trace with nested LLM metadata and duplicate delivery does not duplicate data.

## Phase 4 — Datasets and Evaluators

- [ ] Add dataset, test-case, and evaluator migrations/APIs.
- [ ] Implement exact-match, contains, and JSON-match evaluators.
- [ ] Implement Gemini judge with structured output validation.
- [ ] Add evaluator unit tests, including invalid configuration and provider failure.

Acceptance: a project can define cases and obtain deterministic or Gemini-backed scores from fixed fixtures.

## Phase 5 — Durable Experiments

- [ ] Add experiment/result migrations.
- [ ] Implement experiment creation and immutable snapshots.
- [ ] Implement Temporal workflow, activities, timeouts, and bounded concurrency.
- [ ] Persist idempotent progress, attempts, failures, scores, and terminal state.
- [ ] Implement cancellation and restart/retry tests.

Acceptance: a multi-case experiment survives API/worker restarts and reaches a correct, queryable terminal state.

## Phase 6 — Results, Comparison, and Regression

- [ ] Aggregate quality, latency, token, and estimated-cost metrics.
- [ ] Build result list/detail views.
- [ ] Implement completed-experiment A/B comparison.
- [ ] Persist threshold-based quality, latency, and cost regression decisions.
- [ ] Test zero baselines, partial case failures, and mismatched datasets.

Acceptance: two completed versions produce reproducible deltas and explicit regression verdicts.

## Phase 7 — Dashboard and AI Code Reviewer Demo

- [ ] Build dashboard flows for every frozen resource.
- [ ] Add loading, empty, error, and terminal experiment states.
- [ ] Instrument the AI Code Reviewer with the Python SDK.
- [ ] Create its evaluation dataset and baseline/candidate demo runs.
- [ ] Show one intentional quality or reliability regression end to end.

Acceptance: a reviewer can complete the v1 workflow through the UI and understand the demo without repository knowledge.

## Phase 8 — Public Release

- [ ] Harden production container configuration and secrets handling.
- [ ] Deploy one public API, worker, dashboard, PostgreSQL, and Temporal stack.
- [ ] Write setup, SDK, API, architecture, and demo documentation.
- [ ] Add seeded demo data or a repeatable demo script.
- [ ] Run the complete test suite and deployment smoke test.

Acceptance: a fresh reviewer can access the public demo and reproduce it locally from the documentation.
