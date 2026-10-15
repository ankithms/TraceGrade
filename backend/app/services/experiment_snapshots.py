"""Atomic creation of immutable experiment inputs, without workflow execution."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.models import (
    Dataset,
    Evaluator,
    Experiment,
    ExperimentEvaluator,
    ExperimentResult,
    TestCase,
)
from app.schemas.experiment import ExperimentCreate
from app.services.source_locks import lock_source_project
from tracegrade_experiments.contracts import CaseSnapshot, DatasetSnapshot, EvaluatorSnapshot

MAX_SNAPSHOT_CASES = 1000


@dataclass(frozen=True)
class SnapshotCreation:
    experiment_id: UUID
    created: bool


def request_fingerprint(payload: ExperimentCreate) -> str:
    values = payload.model_dump(mode="json", exclude={"idempotency_key"})
    # Evaluator selection is a set; nested object insertion order is irrelevant.
    # JSON scalar types (including int vs float) remain significant.
    values["evaluator_ids"] = sorted(values["evaluator_ids"])
    encoded = json.dumps(
        values, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def create_experiment_snapshot(
    session: AsyncSession,
    project_id: UUID,
    payload: ExperimentCreate,
) -> SnapshotCreation:
    """Stage a complete run or resolve a replay; the caller commits/rolls back.

    Source writers and this service share a project lock until outer transaction
    end. A savepoint prevents partial creation if a caller catches an exception.
    No network calls or workflow starts occur while holding the lock.
    """
    if session.new or session.dirty or session.deleted:
        raise InvalidRequestError("Snapshot creation requires a session without pending mutations")
    # Revalidate and detach mutable nested values before the first await.
    payload = ExperimentCreate.model_validate(payload.model_dump(mode="json"))
    fingerprint = request_fingerprint(payload)
    await lock_source_project(session, project_id)
    existing = await session.scalar(
        select(Experiment)
        .where(
            Experiment.project_id == project_id,
            Experiment.idempotency_key == payload.idempotency_key,
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise ApiError(
                409, "idempotency_conflict", "Key belongs to a different experiment request"
            )
        # Check the request before loading live sources: they may have changed or
        # been deleted since this run was created. Never rebuild stored snapshots.
        return SnapshotCreation(existing.id, False)

    dataset = await session.scalar(
        select(Dataset)
        .where(
            Dataset.id == payload.dataset_id,
            Dataset.project_id == project_id,
        )
        .execution_options(populate_existing=True)
    )
    if dataset is None:
        raise ApiError(404, "dataset_not_found", "Dataset not found")
    cases = list(
        await session.scalars(
            select(TestCase)
            .where(
                TestCase.dataset_id == dataset.id,
            )
            .order_by(TestCase.id)
            .limit(MAX_SNAPSHOT_CASES + 1)
            .execution_options(populate_existing=True)
        )
    )
    if not cases:
        raise ApiError(422, "empty_dataset", "An experiment requires at least one test case")
    if len(cases) > MAX_SNAPSHOT_CASES:
        raise ApiError(
            422,
            "dataset_too_large",
            f"An experiment supports at most {MAX_SNAPSHOT_CASES} test cases",
        )
    evaluators = list(
        await session.scalars(
            select(Evaluator)
            .where(
                Evaluator.id.in_(payload.evaluator_ids),
                Evaluator.project_id == project_id,
            )
            .order_by(Evaluator.id)
            .execution_options(populate_existing=True)
        )
    )
    if len(evaluators) != len(payload.evaluator_ids):
        raise ApiError(404, "evaluator_not_found", "One or more evaluators were not found")

    try:
        dataset_snapshot = DatasetSnapshot(name=dataset.name, description=dataset.description)
        case_snapshots = [
            (
                case.id,
                CaseSnapshot(
                    name=case.name,
                    input=case.input,
                    expected_output=case.expected_output,
                    metadata=case.case_metadata,
                ).model_dump(mode="json"),
            )
            for case in cases
        ]
        evaluator_snapshots = [
            (
                evaluator.id,
                EvaluatorSnapshot(
                    name=evaluator.name,
                    type=evaluator.type,
                    config=evaluator.config,
                ).model_dump(mode="json"),
            )
            for evaluator in evaluators
        ]
    except ValidationError as error:
        raise ApiError(
            422, "invalid_snapshot_source", "Stored sources do not meet snapshot validation"
        ) from error

    experiment_id = uuid4()
    async with session.begin_nested():
        session.add(
            Experiment(
                id=experiment_id,
                project_id=project_id,
                dataset_id=dataset.id,
                name=payload.name,
                version_label=payload.version_label,
                idempotency_key=payload.idempotency_key,
                request_hash=fingerprint,
                workflow_id=f"experiment-{experiment_id}",
                total_cases=len(case_snapshots),
                run_config=payload.run_config.model_dump(mode="json"),
                dataset_snapshot=dataset_snapshot.model_dump(mode="json"),
            )
        )
        await session.flush()
        session.add_all(
            [
                ExperimentResult(
                    experiment_id=experiment_id,
                    project_id=project_id,
                    test_case_id=case_id,
                    case_snapshot=snapshot,
                )
                for case_id, snapshot in case_snapshots
            ]
        )
        session.add_all(
            [
                ExperimentEvaluator(
                    experiment_id=experiment_id,
                    project_id=project_id,
                    evaluator_id=evaluator_id,
                    config_snapshot=snapshot,
                )
                for evaluator_id, snapshot in evaluator_snapshots
            ]
        )
        await session.flush()
    return SnapshotCreation(experiment_id, True)
