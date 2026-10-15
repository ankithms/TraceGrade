# TraceGrade API

The FastAPI service owns the HTTP contract, project authentication, persistence, and Temporal workflow starts.

Implemented through Phase 4, plus the first Phase 5 schema slice:

- Liveness and PostgreSQL readiness checks.
- Alembic migrations executed at container startup.
- Admin-protected project CRUD.
- Project API-key creation, safe metadata listing, and revocation.
- HMAC-SHA256 key storage with plaintext revealed only at creation.
- Project API-key authentication for trace/span ingestion.
- Idempotent trace and single-span creation/completion with project isolation and payload limits.
- Span batches of up to 100 items with indexed acceptance/errors and per-item savepoints.
- Project-scoped paginated trace lists and detail responses with span metadata and parent links.
- Trace/span schema migration with nested-span ownership constraints.
- Project-scoped dataset, test-case, and evaluator CRUD with bounded payloads and validated configuration.
- Exact-match, contains, JSON-match, and Gemini judge scoring in `tracegrade_evaluators`.
- Experiment, selected-evaluator snapshot, case-result, and score schema with ownership and idempotency constraints.
- Versioned execution/snapshot payload contracts in `tracegrade_experiments`.

See the [evaluation walkthrough](../docs/evaluation-walkthrough.md) for API examples,
scoring semantics, offline fixtures, and an optional live Gemini call. The scoring
module has no API/database dependencies; Temporal integration is Phase 5.

PostgreSQL concurrency tests are opt-in. Point `TRACEGRADE_TEST_DATABASE_URL` at
an isolated migrated PostgreSQL database, then run
`pytest tests/test_trace_ingestion_postgres.py`. The tests create and clean up
their own projects. They exercise concurrent duplicate deliveries and competing
completion snapshots through the actual HTTP endpoints, plus trace browsing, concurrent batch retries, and savepoint/rollback behavior.


Phase 5 is being delivered in separate PRs. The initial schema PR adds no
experiment HTTP routes or execution logic. The [execution contract](../docs/experiment-execution-contract.md)
records the seven-PR sequence, runner interface, source-history retention, and
state semantics. Run `pytest tests/test_experiment_schema.py tests/test_experiment_contracts.py`
for its checks. Set `TRACEGRADE_TEST_DATABASE_URL` to an isolated database
migrated to head to repeat schema checks on PostgreSQL.
