"""Opt-in checks against an isolated PostgreSQL database migrated to head."""

import asyncio
from uuid import UUID

from sqlalchemy import delete, func, select
from test_datasets_evaluators import create_dataset, create_evaluator
from test_trace_ingestion_postgres import postgres_client  # noqa: F401

from app.models import Dataset, Evaluator, TestCase


async def test_concurrent_unique_names(postgres_client):  # noqa: F811
    client, headers, _, _ = postgres_client
    for resource, payload in [
        ("datasets", {"name": "Concurrent"}),
        ("evaluators", {"name": "Concurrent", "type": "exact_match"}),
    ]:
        responses = await asyncio.gather(
            *[client.post(f"/api/v1/{resource}", headers=headers, json=payload) for _ in range(5)]
        )
        assert sorted(response.status_code for response in responses) == [201, 409, 409, 409, 409]


async def test_postgres_json_round_trip_and_cascade(postgres_client):  # noqa: F811
    client, headers, factory, project_id = postgres_client
    dataset = await create_dataset(client, headers)
    evaluator = await create_evaluator(
        client,
        headers,
        kind="gemini_judge",
        config={
            "model": "gemini-test",
            "rubric": "Correctness",
            "pass_threshold": 0.75,
        },
    )
    path = f"/api/v1/datasets/{dataset['id']}"
    payload = {
        "name": "Unicode case",
        "input": {"code": "éλ", "tokens": [1, True, None]},
        "expected_output": None,
        "metadata": {"category": "security"},
    }
    created = await client.post(f"{path}/cases", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    case_path = f"{path}/cases/{created.json()['id']}"
    assert {key: created.json()[key] for key in payload} == payload
    changed = await client.patch(
        case_path, headers=headers, json={"expected_output": {"safe": False}}
    )
    assert changed.status_code == 200
    assert changed.json()["expected_output"] == {"safe": False}
    assert changed.json()["updated_at"] >= created.json()["updated_at"]
    changed = await client.patch(case_path, headers=headers, json={"input": None})
    assert changed.status_code == 200 and changed.json()["input"] is None
    async with factory() as session:
        stored = await session.get(Evaluator, UUID(evaluator["id"]))
        assert stored.config["pass_threshold"] == 0.75
        assert stored.project_id == project_id
    assert (await client.delete(path, headers=headers)).status_code == 204
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TestCase)
            .where(
                TestCase.dataset_id == UUID(dataset["id"]),
            )
        )
        assert count == 0
        # The fixture removes the project and remaining evaluator at teardown.
        assert await session.get(Dataset, UUID(dataset["id"])) is None
        await session.execute(delete(Evaluator).where(Evaluator.id == UUID(evaluator["id"])))
        await session.commit()
