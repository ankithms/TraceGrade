import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Lock
from uuid import UUID, uuid4

import httpx
import pytest

from tracegrade import Span, Trace, TraceGrade, TraceGradeError


class Recorder:
    def __init__(self):
        self.requests = []
        self.ids = {}
        self.lock = Lock()

    def __call__(self, request):
        payload = json.loads(request.content)
        with self.lock:
            self.requests.append((request, payload))
            resource_id = self.ids.setdefault(payload["external_id"], str(uuid4()))
        return httpx.Response(201, json={"id": resource_id, "external_id": payload["external_id"]})


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def client(recorder):
    with TraceGrade("tg_test_secret", transport=httpx.MockTransport(recorder)) as client:
        yield client


def test_nested_spans_and_metadata(client, recorder, monkeypatch):
    clocks = iter([10.0, 10.125, 10.25, 10.5, 11.0, 11.25])
    monkeypatch.setattr("tracegrade.tracing.perf_counter", lambda: next(clocks))
    attributes = {"version": "v1", "nested": {"unchanged": True}}
    with client.trace("review", attributes=attributes) as trace:
        assert isinstance(trace, Trace)
        UUID(trace.id)
        attributes["nested"]["unchanged"] = False
        trace.set_attribute("pull_request", 123)
        with trace.span("fetch") as parent:
            assert isinstance(parent, Span)
            with parent.span(
                "model", kind="llm", model="example", input={"prompt": "  keep  "}
            ) as child:
                child.set_output({"summary": "ok"})
                child.set_usage(
                    prompt_tokens=20,
                    completion_tokens=10,
                    estimated_cost_usd=Decimal("0.0000012345"),
                )
                child.set_attribute("attempt", 1)
        with trace.span("sibling") as sibling:
            sibling.set_input(["new input"])
    assert trace.status == "ok"
    trace_requests = [
        payload for request, payload in recorder.requests if request.url.path.endswith("/traces")
    ]
    spans = [
        payload for request, payload in recorder.requests if request.url.path.endswith("/spans")
    ]
    assert trace_requests[0]["status"] == "running"
    assert trace_requests[-1]["status"] == "ok"
    assert trace_requests[-1]["attributes"] == {
        "version": "v1",
        "nested": {"unchanged": True},
        "pull_request": 123,
    }
    child_final = next(item for item in spans if item["name"] == "model" and item["status"] == "ok")
    assert child_final["parent_span_id"] == parent.id
    assert child_final["input"] == {"prompt": "  keep  "}
    assert child_final["output"] == {"summary": "ok"}
    assert child_final["prompt_tokens"] == 20
    assert child_final["completion_tokens"] == 10
    assert child_final["total_tokens"] == 30
    assert child_final["latency_ms"] == 125
    assert child_final["estimated_cost_usd"] == "0.0000012345"
    sibling_final = next(
        item for item in spans if item["name"] == "sibling" and item["status"] == "ok"
    )
    assert sibling_final["parent_span_id"] is None
    assert sibling_final["input"] == ["new input"]
    for request, payload in recorder.requests:
        assert request.headers["authorization"] == "Bearer tg_test_secret"
        assert request.url.path.startswith("/api/v1/traces")
        UUID(payload["external_id"])
        assert payload["started_at"].endswith("+00:00")
    assert client.delivery_errors == ()


def test_application_exception_is_captured_and_propagated(client, recorder):
    original = RuntimeError("provider failure")
    with pytest.raises(RuntimeError) as caught:
        with client.trace("review"):
            with client.trace("separate") as trace:
                with trace.span("model", kind="llm"):
                    raise original
    assert caught.value is original
    final_spans = [
        payload
        for _, payload in recorder.requests
        if payload.get("kind") and payload["status"] == "error"
    ]
    assert len(final_spans) == 1
    assert final_spans[0]["error_message"] == "provider failure"
    assert all(payload["status"] == "error" for _, payload in recorder.requests[-3:])


def test_caught_span_failure_does_not_fail_trace(client, recorder):
    with client.trace("review") as trace:
        with pytest.raises(ValueError):
            with trace.span("failed"):
                raise ValueError("caught")
        with trace.span("next"):
            pass
    assert trace.status == "ok"
    next_start = next(payload for _, payload in recorder.requests if payload["name"] == "next")
    assert next_start["parent_span_id"] is None


def test_parent_contexts_do_not_leak_between_threads(client, recorder):
    barrier = Barrier(2)
    with client.trace("parallel") as trace:

        def worker(index):
            with trace.span(f"parent-{index}") as parent:
                barrier.wait(timeout=5)
                with trace.span(f"child-{index}"):
                    pass
                return parent.id

        with ThreadPoolExecutor(max_workers=2) as pool:
            parents = list(pool.map(worker, [0, 1]))
    for index in [0, 1]:
        start = next(
            payload for _, payload in recorder.requests if payload["name"] == f"child-{index}"
        )
        assert start["parent_span_id"] == parents[index]
    assert all(
        payload["parent_span_id"] is None
        for _, payload in recorder.requests
        if payload["name"].startswith("parent-")
    )


def test_failed_parent_delivery_skips_descendants():
    requests = []

    def failure(request):
        requests.append(request)
        return httpx.Response(503)

    with TraceGrade("secret", transport=httpx.MockTransport(failure)) as client:
        with client.trace("review") as trace:
            with trace.span("parent") as parent:
                with parent.span("child"):
                    pass
        assert trace.id is None
        assert len(client.delivery_errors) == 1
        assert client.delivery_errors[0].status_code == 503
    assert len(requests) == 1


def test_unavailable_span_does_not_create_orphan_child(recorder):
    def failure(request):
        payload = json.loads(request.content)
        if payload["name"] == "parent":
            return httpx.Response(503)
        return recorder(request)

    with TraceGrade("secret", transport=httpx.MockTransport(failure)) as client:
        with client.trace("review") as trace:
            with trace.span("parent") as parent:
                with parent.span("child"):
                    pass
            with trace.span("sibling") as sibling:
                pass
        assert parent.id is None
        assert sibling.id is not None
        assert len(client.delivery_errors) == 1
    assert all(payload["name"] != "child" for _, payload in recorder.requests)


@pytest.mark.parametrize("status", [301, 401, 422, 429, 500])
def test_http_failures_are_safe_and_not_retried(status):
    requests = []

    def failure(request):
        requests.append(request)
        return httpx.Response(status, json={"message": "secret prompt"})

    with TraceGrade("secret-key", transport=httpx.MockTransport(failure)) as client:
        with client.trace("review"):
            pass
        assert client.delivery_errors[0].status_code == status
        assert "secret" not in str(client.delivery_errors[0])
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"id": 42}, {"id": "invalid"}, {"id": str(uuid4()), "external_id": "wrong"}],
)
def test_invalid_responses_are_delivery_errors(payload):
    with TraceGrade(
        "secret", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        with client.trace("review"):
            pass
        assert client.delivery_errors[0].code == "invalid_response"


def test_transport_failure_is_safe():
    def failure(request):
        raise httpx.ConnectError("secret key in unsafe exception", request=request)

    with TraceGrade("secret", transport=httpx.MockTransport(failure)) as client:
        with client.trace("review"):
            pass
        assert client.delivery_errors[0].code == "transport_error"
        assert "secret" not in str(client.delivery_errors[0])


@pytest.mark.parametrize("application_failure", [False, True])
def test_strict_delivery_preserves_application_exceptions(recorder, application_failure):
    def failure(request):
        payload = json.loads(request.content)
        if payload["status"] != "running":
            return httpx.Response(503)
        return recorder(request)

    original = RuntimeError("application failure")
    expected = (
        pytest.raises(RuntimeError) if application_failure else pytest.raises(TraceGradeError)
    )
    with TraceGrade(
        "secret", transport=httpx.MockTransport(failure), raise_on_error=True
    ) as client:
        with expected as caught:
            with client.trace("review"):
                if application_failure:
                    raise original
        if application_failure:
            assert caught.value is original
        assert len(client.delivery_errors) == 1


def test_contexts_are_one_shot_and_identity_is_read_only(client):
    trace = client.trace("review", external_id="stable")
    with trace:
        span = trace.span("model")
        with span:
            for field in ["name", "external_id", "id", "status"]:
                with pytest.raises(AttributeError):
                    setattr(span, field, "changed")
        with pytest.raises(RuntimeError):
            with span:
                pass
    with pytest.raises(RuntimeError):
        with trace:
            pass
    with pytest.raises(RuntimeError):
        trace.span("outside")
    with pytest.raises(RuntimeError):
        span.set_output("outside")


def test_close_is_idempotent_and_prevents_new_traces(client):
    client.close()
    client.close()
    assert client.closed
    with pytest.raises(RuntimeError):
        client.trace("closed")


@pytest.mark.parametrize("tokens", [-1, True, 0.5, 2**31])
def test_invalid_token_counts_are_rejected(client, tokens):
    with client.trace("review") as trace:
        with trace.span("model") as span:
            with pytest.raises(ValueError):
                span.set_usage(prompt_tokens=tokens, completion_tokens=1)


@pytest.mark.parametrize("cost", ["-1", "NaN", "Infinity", "0.00000000001", "10000000000"])
def test_invalid_cost_is_rejected(client, cost):
    with client.trace("review") as trace:
        with trace.span("model") as span:
            with pytest.raises(ValueError):
                span.set_usage(estimated_cost_usd=cost)


@pytest.mark.parametrize("value", [float("nan"), object(), "x" * 65536])
def test_invalid_payloads_are_rejected(client, value):
    with client.trace("review") as trace:
        with trace.span("model") as span:
            with pytest.raises(ValueError):
                span.set_output(value)


def test_error_history_is_bounded():
    with TraceGrade(
        "secret", transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        for _ in range(105):
            with client.trace("review"):
                pass
        assert len(client.delivery_errors) == 100


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": ""},
        {"api_key": "bad\nkey"},
        {"base_url": "file:///tmp/test"},
        {"base_url": "https://user:password@example.com/api/v1"},
        {"base_url": "https://example.com?secret=x"},
        {"timeout": 0},
        {"timeout": float("inf")},
    ],
)
def test_invalid_client_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        TraceGrade(**{"api_key": "secret", **kwargs})


def test_high_precision_cost_cannot_be_silently_rounded(client):
    with client.trace("review") as trace:
        with trace.span("model") as span:
            with pytest.raises(ValueError):
                span.set_usage(estimated_cost_usd="1.0000000000000000000000000000000000000001")


def test_error_message_is_bounded_and_original_exception_survives(client, recorder):
    original = RuntimeError("x" * 6000)
    with pytest.raises(RuntimeError) as caught:
        with client.trace("review") as trace:
            with trace.span("failed"):
                raise original
    assert caught.value is original
    final = next(
        payload
        for _, payload in recorder.requests
        if payload.get("kind") and payload["status"] == "error"
    )
    assert len(final["error_message"]) == 4096
