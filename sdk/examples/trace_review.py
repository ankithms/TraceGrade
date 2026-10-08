"""Run from the repository root after installing the SDK: python sdk/examples/trace_review.py."""

import argparse
import os
from decimal import Decimal

import httpx

from tracegrade import TraceGrade, TraceGradeError

TRACE_FIELDS = ("external_id", "name", "status", "started_at", "ended_at", "attributes")
SPAN_FIELDS = TRACE_FIELDS + (
    "parent_span_id",
    "kind",
    "input",
    "output",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "latency_ms",
    "estimated_cost_usd",
    "error_message",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def record_review(client):
    with client.trace("AI Code Reviewer example", attributes={"example": "phase-3"}) as trace:
        with trace.span("review pipeline") as pipeline:
            with pipeline.span(
                "model review",
                kind="llm",
                model="fixture-model",
                input={"diff": "- return total\n+ return total / count"},
            ) as model:
                # Replace these fixtures with your application's model call and usage.
                model.set_output({"summary": "Guard against division by zero."})
                model.set_usage(
                    prompt_tokens=20,
                    completion_tokens=10,
                    estimated_cost_usd=Decimal("0.0000012345"),
                )
            try:
                with pipeline.span("publish review"):
                    raise RuntimeError("Fixture: review publishing unavailable")
            except RuntimeError:
                # A handled child failure leaves the enclosing operation successful.
                pipeline.set_output({"review": "ready", "published": False})
    require(client.flush(), "Some span completions remain unacknowledged")
    require(not client.delivery_errors, "Telemetry delivery failed")
    return trace.id


def verify_review(api, trace_id):
    response = api.get(f"traces/{trace_id}")
    response.raise_for_status()
    detail = response.json()
    require(detail["status"] == "ok", "Expected a successful trace")
    spans = {span["name"]: span for span in detail["spans"]}
    require(len(detail["spans"]) == 3 and len(spans) == 3, "Expected exactly three spans")
    pipeline = spans["review pipeline"]
    model = spans["model review"]
    failed = spans["publish review"]
    require(pipeline["parent_span_id"] is None, "Expected a root pipeline span")
    require(pipeline["status"] == model["status"] == "ok", "Expected successful review spans")
    require(
        model["parent_span_id"] == failed["parent_span_id"] == pipeline["id"],
        "Expected nested model and publishing spans",
    )
    require(
        model["kind"] == "llm" and model["model"] == "fixture-model",
        "Model metadata was not persisted",
    )
    require(
        model["input"] == {"diff": "- return total\n+ return total / count"},
        "Model input was not persisted",
    )
    require(
        model["output"] == {"summary": "Guard against division by zero."},
        "Model output was not persisted",
    )
    require(
        (model["prompt_tokens"], model["completion_tokens"], model["total_tokens"]) == (20, 10, 30),
        "Token usage was not persisted",
    )
    require(
        Decimal(model["estimated_cost_usd"]) == Decimal("0.0000012345"),
        "Cost precision was not preserved",
    )
    require(
        all(
            span["latency_ms"] is not None
            and span["latency_ms"] >= 0
            and span["ended_at"] is not None
            for span in detail["spans"]
        ),
        "Timing metadata was not persisted",
    )
    require(
        failed["status"] == "error"
        and failed["error_message"] == "Fixture: review publishing unavailable",
        "Handled failure was not persisted",
    )
    return detail


def verify_replay(api, detail):
    # Replay exact completed snapshots, retaining external IDs and start timestamps.
    response = api.post("traces", json={field: detail[field] for field in TRACE_FIELDS})
    response.raise_for_status()
    require(
        response.status_code == 200 and response.json()["id"] == detail["id"],
        "Trace replay did not retain the original record",
    )
    # Send the root first: parent links must already exist in the receiving trace.
    spans = sorted(detail["spans"], key=lambda span: span["parent_span_id"] is not None)
    response = api.post(
        f"traces/{detail['id']}/spans/batch",
        json={"spans": [{field: span[field] for field in SPAN_FIELDS} for span in spans]},
    )
    response.raise_for_status()
    result = response.json()
    require(
        not result["errors"] and len(result["accepted"]) == len(spans),
        "Span replay was not fully accepted",
    )
    require(
        {item["index"] for item in result["accepted"]} == set(range(len(spans))),
        "Span replay returned unexpected indexes",
    )
    for item in result["accepted"]:
        require(
            item["id"] == spans[item["index"]]["id"] and item["created"] is False,
            "Span replay created a different record",
        )
    after = verify_review(api, detail["id"])
    require(
        {span["id"] for span in after["spans"]} == {span["id"] for span in spans},
        "Replay changed the stored span records",
    )


def main():
    parser = argparse.ArgumentParser(description="Record and verify a fixture review trace.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("TRACEGRADE_API_URL", "http://localhost:8000/api/v1"),
        help="API URL including /api/v1 (default: TRACEGRADE_API_URL or localhost:8000/api/v1)",
    )
    args = parser.parse_args()
    key = os.getenv("TRACEGRADE_API_KEY")
    if not key:
        parser.error("Set TRACEGRADE_API_KEY to a project API key")
    try:
        with TraceGrade(key, base_url=args.base_url, raise_on_error=True) as client:
            trace_id = record_review(client)
        with httpx.Client(
            base_url=args.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {key}"},
            timeout=5,
            trust_env=False,
            follow_redirects=False,
        ) as api:
            detail = verify_review(api, trace_id)
            verify_replay(api, detail)
    except (httpx.HTTPError, TraceGradeError, RuntimeError, ValueError) as error:
        # Avoid printing HTTP objects, credentials or server response bodies.
        parser.exit(1, f"Example failed ({type(error).__name__}). Check API access and logs.\n")
    print(f"Trace ID: {trace_id}")
    print("Verified: 3 spans, nested LLM metadata, 30 tokens, cost, timing and handled error.")
    print("Replay verified: same trace ID and span IDs; no duplicate records.")
    print(f"Inspect: GET {args.base_url.rstrip('/')}/traces/{trace_id}")


if __name__ == "__main__":
    main()
