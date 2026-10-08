# TraceGrade

TraceGrade is an AI Evaluation & Reliability Platform for tracing LLM applications, building evaluation datasets, running durable experiments, comparing versions, and detecting regressions in quality, latency, and cost.

The v1 feature contract is frozen in [`SCOPE.md`](SCOPE.md). Rejected ideas are recorded in [`BACKLOG.md`](BACKLOG.md).

Current implementation status: Phases 0–2 are complete. The next milestone is trace/span ingestion and the Python SDK.

## Repository

- `backend`: FastAPI API.
- `worker`: Temporal worker.
- `sdk`: Python SDK.
- `frontend`: React dashboard.
- `docs`: architecture, data, API, and delivery contracts.
- `infra`: local service initialization.

## Local Foundation

Requirements: Docker with Compose v2.

```bash
cp .env.example .env
docker compose up --build
```

Default local URLs:

- Dashboard: <http://localhost:3000>
- API docs: <http://localhost:8000/docs>
- API liveness: <http://localhost:8000/api/v1/health/live>
- Temporal UI: <http://localhost:8080>

If another project already uses ports `5432` or `3000`, set `POSTGRES_PORT=5433`, `FRONTEND_PORT=3001`, and `TRACEGRADE_CORS_ORIGINS=http://localhost:3001` in `.env`. The dashboard then opens at <http://localhost:3001>. Container-to-container database connections still use `postgres:5432`.

Project administration uses the `X-TraceGrade-Admin-Key` header. The development value comes from `.env`; change both the admin key and API-key hash secret before any public deployment.

Run backend tests locally with Python 3.12:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Design Documents

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/db-schema.md`](docs/db-schema.md)
- [`docs/api-contract.md`](docs/api-contract.md)
- [`docs/task-breakdown.md`](docs/task-breakdown.md)
