from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import InvalidRequestError

from app.api.errors import ApiError
from app.core.config import get_settings
from app.core.security import generate_api_key
from app.models import (
    ApiKey,
    Dataset,
    EvaluationScore,
    Evaluator,
    Experiment,
    ExperimentEvaluator,
    ExperimentResult,
    ExperimentStatus,
    Project,
    TestCase,
)
from app.schemas.experiment import ExperimentCreate
from app.services.experiment_snapshots import create_experiment_snapshot, request_fingerprint


async def seed_sources(factory, project_id=None):
    async with factory() as session:
        if project_id is None:
            project = Project(name="Snapshot tests", slug=f"snapshot-{uuid4().hex}")
            session.add(project)
            await session.flush()
            project_id = project.id
        generated = generate_api_key(get_settings().api_key_hash_secret)
        session.add(
            ApiKey(
                project_id=project_id,
                name="Snapshots",
                prefix=generated.prefix,
                secret_hash=generated.secret_hash,
            )
        )
        dataset = Dataset(project_id=project_id, name="Security", description="Original cases")
        evaluators = [
            Evaluator(project_id=project_id, name="Exact", type="exact_match", config={}),
            Evaluator(project_id=project_id, name="Contains", type="contains", config={}),
        ]
        session.add_all([dataset, *evaluators])
        await session.flush()
        cases = [
            TestCase(
                dataset_id=dataset.id,
                name=f"Case {index}",
                input={"diff": str(index)},
                expected_output="unsafe",
                case_metadata={"category": ["security"]},
            )
            for index in range(2)
        ]
        session.add_all(cases)
        await session.commit()
        return {
            "project": project_id,
            "dataset": dataset.id,
            "cases": [case.id for case in cases],
            "evaluators": [evaluator.id for evaluator in evaluators],
            "headers": {"Authorization": f"Bearer {generated.plaintext}"},
        }


@pytest_asyncio.fixture
async def sources(test_session_factory):
    return await seed_sources(test_session_factory)


def creation_request(sources, **changes):
    values = dict(
        dataset_id=sources["dataset"],
        name="Security baseline",
        version_label="v1",
        idempotency_key="snapshot-1",
        evaluator_ids=sources["evaluators"],
        run_config={"target": "reviewer", "parameters": {"style": "security"}},
    )
    return ExperimentCreate.model_validate(values | changes)


async def create_run(factory, sources, payload=None):
    async with factory() as session:
        result = await create_experiment_snapshot(
            session, sources["project"], payload or creation_request(sources)
        )
        await session.commit()
        return result


async def test_creates_complete_pending_run(test_session_factory, sources):
    result = await create_run(test_session_factory, sources)
    assert result.created
    async with test_session_factory() as session:
        run = await session.get(Experiment, result.experiment_id)
        assert run.status == ExperimentStatus.PENDING
        assert run.workflow_id == f"experiment-{run.id}"
        assert run.request_hash == request_fingerprint(creation_request(sources))
        assert run.total_cases == 2
        assert run.completed_cases == run.failed_cases == run.cancelled_cases == 0
        assert run.dataset_snapshot == {
            "schema_version": 1,
            "name": "Security",
            "description": "Original cases",
        }
        assert run.run_config["max_concurrency"] == 5
        cases = list(await session.scalars(select(ExperimentResult)))
        assert {case.test_case_id for case in cases} == set(sources["cases"])
        assert all(case.case_snapshot["metadata"] == {"category": ["security"]} for case in cases)
        evaluators = list(await session.scalars(select(ExperimentEvaluator)))
        assert {row.evaluator_id for row in evaluators} == set(sources["evaluators"])
        assert all(
            row.config_snapshot["config"] == {"case_sensitive": True, "strip_whitespace": False}
            for row in evaluators
        )
        assert await session.scalar(select(func.count()).select_from(EvaluationScore)) == 0


async def test_canonical_replay_uses_defaults_and_evaluator_set_order(
    test_session_factory, sources
):
    original = creation_request(
        sources, run_config={"target": "reviewer", "parameters": {"a": 1, "b": 2}}
    )
    created = await create_run(test_session_factory, sources, original)
    equivalent = creation_request(
        sources,
        name=" Security baseline ",
        evaluator_ids=list(reversed(sources["evaluators"])),
        run_config={
            "target": "reviewer",
            "schema_version": 1,
            "parameters": {"b": 2, "a": 1},
            "max_concurrency": 5,
            "application_timeout_seconds": 60,
        },
    )
    replay = await create_run(test_session_factory, sources, equivalent)
    assert request_fingerprint(equivalent) == request_fingerprint(original)
    assert not replay.created and replay.experiment_id == created.experiment_id


@pytest.mark.parametrize(
    "change",
    ["name", "version", "dataset", "evaluators", "target", "parameter", "concurrency", "timeout"],
)
async def test_conflicting_replay_returns_409_without_writes(test_session_factory, sources, change):
    await create_run(test_session_factory, sources)
    values = creation_request(sources).model_dump(mode="json")
    if change == "name":
        values["name"] = "Candidate"
    elif change == "version":
        values["version_label"] = "v2"
    elif change == "dataset":
        values["dataset_id"] = str(uuid4())
    elif change == "evaluators":
        values["evaluator_ids"] = values["evaluator_ids"][:1]
    elif change == "target":
        values["run_config"]["target"] = "other"
    elif change == "parameter":
        values["run_config"]["parameters"] = {"style": "general"}
    elif change == "concurrency":
        values["run_config"]["max_concurrency"] = 2
    else:
        values["run_config"]["application_timeout_seconds"] = 30
    with pytest.raises(ApiError) as error:
        await create_run(test_session_factory, sources, ExperimentCreate.model_validate(values))
    assert error.value.status_code == 409 and error.value.code == "idempotency_conflict"
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 1
        assert await session.scalar(select(func.count()).select_from(ExperimentResult)) == 2


async def test_snapshots_remain_frozen_after_live_edits_and_request_mutation(
    test_session_factory, sources, api_client
):
    payload = creation_request(sources)
    created = await create_run(test_session_factory, sources, payload)
    payload.run_config.parameters["style"] = "mutated"
    headers = sources["headers"]
    base = f"/api/v1/datasets/{sources['dataset']}"
    assert (
        await api_client.patch(base, headers=headers, json={"name": "Renamed"})
    ).status_code == 200
    assert (
        await api_client.patch(
            f"{base}/cases/{sources['cases'][0]}",
            headers=headers,
            json={"input": "changed", "metadata": {"category": ["other"]}},
        )
    ).status_code == 200
    assert (
        await api_client.post(f"{base}/cases", headers=headers, json={"name": "New", "input": None})
    ).status_code == 201
    assert (
        await api_client.patch(
            f"/api/v1/evaluators/{sources['evaluators'][0]}",
            headers=headers,
            json={"config": {"case_sensitive": False}},
        )
    ).status_code == 200
    replay = await create_run(test_session_factory, sources)
    assert not replay.created and replay.experiment_id == created.experiment_id
    async with test_session_factory() as session:
        run = await session.get(Experiment, created.experiment_id)
        assert run.dataset_snapshot["name"] == "Security"
        assert run.run_config["parameters"] == {"style": "security"} and run.total_cases == 2
        rows = list(await session.scalars(select(ExperimentResult)))
        assert len(rows) == 2 and all(isinstance(row.case_snapshot["input"], dict) for row in rows)
        assert all(row.case_snapshot["metadata"] == {"category": ["security"]} for row in rows)
        rows = list(await session.scalars(select(ExperimentEvaluator)))
        assert all(row.config_snapshot["config"]["case_sensitive"] for row in rows)


@pytest.mark.parametrize("resource", ["dataset", "evaluator", "project"])
@pytest.mark.parametrize("unknown", [False, True])
async def test_ownership_and_unknown_ids_are_not_found(
    test_session_factory, sources, resource, unknown
):
    other = await seed_sources(test_session_factory)
    payload = creation_request(sources)
    project_id = sources["project"]
    if resource == "dataset":
        payload.dataset_id = uuid4() if unknown else other["dataset"]
    elif resource == "evaluator":
        payload.evaluator_ids = [uuid4() if unknown else other["evaluators"][0]]
    else:
        project_id = uuid4()
    async with test_session_factory() as session:
        with pytest.raises(ApiError) as error:
            await create_experiment_snapshot(session, project_id, payload)
        assert error.value.status_code == 404
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 0


async def test_same_key_is_independent_per_project(test_session_factory, sources):
    other = await seed_sources(test_session_factory)
    first = await create_run(test_session_factory, sources)
    second = await create_run(test_session_factory, other)
    assert first.created and second.created and first.experiment_id != second.experiment_id


async def test_failed_write_rolls_back_savepoint_even_if_caller_commits(
    test_session_factory, sources, monkeypatch
):
    async with test_session_factory() as session:
        original_flush = session.flush
        calls = 0

        async def fail_second_flush(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Injected persistence failure")
            return await original_flush(*args, **kwargs)

        monkeypatch.setattr(session, "flush", fail_second_flush)
        with pytest.raises(RuntimeError, match="Injected"):
            await create_experiment_snapshot(session, sources["project"], creation_request(sources))
        monkeypatch.setattr(session, "flush", original_flush)
        await session.commit()
        for model in (Experiment, ExperimentResult, ExperimentEvaluator):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert (await create_run(test_session_factory, sources)).created


async def test_outer_rollback_removes_whole_run(test_session_factory, sources):
    async with test_session_factory() as session:
        created = await create_experiment_snapshot(
            session, sources["project"], creation_request(sources)
        )
        await session.rollback()
    async with test_session_factory() as session:
        assert await session.get(Experiment, created.experiment_id) is None
    assert (await create_run(test_session_factory, sources)).created


@pytest.mark.parametrize("invalid", ["empty", "oversized", "corrupt_case", "corrupt_evaluator"])
async def test_invalid_sources_create_no_partial_run(
    test_session_factory, sources, monkeypatch, invalid
):
    from app.services import experiment_snapshots

    async with test_session_factory() as session:
        if invalid == "empty":
            for case_id in sources["cases"]:
                await session.delete(await session.get(TestCase, case_id))
        elif invalid == "oversized":
            monkeypatch.setattr(experiment_snapshots, "MAX_SNAPSHOT_CASES", 1)
        elif invalid == "corrupt_case":
            case = await session.get(TestCase, sources["cases"][0])
            case.input = "x" * 65536
        else:
            evaluator = await session.get(Evaluator, sources["evaluators"][0])
            evaluator.config = {"unknown": "private payload"}
        await session.commit()
    with pytest.raises(ApiError) as error:
        await create_run(test_session_factory, sources)
    assert error.value.status_code == 422 and "private payload" not in error.value.message
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 0


@pytest.mark.parametrize("status", [ExperimentStatus.PENDING, ExperimentStatus.RUNNING])
async def test_active_deletion_guards_preserve_sources(
    test_session_factory, sources, api_client, status
):
    created = await create_run(test_session_factory, sources)
    async with test_session_factory() as session:
        run = await session.get(Experiment, created.experiment_id)
        run.status = status
        await session.commit()
    for path, code in [
        (f"/api/v1/datasets/{sources['dataset']}", "dataset_in_use"),
        (f"/api/v1/datasets/{sources['dataset']}/cases/{sources['cases'][0]}", "dataset_in_use"),
        (f"/api/v1/evaluators/{sources['evaluators'][0]}", "evaluator_in_use"),
    ]:
        response = await api_client.delete(path, headers=sources["headers"])
        assert response.status_code == 409 and response.json()["error"]["code"] == code
        assert (await api_client.get(path, headers=sources["headers"])).status_code == 200
    other = await api_client.post(
        "/api/v1/evaluators",
        headers=sources["headers"],
        json={"name": "Unused", "type": "json_match"},
    )
    assert (
        await api_client.delete(
            f"/api/v1/evaluators/{other.json()['id']}", headers=sources["headers"]
        )
    ).status_code == 204


@pytest.mark.parametrize(
    "status", [ExperimentStatus.COMPLETED, ExperimentStatus.FAILED, ExperimentStatus.CANCELLED]
)
async def test_terminal_source_deletion_retains_replay(
    test_session_factory, sources, api_client, status
):
    created = await create_run(test_session_factory, sources)
    async with test_session_factory() as session:
        run = await session.get(Experiment, created.experiment_id)
        run.status = status
        await session.commit()
    base = f"/api/v1/datasets/{sources['dataset']}"
    assert (
        await api_client.delete(f"{base}/cases/{sources['cases'][0]}", headers=sources["headers"])
    ).status_code == 204
    assert (await api_client.delete(base, headers=sources["headers"])).status_code == 204
    for evaluator_id in sources["evaluators"]:
        assert (
            await api_client.delete(
                f"/api/v1/evaluators/{evaluator_id}", headers=sources["headers"]
            )
        ).status_code == 204
    replay = await create_run(test_session_factory, sources)
    assert not replay.created and replay.experiment_id == created.experiment_id
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ExperimentResult)) == 2
        assert await session.scalar(select(func.count()).select_from(ExperimentEvaluator)) == 2


async def test_reject_session_with_pending_unrelated_mutation(test_session_factory, sources):
    async with test_session_factory() as session:
        session.add(Dataset(project_id=sources["project"], name="Unrelated"))
        with pytest.raises(InvalidRequestError):
            await create_experiment_snapshot(session, sources["project"], creation_request(sources))
        await session.rollback()


@pytest.mark.parametrize(
    "changes",
    [
        {"evaluator_ids": []},
        {"evaluator_ids": [uuid4()] * 2},
        {"evaluator_ids": [uuid4() for _ in range(101)]},
        {"name": " "},
        {"idempotency_key": ""},
        {"idempotency_key": "a b"},
        {"idempotency_key": "a" * 256},
        {"run_config": {"target": "../run"}},
        {"project_id": str(uuid4())},
    ],
)
def test_creation_request_validation(changes):
    sources = {"dataset": uuid4(), "evaluators": [uuid4()]}
    with pytest.raises(ValidationError):
        creation_request(sources, **changes)


async def test_reference_free_gemini_sources_are_snapshotted_without_provider_calls(
    test_session_factory, sources
):
    async with test_session_factory() as session:
        evaluator = await session.get(Evaluator, sources["evaluators"][0])
        evaluator.type = "gemini_judge"
        evaluator.config = {
            "model": "gemini-test",
            "rubric": "Assess correctness from input and output",
        }
        for case_id in sources["cases"]:
            case = await session.get(TestCase, case_id)
            case.expected_output = None
        await session.commit()
    result = await create_run(
        test_session_factory,
        sources,
        creation_request(sources, evaluator_ids=sources["evaluators"][:1]),
    )
    async with test_session_factory() as session:
        evaluator = await session.get(
            ExperimentEvaluator, (result.experiment_id, sources["evaluators"][0])
        )
        assert evaluator.config_snapshot["type"] == "gemini_judge"
        assert evaluator.config_snapshot["config"]["pass_threshold"] == 0.5
        cases = list(await session.scalars(select(ExperimentResult)))
        assert all(case.case_snapshot["expected_output"] is None for case in cases)
