# TraceGrade Python SDK

A synchronous Python 3.10+ client for recording traces and nested spans. It sends
start snapshots immediately and batches completed span snapshots, preserving
stable external IDs throughout an operation.

## Install

From the TraceGrade repository root:

```bash
pip install -e ./sdk
```

Create a project API key through the API and set `TRACEGRADE_API_KEY` in your
application environment. The SDK uses project Bearer keys; admin keys manage
projects and should not be used for instrumentation.

## Instrument a call

For a runnable example with persisted metadata checks and duplicate-delivery
verification, follow the [tracing walkthrough](../docs/tracing-walkthrough.md)
and run `python sdk/examples/trace_review.py` from the repository root.

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

Trace/span starts are sent immediately so parent server IDs are available before
children start. Completed span snapshots are queued and batched per trace. They
flush at the configured threshold, at trace completion, on `client.flush()`, and
when closing the client. No batch contains more than 100 spans or mixes traces.
Set `batch_size=1` to send completion snapshots through the single-span endpoint.

| Option | Default | Purpose |
| --- | --- | --- |
| `timeout` | `5.0` | Finite HTTP timeout in seconds. |
| `batch_size` | `100` | Completion batch size, from 1 to 100. |
| `max_pending_spans` | `1000` | Bound on queued completion snapshots. |
| `max_retries` | `2` | Extra attempts, from 0 to 10. |
| `retry_backoff` | `0.1` | Initial exponential retry delay in seconds. |
| `max_retry_delay` | `2.0` | Cap on backoff and `Retry-After` delays. |

Retries handle network/timeouts, remote protocol interruptions and HTTP
408/429/500/502/503/504. Authentication, validation, identity conflicts, redirects
and malformed success responses are not retried. Snapshot IDs and final metadata
remain unchanged across retries, including when a response is lost after commit.

Batch responses are validated before acknowledgement. Accepted items are removed,
permanent item failures are recorded and discarded, and only transient failed
items are retried. Whole-request and item retries share one attempt budget.

Delivery is best effort by default. HTTP, transport and protocol failures are
available through `client.delivery_errors`, which retains the latest 100 errors
without response bodies, model payloads or credentials. An unavailable parent
prevents descendant delivery. Local argument validation errors raise `ValueError`.

After transient retries are exhausted, completions remain queued. Automatic
flushes do not restart their retry budget. Call `client.flush()` explicitly while
the client is open to attempt recovery with a fresh bounded budget:

```python
if not client.flush():
    print("Pending span completions:", client.pending_spans)
```

`flush()` returns true when all selected updates are acknowledged and false when
any delivery fails. A full queue records `queue_full` and drops the new completion
rather than growing indefinitely. The queue is in memory; closing the client ends
delivery and does not persist unresolved telemetry. There is no background thread
or timer.

Use `raise_on_error=True` to surface new delivery failures as `TraceGradeError`.
Existing application exceptions take precedence, including during automatic
flush and client close. Closing is idempotent and closes the transport even when
a strict-mode flush fails. Use a `with` block or `client.close()`.

## Development

```bash
cd sdk
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check src tests examples
pytest
```

The real API round-trip test is opt-in. Set `TRACEGRADE_TEST_API_URL` to an
isolated running API's `/api/v1` URL and `TRACEGRADE_TEST_ADMIN_KEY` to its admin
key, then run `pytest tests/test_api_integration.py`. The test creates and removes
its own projects, verifies nested spans/errors and retries after simulated lost
acknowledgements, and makes no AI-provider calls.
