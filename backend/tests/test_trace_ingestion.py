from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Span, Trace

ADMIN_HEADERS = {"X-TraceGrade-Admin-Key": get_settings().admin_key}
TRACE = {
    "external_id": "review-123",
    "name": "code review",
    "status": "running",
    "started_at": "2026-10-08T10:00:00Z",
    "attributes": {"version": "v1"},
}
SPAN = {
    "external_id": "model-123",
    "name": "Gemini call",
    "kind": "llm",
    "status": "running",
    "started_at": "2026-10-08T10:00:01Z",
    "input": {"prompt": "  Preserve these spaces  "},
    "model": "example-model",
}


async def create_credentials(client, name):
    project = await client.post("/api/v1/projects", json={"name": name}, headers=ADMIN_HEADERS)
    assert project.status_code == 201
    project_id = project.json()["id"]
    key = await client.post(
        f"/api/v1/projects/{project_id}/api-keys", json={"name": "test"}, headers=ADMIN_HEADERS
    )
    assert key.status_code == 201
    return project_id, key.json()["id"], {"Authorization": f"Bearer {key.json()['key']}"}


@pytest_asyncio.fixture
async def credentials(api_client):
    return await create_credentials(api_client, "Reviewer")


@pytest.mark.asyncio
async def test_trace_create_complete_and_replay(api_client, credentials, test_session_factory):
    project_id, _, headers = credentials
    first = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert first.status_code == 201
    record = first.json()
    assert record["project_id"] == project_id
    assert record["started_at"] == "2026-10-08T10:00:00Z"
    UUID(record["id"])
    final = {**TRACE, "status": "ok", "ended_at": "2026-10-08T10:00:10Z"}
    for payload in [TRACE, final, final, TRACE]:
        result = await api_client.post("/api/v1/traces", json=payload, headers=headers)
        assert result.status_code == 200
        assert result.json()["id"] == record["id"]
    assert result.json()["status"] == "ok"
    assert result.json()["ended_at"] == final["ended_at"]
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Trace)) == 1


@pytest.mark.asyncio
async def test_nested_span_lifecycle_and_metadata(api_client, credentials, test_session_factory):
    _, _, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    path = f"/api/v1/traces/{trace.json()['id']}/spans"
    parent = await api_client.post(
        path, json={**SPAN, "external_id": "root", "kind": "operation"}, headers=headers
    )
    assert parent.status_code == 201
    payload = {**SPAN, "parent_span_id": parent.json()["id"]}
    first = await api_client.post(path, json=payload, headers=headers)
    assert first.status_code == 201
    assert first.json()["input"] == SPAN["input"]
    final = {
        **payload,
        "status": "error",
        "ended_at": "2026-10-08T10:00:02Z",
        "output": None,
        "error_message": "Provider timed out",
        "prompt_tokens": 10,
        "completion_tokens": 0,
        "total_tokens": 10,
        "latency_ms": 1000,
        "estimated_cost_usd": "0.0000012345",
    }
    for snapshot in [payload, final, final, payload]:
        result = await api_client.post(path, json=snapshot, headers=headers)
        assert result.status_code == 200
        assert result.json()["id"] == first.json()["id"]
    assert result.json()["status"] == "error"
    assert result.json()["error_message"] == "Provider timed out"
    assert result.json()["estimated_cost_usd"] == "0.0000012345"
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Span)) == 2


@pytest.mark.asyncio
async def test_replay_accepts_equivalent_timezone(api_client, credentials):
    _, _, headers = credentials
    first = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    result = await api_client.post(
        "/api/v1/traces", json={**TRACE, "started_at": "2026-10-08T15:30:00+05:30"}, headers=headers
    )
    assert result.status_code == 200
    assert result.json()["id"] == first.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["trace", "span"])
async def test_project_scoping_and_duplicate_external_ids(api_client, credentials, resource):
    _, _, first_headers = credentials
    _, _, second_headers = await create_credentials(api_client, "Other reviewer")
    first = await api_client.post("/api/v1/traces", json=TRACE, headers=first_headers)
    second = await api_client.post("/api/v1/traces", json=TRACE, headers=second_headers)
    assert first.json()["id"] != second.json()["id"]
    if resource == "span":
        for trace, headers in [(first, first_headers), (second, second_headers)]:
            result = await api_client.post(
                f"/api/v1/traces/{trace.json()['id']}/spans", json=SPAN, headers=headers
            )
            assert result.status_code == 201
    denied = await api_client.post(
        f"/api/v1/traces/{first.json()['id']}/spans", json=SPAN, headers=second_headers
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "trace_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "invalid", "revoked", "admin"])
async def test_ingestion_requires_valid_project_key(api_client, credentials, case):
    project_id, key_id, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"Authorization": "Bearer invalid-key"}
    elif case == "admin":
        headers = ADMIN_HEADERS
    else:
        await api_client.delete(
            f"/api/v1/projects/{project_id}/api-keys/{key_id}", headers=ADMIN_HEADERS
        )
    for path, payload in [
        ("/api/v1/traces", TRACE),
        (f"/api/v1/traces/{trace.json()['id']}/spans", SPAN),
    ]:
        result = await api_client.post(path, json=payload, headers=headers)
        assert result.status_code == 401
        assert result.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["unknown", "other_trace", "other_project"])
async def test_parent_must_exist_in_same_trace(api_client, credentials, case):
    _, _, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    parent_id = str(uuid4())
    if case != "unknown":
        other_headers = headers
        if case == "other_project":
            _, _, other_headers = await create_credentials(api_client, "Other reviewer")
        other = await api_client.post(
            "/api/v1/traces", json={**TRACE, "external_id": "other"}, headers=other_headers
        )
        parent = await api_client.post(
            f"/api/v1/traces/{other.json()['id']}/spans", json=SPAN, headers=other_headers
        )
        parent_id = parent.json()["id"]
    result = await api_client.post(
        f"/api/v1/traces/{trace.json()['id']}/spans",
        json={**SPAN, "parent_span_id": parent_id},
        headers=headers,
    )
    assert result.status_code == 404
    assert result.json()["error"]["code"] == "parent_span_not_found"


@pytest.mark.asyncio
async def test_span_external_id_cannot_move_to_another_trace(api_client, credentials):
    _, _, headers = credentials
    traces = [
        await api_client.post(
            "/api/v1/traces", json={**TRACE, "external_id": str(index)}, headers=headers
        )
        for index in range(2)
    ]
    first = await api_client.post(
        f"/api/v1/traces/{traces[0].json()['id']}/spans", json=SPAN, headers=headers
    )
    assert first.status_code == 201
    conflict = await api_client.post(
        f"/api/v1/traces/{traces[1].json()['id']}/spans", json=SPAN, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["trace", "span"])
async def test_identity_changes_are_conflicts(api_client, credentials, resource):
    _, _, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    path, payload = "/api/v1/traces", TRACE
    if resource == "span":
        path, payload = f"/api/v1/traces/{trace.json()['id']}/spans", SPAN
        await api_client.post(path, json=payload, headers=headers)
    for override in [{"name": "different"}, {"started_at": "2026-10-08T09:00:00Z"}]:
        result = await api_client.post(path, json={**payload, **override}, headers=headers)
        assert result.status_code == 409
    retry = await api_client.post(path, json=payload, headers=headers)
    assert retry.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["trace", "span"])
async def test_completed_record_is_immutable(api_client, credentials, resource):
    _, _, headers = credentials
    path, payload = "/api/v1/traces", TRACE
    if resource == "span":
        trace = await api_client.post(path, json=payload, headers=headers)
        path, payload = f"/api/v1/traces/{trace.json()['id']}/spans", SPAN
    final = {**payload, "status": "ok", "ended_at": "2026-10-08T10:00:02Z"}
    first = await api_client.post(path, json=final, headers=headers)
    for override in [{"status": "error"}, {"attributes": {"tampered": True}}]:
        conflict = await api_client.post(path, json={**final, **override}, headers=headers)
        assert conflict.status_code == 409
    replay = await api_client.post(path, json=final, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == first.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"external_id": " "},
        {"external_id": "x" * 256},
        {"name": "x" * 121},
        {"status": "invalid"},
        {"started_at": "2026-10-08T10:00:00"},
        {"ended_at": "2026-10-08T09:00:00Z"},
        {"project_id": str(uuid4())},
        {"attributes": {"large": "x" * (16 * 1024)}},
        {"attributes": {"unicode": "😀" * 4096}},
    ],
)
async def test_trace_validation(api_client, credentials, override):
    _, _, headers = credentials
    response = await api_client.post("/api/v1/traces", json={**TRACE, **override}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"kind": "invalid"},
        {"prompt_tokens": -1},
        {"completion_tokens": 2**31},
        {"total_tokens": True},
        {"latency_ms": 0.5},
        {"estimated_cost_usd": "-0.01"},
        {"estimated_cost_usd": "0.00000000001"},
        {"estimated_cost_usd": "10000000000"},
        {"error_message": "x" * 4097},
        {"parent_span_id": "invalid"},
        {"input": "x" * (64 * 1024)},
        {"output": "😀" * (16 * 1024)},
    ],
)
async def test_span_validation(api_client, credentials, override):
    _, _, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    response = await api_client.post(
        f"/api/v1/traces/{trace.json()['id']}/spans", json={**SPAN, **override}, headers=headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_missing_trace_returns_not_found(api_client, credentials):
    _, _, headers = credentials
    result = await api_client.post(f"/api/v1/traces/{uuid4()}/spans", json=SPAN, headers=headers)
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_running_snapshot_updates_payload(api_client, credentials):
    _, _, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    path = f"/api/v1/traces/{trace.json()['id']}/spans"
    first = await api_client.post(path, json=SPAN, headers=headers)
    updated = {**SPAN, "output": ["partial"], "attributes": {"step": 2}, "prompt_tokens": 20}
    result = await api_client.post(path, json=updated, headers=headers)
    assert result.status_code == 200
    assert result.json()["id"] == first.json()["id"]
    assert result.json()["output"] == ["partial"]
    assert result.json()["attributes"] == {"step": 2}
    assert result.json()["prompt_tokens"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["kind", "parent_span_id"])
async def test_span_kind_and_parent_are_immutable(api_client, credentials, field):
    _, _, headers = credentials
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    path = f"/api/v1/traces/{trace.json()['id']}/spans"
    parent = await api_client.post(
        path, json={**SPAN, "external_id": "root", "kind": "operation"}, headers=headers
    )
    payload = {**SPAN, "parent_span_id": parent.json()["id"]}
    first = await api_client.post(path, json=payload, headers=headers)
    assert first.status_code == 201
    override = {field: "operation" if field == "kind" else None}
    conflict = await api_client.post(path, json={**payload, **override}, headers=headers)
    assert conflict.status_code == 409
    retry = await api_client.post(path, json=payload, headers=headers)
    assert retry.status_code == 200
    assert retry.json()["parent_span_id"] == parent.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["trace", "span"])
async def test_nonfinite_json_numbers_are_rejected(api_client, credentials, resource):
    import json

    _, _, headers = credentials
    path, payload = "/api/v1/traces", TRACE
    if resource == "span":
        trace = await api_client.post(path, json=payload, headers=headers)
        path, payload = f"/api/v1/traces/{trace.json()['id']}/spans", SPAN
        payload = {**payload, "input": float("nan")}
    else:
        payload = {**payload, "attributes": {"invalid": float("inf")}}
    response = await api_client.post(
        path, content=json.dumps(payload), headers={**headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 422
