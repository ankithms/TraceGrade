# TraceGrade API

The FastAPI service owns the HTTP contract, project authentication, persistence, and Temporal workflow starts.

Implemented through Phase 2, with tracing ingestion, batching, and browsing from Phase 3:

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

PostgreSQL concurrency tests are opt-in. Point `TRACEGRADE_TEST_DATABASE_URL` at
an isolated migrated PostgreSQL database, then run
`pytest tests/test_trace_ingestion_postgres.py`. The tests create and clean up
their own projects. They exercise concurrent duplicate deliveries and competing
completion snapshots through the actual HTTP endpoints, plus trace browsing, concurrent batch retries, and savepoint/rollback behavior.
