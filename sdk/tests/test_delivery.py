import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

import httpx
import pytest

from tracegrade import TraceGrade, TraceGradeError


class API:
    def __init__(self):
        self.requests = []
        self.ids = {}
        self.records = {}
        self.lock = Lock()

    def __call__(self, request):
        payload = json.loads(request.content)
        with self.lock:
            self.requests.append((request.url.path, payload))
            if request.url.path.endswith("/batch"):
                accepted = []
                for index, span in enumerate(payload["spans"]):
                    external_id = span["external_id"]
                    created = external_id not in self.ids
                    resource_id = self.ids.setdefault(external_id, str(uuid4()))
                    self.records[external_id] = span
                    accepted.append(
                        {
                            "index": index,
                            "id": resource_id,
                            "external_id": external_id,
                            "created": created,
                        }
                    )
                return httpx.Response(200, json={"accepted": accepted, "errors": []})
            external_id = payload["external_id"]
            resource_id = self.ids.setdefault(external_id, str(uuid4()))
            self.records[external_id] = payload
            return httpx.Response(201, json={"id": resource_id, "external_id": external_id})

    @property
    def batches(self):
        return [payload["spans"] for path, payload in self.requests if path.endswith("/batch")]


def make_client(handler, **kwargs):
    return TraceGrade("secret", transport=httpx.MockTransport(handler), retry_backoff=0, **kwargs)


def test_completions_batch_and_flush_at_trace_exit():
    api = API()
    with make_client(api) as client:
        with client.trace("review") as trace:
            for index in range(3):
                with trace.span(f"span-{index}") as span:
                    span.set_output({"result": index})
            assert client.pending_spans == 3
            assert api.batches == []
            assert all(
                api.records[fid]["status"] == "running"
                for fid in api.ids
                if fid != trace.external_id
            )
        assert client.pending_spans == 0
        assert len(api.batches) == 1
        assert len(api.batches[0]) == 3
        assert all(item["status"] == "ok" for item in api.batches[0])
        assert client.delivery_errors == ()
    assert api.requests[-1][1]["external_id"] == trace.external_id
    assert api.requests[-1][1]["status"] == "ok"


def test_explicit_flush_and_threshold():
    api = API()
    with make_client(api, batch_size=2) as client:
        with client.trace("review") as trace:
            for index in range(5):
                with trace.span(str(index)):
                    pass
            assert [len(batch) for batch in api.batches] == [2, 2]
            assert client.pending_spans == 1
            assert client.flush() is True
            assert client.pending_spans == 0
    assert [len(batch) for batch in api.batches] == [2, 2, 1]


def test_batches_never_mix_traces_or_exceed_100():
    api = API()
    with make_client(api) as client:
        with client.trace("first") as first, client.trace("second") as second:
            for index in range(105):
                with first.span(str(index)):
                    pass
            with second.span("other"):
                pass
            assert client.flush() is True
    assert sorted(len(batch) for batch in api.batches) == [1, 5, 100]
    paths = {first.id: first.external_id, second.id: second.external_id}
    assert all(path.split("/")[-3] in paths for path, _ in api.requests if path.endswith("/batch"))


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_status_retries_same_start_snapshot(status):
    api = API()
    attempted = []

    def handler(request):
        attempted.append(request.content)
        if len(attempted) == 1:
            return httpx.Response(status)
        return api(request)

    with make_client(handler) as client:
        with client.trace("review"):
            pass
        assert client.delivery_errors == ()
    assert attempted[0] == attempted[1]
    assert len(attempted) == 3  # Two start attempts, one completion.


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 409, 422, 501])
def test_permanent_http_failures_do_not_retry(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    with make_client(handler) as client:
        with client.trace("review"):
            pass
        assert client.delivery_errors[0].status_code == status
    assert len(requests) == 1


def test_exhausted_start_retries_are_bounded():
    calls = []

    def handler(request):
        calls.append(request.content)
        raise httpx.ConnectError("unsafe secret", request=request)

    with make_client(handler, max_retries=2) as client:
        with client.trace("review"):
            pass
        assert len(client.delivery_errors) == 1
        assert "unsafe" not in str(client.delivery_errors[0])
    assert len(calls) == 3
    assert len(set(calls)) == 1


def test_local_protocol_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.LocalProtocolError("bad request", request=request)

    with make_client(handler) as client:
        with client.trace("review"):
            pass
    assert len(calls) == 1


@pytest.mark.parametrize(
    "header, expected",
    [
        ("100000", 0.25),
        ("-10", 0),
        ("not-a-delay", 0.1),
        ("Thu, 01 Jan 2099 00:00:00 GMT", 0.25),
        ("NaN", 0.1),
    ],
)
def test_retry_after_is_bounded(header, expected, monkeypatch):
    delays = []
    monkeypatch.setattr("tracegrade.client.sleep", delays.append)
    api = API()
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": header})
        return api(request)

    with TraceGrade(
        "secret", transport=httpx.MockTransport(handler), retry_backoff=0.1, max_retry_delay=0.25
    ) as client:
        with client.trace("review"):
            pass
    assert delays == ([] if expected == 0 else [expected])


def test_partial_batch_retries_only_transient_items():
    api = API()
    batches = []

    def handler(request):
        if request.url.path.endswith("/batch"):
            spans = json.loads(request.content)["spans"]
            batches.append(spans)
            if len(batches) == 1:
                first = spans[0]
                return httpx.Response(
                    200,
                    json={
                        "accepted": [
                            {
                                "index": 0,
                                "id": api.ids[first["external_id"]],
                                "external_id": first["external_id"],
                                "created": False,
                            }
                        ],
                        "errors": [
                            {"index": 1, "status_code": 503},
                            {"index": 2, "status_code": 422},
                        ],
                    },
                )
        return api(request)

    with make_client(handler) as client:
        with client.trace("review") as trace:
            for index in range(3):
                with trace.span(str(index)):
                    pass
            assert client.flush() is False
            assert client.pending_spans == 0
            assert client.delivery_errors[-1].status_code == 422
    assert [len(batch) for batch in batches] == [3, 1]
    assert batches[1][0]["external_id"] == batches[0][1]["external_id"]


def test_http_and_item_failures_share_one_attempt_budget():
    api = API()
    batches = []

    def handler(request):
        if request.url.path.endswith("/batch"):
            spans = json.loads(request.content)["spans"]
            batches.append(spans)
            if len(batches) == 1:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "accepted": [],
                    "errors": [{"index": index, "status_code": 503} for index in range(len(spans))],
                },
            )
        return api(request)

    client = make_client(handler, max_retries=2)
    with client.trace("review") as trace:
        with trace.span("span"):
            pass
        assert client.flush() is False
        assert len(batches) == 3
        assert client.pending_spans == 1
    client.close()
    assert len(batches) == 3


def test_lost_batch_acknowledgement_retries_without_duplicate_records():
    api = API()
    attempted = []

    def handler(request):
        response = api(request)
        if request.url.path.endswith("/batch"):
            attempted.append(request.content)
            if len(attempted) == 1:
                raise httpx.ReadTimeout("Lost acknowledgement", request=request)
        return response

    with make_client(handler) as client:
        with client.trace("review") as trace:
            with trace.span("span") as span:
                span.set_output({"result": "complete"})
        assert client.delivery_errors == ()
        assert client.pending_spans == 0
    assert len(api.ids) == 2
    assert api.ids[span.external_id] == span.id
    assert attempted[0] == attempted[1]


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "duplicate",
        "overlap",
        "wrong_id",
        "wrong_external",
        "bad_status",
        "boolean_index",
    ],
)
def test_invalid_batch_acknowledgements_are_not_accepted(invalid):
    api = API()
    batch_calls = []

    def handler(request):
        if request.url.path.endswith("/batch"):
            batch_calls.append(request)
            spans = json.loads(request.content)["spans"]
            item = {
                "index": 0,
                "id": api.ids[spans[0]["external_id"]],
                "external_id": spans[0]["external_id"],
                "created": False,
            }
            data = {"accepted": [item], "errors": []}
            if invalid == "missing":
                data["accepted"] = []
            elif invalid == "duplicate":
                data["accepted"].append(item.copy())
            elif invalid == "overlap":
                data["errors"] = [{"index": 0, "status_code": 422}]
            elif invalid == "wrong_id":
                item["id"] = str(uuid4())
            elif invalid == "wrong_external":
                item["external_id"] = "wrong"
            elif invalid == "bad_status":
                data = {"accepted": [], "errors": [{"index": 0, "status_code": 200}]}
            else:
                item["index"] = False
            return httpx.Response(200, json=data)
        return api(request)

    with make_client(handler) as client:
        with client.trace("review") as trace:
            with trace.span("span"):
                pass
            assert client.flush() is False
            assert client.delivery_errors[-1].code == "invalid_response"
    assert len(batch_calls) == 1


def test_queue_is_bounded_during_outage():
    api = API()

    def handler(request):
        if request.url.path.endswith("/batch"):
            return httpx.Response(503)
        return api(request)

    with make_client(handler, max_retries=0, max_pending_spans=2) as client:
        with client.trace("review") as trace:
            for index in range(5):
                with trace.span(str(index)):
                    pass
                assert client.pending_spans <= 2
            assert any(error.code == "queue_full" for error in client.delivery_errors)
        assert client.pending_spans == 2


def test_retained_completion_can_be_flushed_after_recovery():
    api = API()
    healthy = False

    def handler(request):
        if request.url.path.endswith("/batch") and not healthy:
            return httpx.Response(503)
        return api(request)

    with make_client(handler, max_retries=0) as client:
        with client.trace("review") as trace:
            with trace.span("span") as span:
                span.set_output("final")
            assert client.flush() is False
            assert client.pending_spans == 1
            healthy = True
            assert client.flush() is True
            assert client.pending_spans == 0
    assert api.records[span.external_id]["output"] == "final"


def test_client_close_flushes_once_and_closes_after_strict_failure():
    api = API()

    def handler(request):
        if request.url.path.endswith("/batch"):
            return httpx.Response(503)
        return api(request)

    client = make_client(handler, max_retries=0, raise_on_error=True)
    trace = client.trace("review")
    trace.__enter__()
    with trace.span("span"):
        pass
    with pytest.raises(TraceGradeError):
        client.close()
    assert client.closed
    client.close()
    with pytest.raises(RuntimeError):
        client.flush()


def test_strict_batch_failure_does_not_mask_application_exception():
    api = API()

    def handler(request):
        if request.url.path.endswith("/batch"):
            return httpx.Response(503)
        return api(request)

    original = RuntimeError("application failure")
    with pytest.raises(RuntimeError) as caught:
        with make_client(handler, max_retries=0, raise_on_error=True) as client:
            with client.trace("review") as trace:
                with trace.span("span"):
                    raise original
    assert caught.value is original
    assert client.closed
    assert api.records[trace.external_id]["status"] == "error"


def test_concurrent_completion_queue_has_no_loss_or_duplicates():
    api = API()
    with make_client(api, batch_size=10) as client:
        with client.trace("parallel") as trace:

            def worker(index):
                with trace.span(str(index)):
                    pass

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(worker, range(50)))
        assert client.pending_spans == 0
    completions = [span["external_id"] for batch in api.batches for span in batch]
    assert len(completions) == 50
    assert all(count == 1 for count in Counter(completions).values())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"batch_size": 101},
        {"batch_size": True},
        {"max_retries": -1},
        {"max_retries": 11},
        {"retry_backoff": -1},
        {"max_retry_delay": float("inf")},
        {"max_pending_spans": 0},
    ],
)
def test_invalid_delivery_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        TraceGrade("secret", **kwargs)


def test_automatic_flush_does_not_restart_exhausted_retry_budget():
    api = API()
    attempts = []

    def handler(request):
        if request.url.path.endswith("/batch"):
            attempts.append(json.loads(request.content)["spans"])
            return httpx.Response(503)
        return api(request)

    with make_client(handler, batch_size=2, max_retries=1) as client:
        with client.trace("review") as trace:
            for index in range(4):
                with trace.span(str(index)):
                    pass
        assert client.pending_spans == 4
    first_id = attempts[0][0]["external_id"]
    assert sum(any(item["external_id"] == first_id for item in batch) for batch in attempts) == 2
