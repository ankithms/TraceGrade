"""Run schema invariants on SQLite and, optionally, a migrated PostgreSQL DB."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    CaseStatus,
    Dataset,
    EvaluationScore,
    Evaluator,
    Experiment,
    ExperimentEvaluator,
    ExperimentResult,
    ExperimentStatus,
    Project,
    ScoreStatus,
    TestCase,
)
from tracegrade_experiments.contracts import (
    CaseSnapshot,
    DatasetSnapshot,
    EvaluatorSnapshot,
    RunConfig,
)


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def experiment_sessions(request, test_session_factory):
    if request.param == "sqlite":
        yield test_session_factory
        return
    url = os.getenv("TRACEGRADE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TRACEGRADE_TEST_DATABASE_URL to a PostgreSQL database migrated to head")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield async_sessionmaker(
                    connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


def experiment(project_id, **overrides):
    values = dict(
        id=uuid4(),
        project_id=project_id,
        dataset_id=uuid4(),
        name="Review run",
        version_label="v1",
        idempotency_key="request-1",
        request_hash="a" * 64,
        workflow_id=f"experiment-{uuid4()}",
        run_config=RunConfig(target="reviewer").model_dump(),
        dataset_snapshot=DatasetSnapshot(name="Security cases").model_dump(),
        total_cases=1,
    )
    return Experiment(**(values | overrides))


def case_result(run, **overrides):
    values = dict(
        id=uuid4(),
        experiment_id=run.id,
        project_id=run.project_id,
        test_case_id=uuid4(),
        case_snapshot=CaseSnapshot(name="SQL injection", input={"diff": "éλ"}).model_dump(),
    )
    return ExperimentResult(**(values | overrides))


def evaluator_snapshot(run, **overrides):
    values = dict(
        experiment_id=run.id,
        project_id=run.project_id,
        evaluator_id=uuid4(),
        config_snapshot=EvaluatorSnapshot(name="Exact", type="exact_match").model_dump(mode="json"),
    )
    return ExperimentEvaluator(**(values | overrides))


def score(result, evaluator, **overrides):
    values = dict(
        experiment_result_id=result.id,
        experiment_id=result.experiment_id,
        evaluator_id=evaluator.evaluator_id,
        status=ScoreStatus.COMPLETED,
        score=Decimal("0.8"),
        passed=True,
    )
    return EvaluationScore(**(values | overrides))


async def seed(session):
    first = Project(name="First", slug=f"first-{uuid4().hex}")
    other = Project(name="Other", slug=f"other-{uuid4().hex}")
    session.add_all([first, other])
    await session.flush()
    run = experiment(first.id)
    other_run = experiment(other.id)
    session.add_all([run, other_run])
    await session.flush()
    result = case_result(run)
    evaluator = evaluator_snapshot(run)
    other_evaluator = evaluator_snapshot(other_run)
    session.add_all([result, evaluator, other_evaluator])
    await session.commit()
    return run, other_run, result, evaluator, other_evaluator


async def test_round_trip_frozen_inputs_metrics_and_null_output(experiment_sessions):
    async with experiment_sessions() as session:
        run, _, result, evaluator, _ = await seed(session)
        assert run.status == ExperimentStatus.PENDING
        assert (run.completed_cases, run.failed_cases, run.cancelled_cases) == (0, 0, 0)
        assert result.status == CaseStatus.PENDING and result.attempt_count == 0
        assert result.output_recorded_at is None
        result.output = None
        result.output_recorded_at = datetime.now(UTC)
        result.prompt_tokens, result.completion_tokens, result.total_tokens = 20, 5, 25
        result.latency_ms = 123
        result.estimated_cost_usd = Decimal("0.0000012345")
        result.status = CaseStatus.COMPLETED
        result.attempt_count = 2
        saved_score = score(result, evaluator)
        session.add(saved_score)
        await session.commit()
        result_id, score_id, run_id = result.id, saved_score.id, run.id
        session.expire_all()
        stored = await session.get(ExperimentResult, result_id)
        assert stored.output is None and stored.output_recorded_at is not None
        assert stored.case_snapshot["input"] == {"diff": "éλ"}
        assert stored.estimated_cost_usd == Decimal("0.0000012345")
        assert stored.total_tokens == 25 and stored.attempt_count == 2
        assert stored.status == CaseStatus.COMPLETED
        assert (await session.get(EvaluationScore, score_id)).score == Decimal("0.8")
        assert (await session.get(Experiment, run_id)).run_config["schema_version"] == 1


@pytest.mark.parametrize(
    "violation", ["idempotency_key", "workflow_id", "case", "evaluator", "score"]
)
async def test_unique_constraints(experiment_sessions, violation):
    async with experiment_sessions() as session:
        run, other, result, evaluator, _ = await seed(session)
        if violation == "idempotency_key":
            row = experiment(run.project_id)
        elif violation == "workflow_id":
            row = experiment(other.project_id, idempotency_key="new", workflow_id=run.workflow_id)
        elif violation == "case":
            row = case_result(run, test_case_id=result.test_case_id)
        elif violation == "evaluator":
            row = evaluator_snapshot(run, evaluator_id=evaluator.evaluator_id)
            session.expunge(evaluator)
        else:
            session.add(score(result, evaluator))
            await session.commit()
            row = score(result, evaluator)
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize(
    "violation", ["case_project", "evaluator_project", "result_experiment", "unselected_evaluator"]
)
async def test_cross_project_and_experiment_references_rejected(experiment_sessions, violation):
    async with experiment_sessions() as session:
        run, other, result, evaluator, other_evaluator = await seed(session)
        if violation == "case_project":
            row = case_result(run, project_id=other.project_id)
        elif violation == "evaluator_project":
            row = evaluator_snapshot(run, project_id=other.project_id)
        elif violation == "result_experiment":
            row = score(result, other_evaluator, experiment_id=other.id)
        else:
            row = score(result, other_evaluator)
        session.add(row)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_evaluator_from_another_run_in_same_project_is_rejected(experiment_sessions):
    async with experiment_sessions() as session:
        run, _, result, _, _ = await seed(session)
        sibling = experiment(run.project_id, idempotency_key="sibling")
        session.add(sibling)
        await session.flush()
        evaluator = evaluator_snapshot(sibling)
        session.add(evaluator)
        await session.commit()
        session.add(score(result, evaluator))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize(
    "fields",
    [
        {"total_cases": 0},
        {"completed_cases": -1},
        {"failed_cases": -1},
        {"cancelled_cases": -1},
        {"completed_cases": 1, "failed_cases": 1},
        {"cancelled_cases": 2},
    ],
)
async def test_invalid_progress_rejected(experiment_sessions, fields):
    async with experiment_sessions() as session:
        run, _, _, _, _ = await seed(session)
        for key, value in fields.items():
            setattr(run, key, value)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize(
    "metric",
    [
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
        "estimated_cost_usd",
        "attempt_count",
    ],
)
async def test_negative_case_metrics_rejected(experiment_sessions, metric):
    async with experiment_sessions() as session:
        _, _, result, _, _ = await seed(session)
        setattr(result, metric, -1)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize(
    "fields",
    [
        {"score": -0.1},
        {"score": 1.1},
        {"score": None},
        {"passed": None},
        {"error_message": "error"},
        {"attempt_count": 0},
        {"status": ScoreStatus.FAILED},
        {"status": ScoreStatus.FAILED, "score": None, "passed": None},
    ],
)
async def test_invalid_score_states_rejected(experiment_sessions, fields):
    async with experiment_sessions() as session:
        _, _, result, evaluator, _ = await seed(session)
        session.add(score(result, evaluator, **fields))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_provider_failure_and_zero_score_are_distinct(experiment_sessions):
    async with experiment_sessions() as session:
        run, _, result, evaluator, _ = await seed(session)
        other_evaluator = evaluator_snapshot(run)
        session.add(other_evaluator)
        await session.flush()
        session.add_all(
            [
                score(result, evaluator, score=Decimal("0"), passed=False),
                score(
                    result,
                    other_evaluator,
                    status=ScoreStatus.FAILED,
                    score=None,
                    passed=None,
                    error_message="Gemini timed out",
                    attempt_count=3,
                ),
            ]
        )
        await session.commit()
        rows = list(await session.scalars(select(EvaluationScore)))
        assert {row.status for row in rows} == {ScoreStatus.COMPLETED, ScoreStatus.FAILED}


@pytest.mark.parametrize("table", ["experiments", "experiment_results", "evaluation_scores"])
async def test_invalid_status_rejected_by_database(experiment_sessions, table):
    async with experiment_sessions() as session:
        _, _, result, evaluator, _ = await seed(session)
        session.add(score(result, evaluator))
        await session.commit()
        with pytest.raises(DBAPIError):
            await session.execute(text(f"UPDATE {table} SET status = 'invalid'"))
        await session.rollback()


async def test_source_deletion_preserves_terminal_run_history(experiment_sessions):
    async with experiment_sessions() as session:
        run, _, result, snapshot, _ = await seed(session)
        dataset = Dataset(id=run.dataset_id, project_id=run.project_id, name="Source")
        evaluator = Evaluator(
            id=snapshot.evaluator_id,
            project_id=run.project_id,
            name="Source",
            type="exact_match",
            config={},
        )
        session.add_all([dataset, evaluator])
        await session.flush()
        session.add(
            TestCase(id=result.test_case_id, dataset_id=dataset.id, name="Source", input="x")
        )
        run.status = ExperimentStatus.COMPLETED
        run.completed_cases = 1
        session.add(score(result, snapshot))
        await session.commit()
        run_id, result_id, dataset_id, evaluator_id = run.id, result.id, dataset.id, evaluator.id
        await session.execute(delete(Dataset).where(Dataset.id == dataset_id))
        await session.execute(delete(Evaluator).where(Evaluator.id == evaluator_id))
        await session.commit()
        session.expire_all()
        assert (await session.get(Experiment, run_id)).dataset_snapshot["name"] == "Security cases"
        assert (await session.get(ExperimentResult, result_id)).case_snapshot[
            "name"
        ] == "SQL injection"
        assert await session.scalar(select(func.count()).select_from(EvaluationScore)) == 1


async def test_project_deletion_cascades_only_its_experiments(experiment_sessions):
    async with experiment_sessions() as session:
        run, other, result, evaluator, _ = await seed(session)
        session.add(score(result, evaluator))
        await session.commit()
        other_id = other.id
        await session.execute(delete(Project).where(Project.id == run.project_id))
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Experiment)) == 1
        assert await session.get(Experiment, other_id) is not None
        assert await session.scalar(select(func.count()).select_from(ExperimentResult)) == 0
        assert await session.scalar(select(func.count()).select_from(EvaluationScore)) == 0
        assert await session.scalar(select(func.count()).select_from(ExperimentEvaluator)) == 1


async def test_cancellation_state_round_trip(experiment_sessions):
    async with experiment_sessions() as session:
        run, _, result, _, _ = await seed(session)
        run.status = ExperimentStatus.CANCELLED
        run.cancelled_cases = 1
        run.cancel_requested_at = datetime.now(UTC)
        run.completed_at = datetime.now(UTC)
        result.status = CaseStatus.CANCELLED
        result.completed_at = run.completed_at
        await session.commit()
        run_id, result_id = run.id, result.id
        session.expire_all()
        stored = await session.get(Experiment, run_id)
        assert stored.status == ExperimentStatus.CANCELLED
        assert stored.cancel_requested_at is not None and stored.cancelled_cases == 1
        assert (await session.get(ExperimentResult, result_id)).status == CaseStatus.CANCELLED


async def test_same_sources_can_be_used_by_multiple_experiments(experiment_sessions):
    async with experiment_sessions() as session:
        run, _, result, evaluator, _ = await seed(session)
        candidate = experiment(
            run.project_id, dataset_id=run.dataset_id, idempotency_key="candidate"
        )
        session.add(candidate)
        await session.flush()
        candidate_result = case_result(candidate, test_case_id=result.test_case_id)
        candidate_evaluator = evaluator_snapshot(candidate, evaluator_id=evaluator.evaluator_id)
        session.add_all([candidate_result, candidate_evaluator])
        await session.flush()
        session.add_all([score(result, evaluator), score(candidate_result, candidate_evaluator)])
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(EvaluationScore)) == 2
