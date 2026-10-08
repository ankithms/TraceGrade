from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExecutionStatus, Project, Span, SpanKind, Trace


def make_trace(project_id, external_id="trace-1") -> Trace:
    return Trace(
        id=uuid4(),
        project_id=project_id,
        external_id=external_id,
        name="review",
        status=ExecutionStatus.RUNNING,
        started_at=datetime.now(UTC),
    )


def make_span(trace: Trace, **overrides) -> Span:
    fields = {
        "id": uuid4(),
        "project_id": trace.project_id,
        "trace_id": trace.id,
        "external_id": "span-1",
        "name": "model call",
        "kind": SpanKind.LLM,
        "status": ExecutionStatus.OK,
        "started_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Span(**fields)


async def seed(session: AsyncSession) -> tuple[Project, Project, Trace, Trace, Trace]:
    first = Project(name="First", slug="first")
    second = Project(name="Second", slug="second")
    session.add_all([first, second])
    await session.flush()
    first_trace = make_trace(first.id)
    other_trace = make_trace(first.id, "trace-2")
    second_trace = make_trace(second.id)
    session.add_all([first_trace, other_trace, second_trace])
    await session.commit()
    return first, second, first_trace, other_trace, second_trace


@pytest.mark.asyncio
async def test_nested_spans_round_trip_metadata(test_session_factory):
    async with test_session_factory() as session:
        _, _, trace, _, _ = await seed(session)
        root = make_span(trace, external_id="root", kind=SpanKind.OPERATION)
        session.add(root)
        await session.flush()
        child = make_span(
            trace,
            parent_span_id=root.id,
            input={"prompt": "review this"},
            output=["looks good"],
            model="example-model",
            prompt_tokens=20,
            completion_tokens=5,
            total_tokens=25,
            latency_ms=100,
            estimated_cost_usd=Decimal("0.0000012345"),
            attributes={"version": "v1"},
        )
        session.add(child)
        await session.commit()
        child_id, root_id, trace_id = child.id, root.id, trace.id
        session.expire_all()
        stored = await session.get(Span, child_id)
        assert stored.parent_span_id == root_id
        assert stored.input == {"prompt": "review this"}
        assert stored.output == ["looks good"]
        assert stored.estimated_cost_usd == Decimal("0.0000012345")
        assert stored.status == ExecutionStatus.OK
        assert stored.kind == SpanKind.LLM
        assert stored.attributes == {"version": "v1"}
        assert stored.created_at is not None
        stored_trace = await session.get(Trace, trace_id)
        assert stored_trace.attributes == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["trace", "span"])
async def test_external_ids_are_unique_within_project(test_session_factory, resource):
    async with test_session_factory() as session:
        _, _, first, other, second = await seed(session)
        if resource == "span":
            session.add_all([make_span(first), make_span(second)])
            await session.commit()
            duplicate = make_span(other)
        else:
            duplicate = make_trace(first.project_id)
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_reference",
    ["project", "trace", "parent_project", "missing_trace", "missing_parent", "self"],
)
async def test_invalid_span_references_are_rejected(test_session_factory, invalid_reference):
    async with test_session_factory() as session:
        _, second, first, other, second_trace = await seed(session)
        parent = make_span(first, external_id="parent")
        session.add(parent)
        await session.commit()
        child = make_span(first, parent_span_id=parent.id)
        if invalid_reference == "project":
            child.project_id = second.id
            child.parent_span_id = None
        elif invalid_reference == "trace":
            child.trace_id = other.id
        elif invalid_reference == "parent_project":
            child.project_id = second.id
            child.trace_id = second_trace.id
        elif invalid_reference == "missing_trace":
            child.trace_id = uuid4()
            child.parent_span_id = None
        elif invalid_reference == "missing_parent":
            child.parent_span_id = uuid4()
        else:
            child.parent_span_id = child.id
        session.add(child)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metric",
    ["prompt_tokens", "completion_tokens", "total_tokens", "latency_ms", "estimated_cost_usd"],
)
async def test_negative_metrics_are_rejected(test_session_factory, metric):
    async with test_session_factory() as session:
        _, _, trace, _, _ = await seed(session)
        session.add(make_span(trace, **{metric: -1}))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["project", "trace", "parent"])
async def test_deletion_cascades_without_affecting_other_projects(test_session_factory, resource):
    async with test_session_factory() as session:
        first_project, _, first, _, second = await seed(session)
        parent = make_span(first, external_id="parent")
        session.add(parent)
        await session.flush()
        session.add_all([make_span(first, parent_span_id=parent.id), make_span(second)])
        await session.commit()
        if resource == "project":
            statement = delete(Project).where(Project.id == first_project.id)
        elif resource == "trace":
            statement = delete(Trace).where(Trace.id == first.id)
        else:
            statement = delete(Span).where(Span.id == parent.id)
        await session.execute(statement)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Span)) == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(Trace).where(Trace.id == second.id)
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table, column", [("traces", "status"), ("spans", "status"), ("spans", "kind")]
)
async def test_database_rejects_invalid_enum_values(test_session_factory, table, column):
    async with test_session_factory() as session:
        _, _, trace, _, _ = await seed(session)
        session.add(make_span(trace))
        await session.commit()
        # Raw SQL verifies database enforcement independently of ORM validation.
        with pytest.raises(DBAPIError, match="invalid"):
            await session.execute(text(f"UPDATE {table} SET {column} = 'invalid'"))
        await session.rollback()
