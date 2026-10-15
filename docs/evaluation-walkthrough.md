# Phase 4: datasets and evaluators

Phase 4 adds project-scoped dataset, test-case, and evaluator CRUD plus a scoring
library for fixed outputs. Temporal execution, persisted experiment scores, and
experiment snapshots arrive in Phase 5. Dashboard management of these resources
remains in Phase 7.

## Run the API

From the repository root, update the local backend:

```bash
docker compose up -d --build backend
```

The container applies the new migration automatically. For a backend running
outside Docker, set `TRACEGRADE_DATABASE_URL` to the intended PostgreSQL database
and run `alembic upgrade head` from `backend/`.

Use a project key from the [tracing walkthrough](tracing-walkthrough.md). Set
`TRACEGRADE_API_KEY` in your shell; the API does not accept an admin key for these
resources. The examples use the default API port:

```bash
export TRACEGRADE_API_URL=http://localhost:8000/api/v1

curl --fail-with-body "$TRACEGRADE_API_URL/datasets" \
  -H "Authorization: Bearer $TRACEGRADE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Code review fixtures","description":"Fixed security review cases"}'
```

Copy the response's `id` into `DATASET_ID`, then create a case:

```bash
export DATASET_ID='<dataset UUID>'

curl --fail-with-body "$TRACEGRADE_API_URL/datasets/$DATASET_ID/cases" \
  -H "Authorization: Bearer $TRACEGRADE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Unsafe SQL","input":{"diff":"+query(user_input)"},"expected_output":{"safe":false,"finding":"SQL injection"},"metadata":{"category":"security"}}'

curl --fail-with-body "$TRACEGRADE_API_URL/evaluators" \
  -H "Authorization: Bearer $TRACEGRADE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Structured review match","type":"json_match","config":{}}'
```

Copy the evaluator's `id` into `EVALUATOR_ID`. Dataset and evaluator names must be
unique within this project. Repeating these create requests returns `409`;
use `GET /datasets` and `GET /evaluators` to find the existing resources.

`expected_output` is optional when a Gemini judge grades against a reusable rubric.
For example, this review case needs only the code or diff:

```bash
curl --fail-with-body "$TRACEGRADE_API_URL/datasets/$DATASET_ID/cases" \
  -H "Authorization: Bearer $TRACEGRADE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Rubric-only review","input":{"diff":"+query(user_input)"},"metadata":{"category":"security"}}'
```

Reference outputs remain required by `exact_match` and `contains`, and are the
comparison target for `json_match`.

## Score the stored fixture

Install the backend package locally with Python 3.12:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export EVALUATOR_ID='<evaluator UUID>'
```

This example reads the case and configuration through the authenticated API and
scores a fixed candidate output. It does not execute the application or save an
experiment result:

```python
import asyncio
import os
from dataclasses import asdict

import httpx
from tracegrade_evaluators import evaluate


async def main():
    base = os.environ["TRACEGRADE_API_URL"].rstrip("/")
    headers = {"Authorization": f"Bearer {os.environ['TRACEGRADE_API_KEY']}"}
    async with httpx.AsyncClient(headers=headers) as client:
        cases = await client.get(f"{base}/datasets/{os.environ['DATASET_ID']}/cases")
        cases.raise_for_status()
        evaluator = await client.get(f"{base}/evaluators/{os.environ['EVALUATOR_ID']}")
        evaluator.raise_for_status()
    case = cases.json()["items"][0]
    definition = evaluator.json()
    result = await evaluate(
        definition["type"], definition["config"],
        input=case["input"], expected_output=case["expected_output"],
        output='{"finding":"SQL injection","safe":false}',
    )
    print(asdict(result))


asyncio.run(main())
```

Expected: `{'score': 1.0, 'passed': True, 'rationale': None}`. Changing `safe` to
`true` produces a valid zero score.

## Run the standalone fixtures

The example also works without a database or running API:

```bash
python examples/evaluate_fixtures.py
```

It prints JSON results for exact match, a deliberately missing finding, and a
JSON match. Expected scores are **1, 0, 1**. A failed quality check is a completed
evaluation; an invalid configuration or provider failure is an evaluation error.
The script exits nonzero on evaluation errors.

To include a real Gemini judge, set `GEMINI_API_KEY` in the shell and specify a
Gemini model available to your account that supports structured output:

```bash
python examples/evaluate_fixtures.py --gemini-model "$GEMINI_MODEL"
```

This option makes one billable provider request. The default invocation makes
none. Credentials are passed to the scoring library at runtime and are never
part of stored evaluator configuration. Gemini scoring uses the official
[generateContent structured-output fields](https://ai.google.dev/api/generate-content#v1beta.GenerationConfig)
and validates the returned score and rationale locally. Model access and exact
judge scores depend on the provider; automated tests use an injected HTTP mock.

## Evaluator behavior

| Type | Config | Behavior |
| --- | --- | --- |
| `exact_match` | `case_sensitive` (default `true`), `strip_whitespace` (default `false`) | Compare two strings. Optional stripping affects leading/trailing whitespace; case-insensitive comparison uses Unicode case folding. |
| `contains` | Same as exact match | Find the expected string inside the output; an empty normalized reference is invalid. |
| `json_match` | `{}` | Compare full JSON values. Object key order does not matter; array order does. Booleans differ from numbers; `1` and `1.0` match. Strings are parsed as JSON documents, so a JSON string scalar must be quoted, e.g. `'"ok"'`. |
| `gemini_judge` | Required `model` and `rubric`; `pass_threshold` defaults to `0.5` | Grade the input and candidate output against the reusable rubric. A reference output is optional. The application computes `passed = score >= pass_threshold`. |

All deterministic scores are 0 or 1. Invalid candidate JSON produces a completed
zero score; invalid reference JSON is an input error. Duplicate object keys and
non-finite numbers in JSON text are invalid. The library accepts finite JSON
fixtures up to 64 KiB each and 64 levels of nesting.

Gemini requires a nonempty rubric (up to 8,192 characters), an explicit
`gemini-...` model ID, and a finite threshold from 0 to 1. It accepts only a
complete `STOP` response with one candidate and a strict numeric score from
0 to 1 plus a nonblank rationale of at most 4,096 characters. Missing, blocked,
truncated, malformed, or oversized responses fail evaluation. The adapter has a
30-second overall deadline, a 5-second connection timeout, and a 256-KiB response
limit. It makes no internal retries; transient network/timeouts, HTTP 408/429,
and HTTP 500/502/503/504 carry `retryable=True` for Phase 5's workflow policy.
Provider error bodies are not included in the public error message.

## Validation

```bash
ruff check .
pytest
python examples/evaluate_fixtures.py
```

PostgreSQL checks are opt-in and must target an isolated database migrated to
head, not the application database:

```bash
TRACEGRADE_TEST_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/TEST_DB \
  pytest tests/test_datasets_evaluators_postgres.py
```

The tests cover project isolation, pagination, CRUD, name conflicts, partial
updates, JSON round trips, deletion cascades, configuration rejection,
deterministic scoring, provider payloads, invalid responses, timeouts, and
retry classification. PostgreSQL tests additionally verify concurrent creates.
