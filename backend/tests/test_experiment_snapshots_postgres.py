"""Real-transaction snapshot races against an isolated migrated PostgreSQL DB."""

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from test_experiment_snapshots import create_run, creation_request, seed_sources
from test_trace_ingestion_postgres import postgres_client  # noqa: F401

from app.api.errors import ApiError
from app.models import (
    Dataset,
    Evaluator,
    Experiment,
    ExperimentEvaluator,
    ExperimentResult,
    TestCase,
)
from app.services.experiment_snapshots import create_experiment_snapshot
from app.services.source_locks import lock_source_project


@pytest_asyncio.fixture
async def pg_sources(postgres_client):  # noqa: F811
    client, _, factory, project_id = postgres_client
    sources = await seed_sources(factory, project_id)
    return client, factory, sources


async def assert_blocked(task):
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.1)


async def test_concurrent_replays_create_one_complete_run(pg_sources):
    _, factory, sources = pg_sources
    async with asyncio.timeout(10):
        results = await asyncio.gather(*[create_run(factory, sources) for _ in range(8)])
    assert sum(result.created for result in results) == 1
    assert len({result.experiment_id for result in results}) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 1
        assert await session.scalar(select(func.count()).select_from(ExperimentResult)) == 2
        assert await session.scalar(select(func.count()).select_from(ExperimentEvaluator)) == 2


async def test_concurrent_conflicting_requests_have_one_winner(pg_sources):
    _, factory, sources = pg_sources
    async with asyncio.timeout(10):
        results = await asyncio.gather(
            *[
                create_run(factory, sources, creation_request(sources, name=name))
                for name in ("A", "B")
            ],
            return_exceptions=True,
        )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, ApiError)]
    assert len(successes) == len(failures) == 1
    assert successes[0].created and failures[0].code == "idempotency_conflict"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 1


async def test_snapshot_waits_for_source_writer_and_copies_coherent_data(pg_sources):
    _, factory, sources = pg_sources
    async with factory() as writer:
        await lock_source_project(writer, sources["project"])
        dataset = await writer.get(Dataset, sources["dataset"])
        case = await writer.get(TestCase, sources["cases"][0])
        evaluator = await writer.get(Evaluator, sources["evaluators"][0])
        dataset.name, case.input, evaluator.config = (
            "Updated",
            {"diff": "new"},
            {"case_sensitive": False},
        )
        await writer.flush()
        task = asyncio.create_task(create_run(factory, sources))
        try:
            await assert_blocked(task)
            await writer.commit()
            result = await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with factory() as session:
        run = await session.get(Experiment, result.experiment_id)
        assert run.dataset_snapshot["name"] == "Updated"
        case = await session.scalar(
            select(ExperimentResult).where(ExperimentResult.test_case_id == sources["cases"][0])
        )
        assert case.case_snapshot["input"] == {"diff": "new"}
        evaluator = await session.get(ExperimentEvaluator, (run.id, sources["evaluators"][0]))
        assert evaluator.config_snapshot["config"]["case_sensitive"] is False


@pytest.mark.parametrize(
    "operation",
    [
        "rename_dataset",
        "add_case",
        "update_case",
        "delete_case",
        "update_evaluator",
        "delete_evaluator",
        "delete_dataset",
    ],
)
async def test_source_routes_wait_for_snapshot_commit(pg_sources, operation):
    client, factory, sources = pg_sources
    base = f"/api/v1/datasets/{sources['dataset']}"
    operations = {
        "rename_dataset": ("PATCH", base, {"name": "Renamed"}, 200),
        "add_case": ("POST", f"{base}/cases", {"name": "New", "input": "new"}, 201),
        "update_case": ("PATCH", f"{base}/cases/{sources['cases'][0]}", {"input": "changed"}, 200),
        "delete_case": ("DELETE", f"{base}/cases/{sources['cases'][0]}", None, 409),
        "update_evaluator": (
            "PATCH",
            f"/api/v1/evaluators/{sources['evaluators'][0]}",
            {"config": {"case_sensitive": False}},
            200,
        ),
        "delete_evaluator": ("DELETE", f"/api/v1/evaluators/{sources['evaluators'][0]}", None, 409),
        "delete_dataset": ("DELETE", base, None, 409),
    }
    method, path, payload, expected = operations[operation]
    async with factory() as creator:
        result = await create_experiment_snapshot(
            creator, sources["project"], creation_request(sources)
        )
        task = asyncio.create_task(
            client.request(method, path, json=payload, headers=sources["headers"])
        )
        try:
            await assert_blocked(task)
            await creator.commit()
            response = await asyncio.wait_for(task, timeout=5)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert response.status_code == expected, response.text
    async with factory() as session:
        run = await session.get(Experiment, result.experiment_id)
        assert run.dataset_snapshot["name"] == "Security" and run.total_cases == 2
        cases = list(await session.scalars(select(ExperimentResult)))
        assert all(isinstance(case.case_snapshot["input"], dict) for case in cases)
        evaluator = await session.get(ExperimentEvaluator, (run.id, sources["evaluators"][0]))
        assert evaluator.config_snapshot["config"]["case_sensitive"] is True


async def test_source_deletion_winning_race_prevents_creation(pg_sources):
    _, factory, sources = pg_sources
    async with factory() as deleter:
        await lock_source_project(deleter, sources["project"])
        await deleter.execute(delete(Dataset).where(Dataset.id == sources["dataset"]))
        task = asyncio.create_task(create_run(factory, sources))
        try:
            await assert_blocked(task)
            await deleter.commit()
            with pytest.raises(ApiError) as error:
                await asyncio.wait_for(task, timeout=5)
            assert error.value.code == "dataset_not_found"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 0


async def test_existing_session_identity_cache_is_refreshed(pg_sources):
    client, factory, sources = pg_sources
    async with factory() as session:
        dataset = await session.get(Dataset, sources["dataset"])
        assert dataset.name == "Security"
        response = await client.patch(
            f"/api/v1/datasets/{sources['dataset']}",
            headers=sources["headers"],
            json={"name": "Latest"},
        )
        assert response.status_code == 200
        created = await create_experiment_snapshot(
            session, sources["project"], creation_request(sources)
        )
        await session.commit()
    async with factory() as session:
        assert (await session.get(Experiment, created.experiment_id)).dataset_snapshot[
            "name"
        ] == "Latest"


async def test_database_constraint_failure_rolls_back_entire_snapshot(pg_sources, monkeypatch):
    _, factory, sources = pg_sources
    async with factory() as session:
        original_flush = session.flush
        calls = 0

        async def invalidate_child(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                for row in session.new:
                    if isinstance(row, ExperimentResult):
                        row.project_id = uuid4()
            await original_flush(*args, **kwargs)

        monkeypatch.setattr(session, "flush", invalidate_child)
        with pytest.raises(IntegrityError):
            await create_experiment_snapshot(session, sources["project"], creation_request(sources))
        monkeypatch.setattr(session, "flush", original_flush)
        await session.commit()
        for model in (Experiment, ExperimentResult, ExperimentEvaluator):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert (await create_run(factory, sources)).created
