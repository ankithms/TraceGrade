"""Opt-in concurrency checks against an isolated, migrated PostgreSQL database.

Set TRACEGRADE_TEST_DATABASE_URL to enable these checks. Each test removes only
its own project and dependent data; it does not recreate the database schema.
"""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import generate_api_key
from app.db.session import get_session
from app.main import app
from app.models import ApiKey, Project, Span, Trace

TRACE = {
    "external_id": "concurrent-trace",
    "name": "review",
    "status": "running",
    "started_at": "2026-10-08T10:00:00Z",
}
SPAN = {
    "external_id": "concurrent-span",
    "name": "model",
    "kind": "llm",
    "status": "running",
    "started_at": "2026-10-08T10:00:01Z",
}


@pytest_asyncio.fixture
async def postgres_client():
    url = os.getenv("TRACEGRADE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TRACEGRADE_TEST_DATABASE_URL to a migrated PostgreSQL test database")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project_id = uuid4()
    generated = generate_api_key(get_settings().api_key_hash_secret)
    async with factory() as session:
        session.add(
            Project(id=project_id, name="Concurrency test", slug=f"concurrency-{uuid4().hex}")
        )
        await session.flush()
        session.add(
            ApiKey(
                project_id=project_id,
                name="Test",
                prefix=generated.prefix,
                secret_hash=generated.secret_hash,
            )
        )
        await session.commit()

    async def override_session():
        async with factory() as session:
            yield session

    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, {"Authorization": f"Bearer {generated.plaintext}"}, factory, project_id
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous
        async with factory() as session:
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["trace", "span"])
async def test_concurrent_duplicate_deliveries(postgres_client, resource):
    client, headers, factory, project_id = postgres_client
    path, payload, model = "/api/v1/traces", TRACE, Trace
    if resource == "span":
        trace = await client.post(path, json=TRACE, headers=headers)
        assert trace.status_code == 201
        path, payload, model = f"/api/v1/traces/{trace.json()['id']}/spans", SPAN, Span
    results = await asyncio.gather(
        *[client.post(path, json=payload, headers=headers) for _ in range(10)]
    )
    assert [r.status_code for r in results].count(201) == 1
    assert all(r.status_code in (200, 201) for r in results)
    ids = {r.json()["id"] for r in results}
    assert len(ids) == 1
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(model).where(model.project_id == project_id)
            )
            == 1
        )
    final = {**payload, "status": "ok", "ended_at": "2026-10-08T10:00:02Z"}
    completed = await asyncio.gather(
        *[client.post(path, json=final, headers=headers) for _ in range(10)]
    )
    assert all(r.status_code == 200 for r in completed)
    replay = await client.post(path, json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["status"] == "ok"
    assert replay.json()["id"] in ids


@pytest.mark.asyncio
async def test_concurrent_conflicting_completions(postgres_client):
    client, headers, factory, _ = postgres_client
    trace = await client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert trace.status_code == 201
    snapshots = [
        {**TRACE, "status": status, "ended_at": "2026-10-08T10:00:02Z"}
        for status in ["ok", "error"]
    ]
    results = await asyncio.gather(
        *[client.post("/api/v1/traces", json=snapshot, headers=headers) for snapshot in snapshots]
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    async with factory() as session:
        record = await session.get(Trace, UUID(trace.json()["id"]))
        assert record.status == winner["status"]


@pytest.mark.asyncio
async def test_trace_browsing_round_trip(postgres_client):
    client, headers, _, project_id = postgres_client
    trace = await client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert trace.status_code == 201
    span_payload = {
        **SPAN,
        "status": "ok",
        "started_at": "2026-10-08T15:30:01+05:30",
        "ended_at": "2026-10-08T10:00:02Z",
        "input": {"prompt": "review"},
        "output": ["complete"],
        "estimated_cost_usd": "0.0000012345",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    span = await client.post(
        f"/api/v1/traces/{trace.json()['id']}/spans", json=span_payload, headers=headers
    )
    assert span.status_code == 201
    listing = await client.get("/api/v1/traces?limit=1", headers=headers)
    assert listing.status_code == 200
    assert listing.json() == {"items": [trace.json()], "total": 1}
    detail = await client.get(f"/api/v1/traces/{trace.json()['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json() == {**trace.json(), "spans": [span.json()]}
    assert detail.json()["project_id"] == str(project_id)
    assert detail.json()["spans"][0]["started_at"] == "2026-10-08T10:00:01Z"
    assert detail.json()["spans"][0]["estimated_cost_usd"] == "0.0000012345"


@pytest.mark.asyncio
async def test_concurrent_batch_retries(postgres_client):
    client, headers, factory, project_id = postgres_client
    trace = await client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert trace.status_code == 201
    path = f"/api/v1/traces/{trace.json()['id']}/spans/batch"
    snapshots = [{**SPAN, "external_id": f"batch-{index}"} for index in range(5)]
    responses = await asyncio.gather(
        *[client.post(path, json={"spans": snapshots}, headers=headers) for _ in range(10)]
    )
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["errors"] == [] for response in responses)
    accepted = [item for response in responses for item in response.json()["accepted"]]
    assert len({item["id"] for item in accepted}) == 5
    assert sum(item["created"] for item in accepted) == 5
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Span).where(Span.project_id == project_id)
            )
            == 5
        )


@pytest.mark.asyncio
async def test_batch_savepoints_recover_from_database_errors(postgres_client, monkeypatch):
    from app.services import ingestion

    client, headers, factory, project_id = postgres_client
    trace = await client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert trace.status_code == 201
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
    snapshots = [
        SPAN,
        {**SPAN, "external_id": "db-invalid"},
        {**SPAN, "external_id": "bad-input", "prompt_tokens": -1},
        {**SPAN, "external_id": "last"},
    ]
    response = await client.post(
        f"/api/v1/traces/{trace.json()['id']}/spans/batch",
        json={"spans": snapshots},
        headers=headers,
    )
    assert response.status_code == 200
    assert [item["index"] for item in response.json()["accepted"]] == [0, 3]
    assert [(error["index"], error["code"]) for error in response.json()["errors"]] == [
        (1, "ingestion_conflict"),
        (2, "validation_error"),
    ]
    async with factory() as session:
        assert set(
            await session.scalars(select(Span.external_id).where(Span.project_id == project_id))
        ) == {SPAN["external_id"], "last"}


@pytest.mark.asyncio
async def test_fatal_batch_failure_rolls_back_accepted_items(postgres_client, monkeypatch):
    from app.services import ingestion

    client, headers, factory, project_id = postgres_client
    trace = await client.post("/api/v1/traces", json=TRACE, headers=headers)
    assert trace.status_code == 201
    original = ingestion.ingest_span

    async def fail_second_item(session, project_id, trace_id, payload):
        if payload.external_id == "fatal":
            raise RuntimeError("Simulated fatal failure")
        return await original(session, project_id, trace_id, payload)

    monkeypatch.setattr(ingestion, "ingest_span", fail_second_item)
    with pytest.raises(RuntimeError, match="Simulated fatal failure"):
        await client.post(
            f"/api/v1/traces/{trace.json()['id']}/spans/batch",
            json={"spans": [SPAN, {**SPAN, "external_id": "fatal"}]},
            headers=headers,
        )
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Span).where(Span.project_id == project_id)
            )
            == 0
        )
