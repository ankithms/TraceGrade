# TraceGrade Python SDK

A synchronous Python 3.10+ client for recording traces and nested spans. It sends
start and completion snapshots to the TraceGrade API and preserves stable
external IDs throughout an operation.

## Install

From the TraceGrade repository root:

```bash
pip install -e ./sdk
```

Create a project API key through the API and set `TRACEGRADE_API_KEY` in your
application environment. The SDK uses project Bearer keys; admin keys manage
projects and should not be used for instrumentation.

## Instrument a call

```python
import os
from decimal import Decimal

from tracegrade import TraceGrade

with TraceGrade(
    api_key=os.environ["TRACEGRADE_API_KEY"],
    base_url="http://localhost:8000/api/v1",
) as client:
    with client.trace("AI Code Reviewer", attributes={"pull_request": 123}) as trace:
        with trace.span("load diff") as operation:
            operation.set_output({"files": 3})
            with operation.span(
                "model review",
                kind="llm",
                model="example-model",
                input={"prompt": "Review this diff"},
            ) as model_call:
                # Replace this fixture with your application's existing model call.
                review = {"summary": "Review complete"}
                model_call.set_output(review)
                model_call.set_usage(
                    prompt_tokens=20,
                    completion_tokens=10,
                    estimated_cost_usd=Decimal("0.0000012345"),
                )
                model_call.set_attribute("attempt", 1)

    print("Trace ID:", trace.id)
    if client.delivery_errors:
        print("Telemetry delivery failures:", len(client.delivery_errors))
```

Read the resulting trace through `GET /api/v1/traces/{trace.id}` with the same
project key. The SDK records data from your application; it does not call an AI
provider or run evaluations.

## Context and metadata behavior

- A trace or span context starts as `running` and finishes as `ok` or `error`.
  Exceptions propagate to the application; spans also record a bounded error message.
- Spans record UTC start/end timestamps and elapsed milliseconds using a monotonic
  clock. Latency excludes the span's initial telemetry HTTP call.
- `trace.span(...)` attaches to the current span in that trace. `span.span(...)`
  creates a child of the currently active span. Parent context is restored on exit
  and isolated across threads and independently active traces.
- Names, external IDs and returned server IDs are read-only. Contexts are one-shot;
  create a new context for a new operation. The SDK generates UUID external IDs,
  or callers can supply `external_id` for their own operation identifiers.
- `set_input`, `set_output`, `set_attribute` and `set_usage` update completion
  metadata inside an active context. JSON values are copied when supplied, so
  later application mutations do not change captured data.
- `set_usage` replaces the usage snapshot and computes total tokens when both
  prompt and completion counts are provided. Costs retain up to 10 decimal places.
- Local validation follows API payload and metric bounds: 16 KiB attributes,
  64 KiB input/output, finite JSON, and non-negative 32-bit integer token counts.

## Delivery behavior

This branch sends each start/completion immediately with a finite HTTP timeout
(default 5 seconds). Batching, explicit flush and bounded retries are the next
SDK milestone.

Delivery is best effort by default. HTTP, transport and protocol failures are
available through `client.delivery_errors`, which retains the latest 100 errors
without response bodies, model payloads or credentials. An unavailable parent
prevents descendant delivery, avoiding incorrectly attached spans. Local argument
validation errors still raise `ValueError`.

Use `raise_on_error=True` to surface delivery failures as `TraceGradeError` during
setup or tests. An existing application exception takes precedence even in this
mode. Close the client with a `with` block or `client.close()`.

## Development

```bash
cd sdk
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check src tests
pytest
```

The real API round-trip test is opt-in. Set `TRACEGRADE_TEST_API_URL` to an
isolated running API's `/api/v1` URL and `TRACEGRADE_TEST_ADMIN_KEY` to its admin
key, then run `pytest tests/test_api_integration.py`. The test creates and removes
its own project, verifies nested spans and errors, and makes no AI-provider calls.
