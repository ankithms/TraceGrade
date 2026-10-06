# TraceGrade

TraceGrade is an AI Evaluation & Reliability Platform for tracing LLM applications, building evaluation datasets, running durable experiments, comparing versions, and detecting regressions in quality, latency, and cost.

The v1 feature contract is frozen in [`SCOPE.md`](SCOPE.md). Rejected ideas are recorded in [`BACKLOG.md`](BACKLOG.md).

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
