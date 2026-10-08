import json
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from test_trace_ingestion import ADMIN_HEADERS, SPAN, TRACE, create_credentials

from app.models import Span
from app.services import ingestion


@pytest_asyncio.fixture
async def batch_context(api_client):
    project_id, key_id, headers = await create_credentials(api_client, "Batch reviewer")
    trace = await api_client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert trace.status_code == 201
    return project_id, key_id, trace.json()["id"], headers


def batch_path(trace_id):
    return f"/api/v1/traces/{trace_id}/spans/batch"


@pytest.mark.asyncio
async def test_batch_create_and_replay(api_client, batch_context, test_session_factory):
    _, _, trace_id, headers = batch_context
    snapshots = [{**SPAN, "external_id": f"span-{index}"} for index in range(3)]
    first = await api_client.post(batch_path(trace_id), json={"spans": snapshots}, headers=headers)
    assert first.status_code == 200
    assert first.json()["errors"] == []
    assert [item["index"] for item in first.json()["accepted"]] == [0, 1, 2]
    assert all(item["created"] for item in first.json()["accepted"])
    ids = [item["id"] for item in first.json()["accepted"]]
    replay = await api_client.post(batch_path(trace_id), json={"spans": snapshots}, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["errors"] == []
    assert [item["id"] for item in replay.json()["accepted"]] == ids
    assert all(not item["created"] for item in replay.json()["accepted"])
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Span)) == 3


@pytest.mark.asyncio
async def test_duplicate_ids_within_batch_share_one_record(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    result = await api_client.post(
        batch_path(trace_id), json={"spans": [SPAN, SPAN]}, headers=headers
    )
    assert result.status_code == 200
    first, second = result.json()["accepted"]
    assert first["id"] == second["id"]
    assert first["created"] is True
    assert second["created"] is False
    assert result.json()["errors"] == []


@pytest.mark.asyncio
async def test_mixed_items_commit_valid_siblings(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    snapshots = [
        SPAN,
        {**SPAN, "external_id": "bad-metric", "prompt_tokens": -1},
        {**SPAN, "external_id": "missing-parent", "parent_span_id": str(uuid4())},
        {**SPAN, "external_id": "last"},
    ]
    result = await api_client.post(batch_path(trace_id), json={"spans": snapshots}, headers=headers)
    assert result.status_code == 200
    assert [item["index"] for item in result.json()["accepted"]] == [0, 3]
    assert [
        (error["index"], error["status_code"], error["code"]) for error in result.json()["errors"]
    ] == [
        (1, 422, "validation_error"),
        (2, 404, "parent_span_not_found"),
    ]
    detail = await api_client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    assert {span["external_id"] for span in detail.json()["spans"]} == {"model-123", "last"}


@pytest.mark.asyncio
async def test_completion_and_conflict_are_item_scoped(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    final = {**SPAN, "status": "ok", "ended_at": "2026-10-08T10:00:02Z"}
    snapshots = [
        SPAN,
        final,
        {**final, "status": "error"},
        SPAN,
        {**SPAN, "external_id": "sibling"},
    ]
    result = await api_client.post(batch_path(trace_id), json={"spans": snapshots}, headers=headers)
    assert result.status_code == 200
    assert [item["index"] for item in result.json()["accepted"]] == [0, 1, 3, 4]
    assert result.json()["errors"][0]["index"] == 2
    assert result.json()["errors"][0]["code"] == "idempotency_conflict"
    detail = await api_client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    completed = next(
        span for span in detail.json()["spans"] if span["external_id"] == SPAN["external_id"]
    )
    assert completed["status"] == "ok"
    assert completed["ended_at"] == final["ended_at"]


@pytest.mark.asyncio
async def test_children_can_reference_existing_parent(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    parent = await api_client.post(
        f"/api/v1/traces/{trace_id}/spans", json={**SPAN, "kind": "operation"}, headers=headers
    )
    child = {**SPAN, "external_id": "child", "parent_span_id": parent.json()["id"]}
    result = await api_client.post(batch_path(trace_id), json={"spans": [child]}, headers=headers)
    assert result.status_code == 200
    assert result.json()["errors"] == []
    detail = await api_client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    stored = next(span for span in detail.json()["spans"] if span["external_id"] == "child")
    assert stored["parent_span_id"] == parent.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot", [None, 42, "not-a-span", [], {}, {**SPAN, "input": "x" * 65536}]
)
async def test_bad_items_do_not_reject_envelope(api_client, batch_context, snapshot):
    _, _, trace_id, headers = batch_context
    result = await api_client.post(
        batch_path(trace_id), json={"spans": [snapshot, SPAN]}, headers=headers
    )
    assert result.status_code == 200
    assert [item["index"] for item in result.json()["accepted"]] == [1]
    assert result.json()["errors"] == [
        {
            "index": 0,
            "status_code": 422,
            "code": "validation_error",
            "message": "Span validation failed",
        }
    ]


@pytest.mark.asyncio
async def test_nonfinite_item_is_rejected_without_leaking_payload(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    snapshots = [{**SPAN, "input": float("nan")}, {**SPAN, "external_id": "valid"}]
    result = await api_client.post(
        batch_path(trace_id),
        content=json.dumps({"spans": snapshots}),
        headers={**headers, "Content-Type": "application/json"},
    )
    assert result.status_code == 200
    assert result.json()["errors"][0]["code"] == "validation_error"
    assert [item["index"] for item in result.json()["accepted"]] == [1]
    assert "NaN" not in result.text


@pytest.mark.asyncio
async def test_batch_accepts_100_spans(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    snapshots = [{**SPAN, "external_id": str(index)} for index in range(100)]
    result = await api_client.post(batch_path(trace_id), json={"spans": snapshots}, headers=headers)
    assert result.status_code == 200
    assert len(result.json()["accepted"]) == 100
    assert result.json()["errors"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"spans": []},
        {"spans": [SPAN] * 101},
        {"spans": "invalid"},
        {"spans": [SPAN], "extra": True},
    ],
)
async def test_invalid_envelopes_have_no_writes(api_client, batch_context, envelope):
    _, _, trace_id, headers = batch_context
    response = await api_client.post(batch_path(trace_id), json=envelope, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    detail = await api_client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    assert detail.json()["spans"] == []


@pytest.mark.asyncio
async def test_all_invalid_items_return_only_errors(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    response = await api_client.post(
        batch_path(trace_id), json={"spans": [{}, None]}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == []
    assert [error["index"] for error in response.json()["errors"]] == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "invalid", "revoked", "admin"])
async def test_batch_requires_valid_project_key(api_client, batch_context, case):
    project_id, key_id, trace_id, headers = batch_context
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
    response = await api_client.post(batch_path(trace_id), json={"spans": [SPAN]}, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cross_project_and_unknown_trace_are_not_found(api_client, batch_context):
    _, _, trace_id, headers = batch_context
    _, _, other_headers = await create_credentials(api_client, "Other batch reviewer")
    for inaccessible_id in [trace_id, str(uuid4())]:
        response = await api_client.post(
            batch_path(inaccessible_id), json={"spans": [SPAN]}, headers=other_headers
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "trace_not_found"


@pytest.mark.asyncio
async def test_constraint_failure_rolls_back_only_one_item(api_client, batch_context, monkeypatch):
    _, _, trace_id, headers = batch_context
    original = ingestion.ingest_span

    async def inject_constraint_failure(session, project_id, trace_id, payload):
        if payload.external_id == "db-invalid":
            session.add(
                Span(
                    id=uuid4(),
                    project_id=project_id,
                    trace_id=trace_id,
                    **{**payload.model_dump(), "prompt_tokens": -1},
                )
            )
            await session.flush()
        return await original(session, project_id, trace_id, payload)

    monkeypatch.setattr(ingestion, "ingest_span", inject_constraint_failure)
    snapshots = [SPAN, {**SPAN, "external_id": "db-invalid"}, {**SPAN, "external_id": "last"}]
    response = await api_client.post(
        batch_path(trace_id), json={"spans": snapshots}, headers=headers
    )
    assert response.status_code == 200
    assert [item["index"] for item in response.json()["accepted"]] == [0, 2]
    assert response.json()["errors"][0]["code"] == "ingestion_conflict"
    detail = await api_client.get(f"/api/v1/traces/{trace_id}", headers=headers)
    assert {span["external_id"] for span in detail.json()["spans"]} == {"model-123", "last"}


@pytest.mark.asyncio
async def test_unexpected_failure_rolls_back_whole_batch(
    api_client, batch_context, test_session_factory, monkeypatch
):
    project_id, _, trace_id, headers = batch_context
    original = ingestion.ingest_span

    async def fail_second_item(session, project_id, trace_id, payload):
        if payload.external_id == "unexpected-failure":
            raise RuntimeError("Simulated processing failure")
        return await original(session, project_id, trace_id, payload)

    monkeypatch.setattr(ingestion, "ingest_span", fail_second_item)
    with pytest.raises(RuntimeError, match="Simulated processing failure"):
        await api_client.post(
            batch_path(trace_id),
            json={
                "spans": [
                    SPAN,
                    {**SPAN, "external_id": "unexpected-failure"},
                ]
            },
            headers=headers,
        )
    async with test_session_factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Span).where(Span.project_id == UUID(project_id))
            )
            == 0
        )
