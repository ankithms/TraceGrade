from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update
from test_trace_ingestion import ADMIN_HEADERS, SPAN, TRACE, create_credentials

from app.models import Trace


@pytest_asyncio.fixture
async def credentials(api_client):
    return await create_credentials(api_client, "Trace browser")


async def add_trace(client, headers, external_id="trace-1"):
    response = await client.post(
        "/api/v1/traces", json={**TRACE, "external_id": external_id}, headers=headers
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_empty_project_and_empty_page(api_client, credentials):
    _, _, headers = credentials
    empty = await api_client.get("/api/v1/traces", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total": 0}
    await add_trace(api_client, headers)
    page = await api_client.get("/api/v1/traces?offset=10", headers=headers)
    assert page.status_code == 200
    assert page.json() == {"items": [], "total": 1}


@pytest.mark.asyncio
async def test_pagination_has_stable_order_and_project_total(
    api_client, credentials, test_session_factory
):
    project_id, _, headers = credentials
    traces = [await add_trace(api_client, headers, str(index)) for index in range(5)]
    _, _, other_headers = await create_credentials(api_client, "Other browser")
    await add_trace(api_client, other_headers, "private")
    # Force a timestamp tie to exercise the UUID tie-breaker between pages.
    async with test_session_factory() as session:
        await session.execute(
            update(Trace)
            .where(Trace.project_id == UUID(project_id))
            .values(created_at=datetime(2026, 10, 8, tzinfo=UTC))
        )
        await session.commit()
    expected = sorted((trace["id"] for trace in traces), reverse=True)
    ids = []
    for offset in [0, 2, 4]:
        response = await api_client.get(f"/api/v1/traces?limit=2&offset={offset}", headers=headers)
        assert response.status_code == 200
        assert response.json()["total"] == 5
        assert all(item["project_id"] == project_id for item in response.json()["items"])
        assert all("spans" not in item for item in response.json()["items"])
        ids.extend(item["id"] for item in response.json()["items"])
    assert ids == expected
    repeated = await api_client.get("/api/v1/traces?limit=2&offset=2", headers=headers)
    assert [item["id"] for item in repeated.json()["items"]] == expected[2:4]


@pytest.mark.asyncio
async def test_list_orders_newest_ingestion_first(api_client, credentials, test_session_factory):
    _, _, headers = credentials
    first = await add_trace(api_client, headers, "first")
    second = await add_trace(api_client, headers, "second")
    async with test_session_factory() as session:
        for trace, day in [(first, 8), (second, 9)]:
            await session.execute(
                update(Trace)
                .where(Trace.id == UUID(trace["id"]))
                .values(created_at=datetime(2026, 10, day, tzinfo=UTC))
            )
        await session.commit()
    result = await api_client.get("/api/v1/traces", headers=headers)
    assert [item["id"] for item in result.json()["items"]] == [second["id"], first["id"]]


@pytest.mark.asyncio
async def test_detail_without_spans(api_client, credentials):
    _, _, headers = credentials
    trace = await add_trace(api_client, headers)
    detail = await api_client.get(f"/api/v1/traces/{trace['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json() == {**trace, "spans": []}


@pytest.mark.asyncio
async def test_detail_returns_nested_span_links_and_metadata(api_client, credentials):
    project_id, _, headers = credentials
    trace = await add_trace(api_client, headers)
    path = f"/api/v1/traces/{trace['id']}/spans"
    root = await api_client.post(
        path, json={**SPAN, "external_id": "root", "kind": "operation"}, headers=headers
    )
    assert root.status_code == 201
    child_payload = {
        **SPAN,
        "parent_span_id": root.json()["id"],
        "status": "error",
        "started_at": "2026-10-08T10:00:02Z",
        "ended_at": "2026-10-08T10:00:03Z",
        "input": {"prompt": "  keep whitespace  "},
        "output": ["partial"],
        "error_message": "Provider timed out",
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "latency_ms": 1000,
        "estimated_cost_usd": "0.0000012345",
        "attributes": {"attempt": 1},
    }
    child = await api_client.post(path, json=child_payload, headers=headers)
    assert child.status_code == 201
    other = await add_trace(api_client, headers, "other-trace")
    await api_client.post(f"/api/v1/traces/{other['id']}/spans", json=SPAN, headers=headers)
    detail = await api_client.get(f"/api/v1/traces/{trace['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["spans"] == [root.json(), child.json()]
    assert all(span["project_id"] == project_id for span in detail.json()["spans"])
    assert detail.json()["spans"][1]["parent_span_id"] == root.json()["id"]


@pytest.mark.asyncio
async def test_span_timestamp_ties_have_stable_order(api_client, credentials):
    _, _, headers = credentials
    trace = await add_trace(api_client, headers)
    path = f"/api/v1/traces/{trace['id']}/spans"
    spans = [
        await api_client.post(path, json={**SPAN, "external_id": str(index)}, headers=headers)
        for index in range(3)
    ]
    assert all(span.status_code == 201 for span in spans)
    # The timestamps already tie; verify read ordering independently of creation order.
    response = await api_client.get(f"/api/v1/traces/{trace['id']}", headers=headers)
    expected = sorted(span.json()["id"] for span in spans)
    assert [span["id"] for span in response.json()["spans"]] == expected


@pytest.mark.asyncio
async def test_unknown_and_cross_project_traces_are_indistinguishable(api_client, credentials):
    _, _, headers = credentials
    _, _, other_headers = await create_credentials(api_client, "Other browser")
    trace = await add_trace(api_client, other_headers)
    for trace_id in [trace["id"], str(uuid4())]:
        response = await api_client.get(f"/api/v1/traces/{trace_id}", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "trace_not_found"
        assert response.json()["error"]["message"] == "Trace not found"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "invalid", "revoked", "admin"])
async def test_reads_require_valid_project_key(api_client, credentials, case):
    project_id, key_id, headers = credentials
    trace = await add_trace(api_client, headers)
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
    for path in ["/api/v1/traces", f"/api/v1/traces/{trace['id']}"]:
        response = await api_client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc", "offset=abc"])
async def test_invalid_pagination_is_rejected(api_client, credentials, query):
    _, _, headers = credentials
    response = await api_client.get(f"/api/v1/traces?{query}", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_invalid_trace_uuid_is_rejected(api_client, credentials):
    _, _, headers = credentials
    response = await api_client.get("/api/v1/traces/not-a-uuid", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
