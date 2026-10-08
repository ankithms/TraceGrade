# TraceGrade API

The FastAPI service owns the HTTP contract, project authentication, persistence, and Temporal workflow starts.

Implemented through Phase 2:

- Liveness and PostgreSQL readiness checks.
- Alembic migrations executed at container startup.
- Admin-protected project CRUD.
- Project API-key creation, safe metadata listing, and revocation.
- HMAC-SHA256 key storage with plaintext revealed only at creation.
- Project API-key authentication dependency for the tracing endpoints added in Phase 3.
