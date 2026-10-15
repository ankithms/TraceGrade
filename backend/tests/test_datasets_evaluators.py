from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Dataset, Evaluator, TestCase

ADMIN = {"X-TraceGrade-Admin-Key": get_settings().admin_key}


async def project_key(client):
    project = await client.post(
        "/api/v1/projects",
        headers=ADMIN,
        json={"name": f"Fixture {uuid4().hex}"},
    )
    assert project.status_code == 201
    key = await client.post(
        f"/api/v1/projects/{project.json()['id']}/api-keys",
        headers=ADMIN,
        json={"name": "Test"},
    )
    assert key.status_code == 201
    return project.json()["id"], {"Authorization": f"Bearer {key.json()['key']}"}


async def create_dataset(client, headers, name="Reviews"):
    response = await client.post("/api/v1/datasets", headers=headers, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def create_evaluator(client, headers, name="Exact", kind="exact_match", config=None):
    response = await client.post(
        "/api/v1/evaluators",
        headers=headers,
        json={"name": name, "type": kind, "config": config or {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_dataset_case_crud_and_null_patch(api_client):
    _, headers = await project_key(api_client)
    dataset = await create_dataset(api_client, headers)
    path = f"/api/v1/datasets/{dataset['id']}"
    assert (await api_client.get(path, headers=headers)).json() == dataset
    assert dataset["created_at"].endswith("Z")
    updated = await api_client.patch(path, headers=headers, json={"description": "Review fixtures"})
    assert updated.json()["name"] == "Reviews"
    assert updated.json()["description"] == "Review fixtures"
    assert (await api_client.patch(path, headers=headers, json={"description": None})).json()[
        "description"
    ] is None
    payload = {
        "name": "Unsafe SQL",
        "input": {"diff": "+query(user)"},
        "expected_output": ["SQL injection"],
        "metadata": {"severity": "high"},
    }
    case = await api_client.post(f"{path}/cases", headers=headers, json=payload)
    assert case.status_code == 201, case.text
    case_path = f"{path}/cases/{case.json()['id']}"
    assert {key: case.json()[key] for key in payload} == payload
    assert (await api_client.get(case_path, headers=headers)).json() == case.json()
    changed = await api_client.patch(case_path, headers=headers, json={"expected_output": None})
    assert changed.status_code == 200
    assert changed.json()["expected_output"] is None
    assert changed.json()["input"] == payload["input"]
    changed = await api_client.patch(
        case_path, headers=headers, json={"input": None, "metadata": {}}
    )
    assert changed.json()["input"] is None
    assert changed.json()["metadata"] == {}
    assert (await api_client.delete(case_path, headers=headers)).status_code == 204
    assert (await api_client.get(case_path, headers=headers)).status_code == 404
    assert (await api_client.delete(path, headers=headers)).status_code == 204
    assert (await api_client.get(path, headers=headers)).status_code == 404


async def test_project_isolation_all_operations(api_client):
    _, owner = await project_key(api_client)
    _, other = await project_key(api_client)
    dataset = await create_dataset(api_client, owner)
    path = f"/api/v1/datasets/{dataset['id']}"
    case = await api_client.post(f"{path}/cases", headers=owner, json={"name": "Case", "input": 1})
    evaluator = await create_evaluator(api_client, owner)
    evaluator_path = f"/api/v1/evaluators/{evaluator['id']}"
    case_path = f"{path}/cases/{case.json()['id']}"
    for method, target, payload in [
        ("GET", path, None),
        ("PATCH", path, {"name": "stolen"}),
        ("DELETE", path, None),
        ("GET", f"{path}/cases", None),
        ("POST", f"{path}/cases", {"name": "x", "input": None}),
        ("GET", case_path, None),
        ("PATCH", case_path, {"name": "stolen"}),
        ("DELETE", case_path, None),
        ("GET", evaluator_path, None),
        ("PATCH", evaluator_path, {"name": "stolen"}),
        ("DELETE", evaluator_path, None),
    ]:
        response = await api_client.request(method, target, headers=other, json=payload)
        assert response.status_code == 404, (method, target, response.text)
    for resource in ("datasets", "evaluators"):
        assert (await api_client.get(f"/api/v1/{resource}", headers=other)).json() == {
            "items": [],
            "total": 0,
        }
        assert (await api_client.get(f"/api/v1/{resource}")).status_code == 401
    another = await create_dataset(api_client, owner, "Another")
    wrong_parent = f"/api/v1/datasets/{another['id']}/cases/{case.json()['id']}"
    for method in ("GET", "PATCH", "DELETE"):
        response = await api_client.request(
            method, wrong_parent, headers=owner, json={"name": "x"} if method == "PATCH" else None
        )
        assert response.status_code == 404


async def test_names_pagination_and_conflict_rollback(api_client):
    _, headers = await project_key(api_client)
    _, other = await project_key(api_client)
    for resource, creator in [("datasets", create_dataset), ("evaluators", create_evaluator)]:
        first = await creator(api_client, headers, "First")
        second = await creator(api_client, headers, "Second")
        await creator(api_client, other, "First")
        base = f"/api/v1/{resource}"
        duplicate = {"name": "First"}
        if resource == "evaluators":
            duplicate["type"] = "exact_match"
        assert (await api_client.post(base, headers=headers, json=duplicate)).status_code == 409
        assert (
            await api_client.patch(
                f"{base}/{second['id']}", headers=headers, json={"name": "First"}
            )
        ).status_code == 409
        assert (await api_client.get(f"{base}/{second['id']}", headers=headers)).json()[
            "name"
        ] == "Second"
        page = await api_client.get(base, headers=headers, params={"limit": 1, "offset": 1})
        full = (await api_client.get(base, headers=headers)).json()
        assert page.json() == {"items": full["items"][1:], "total": 2}
        assert {i["id"] for i in full["items"]} == {first["id"], second["id"]}
        assert (await api_client.get(base, headers=headers, params={"offset": 5})).json() == {
            "items": [],
            "total": 2,
        }
        assert (
            await api_client.get(base, headers=headers, params={"limit": 101})
        ).status_code == 422


async def test_case_pagination_and_cascades(api_client, test_session_factory):
    project_id, headers = await project_key(api_client)
    dataset = await create_dataset(api_client, headers)
    path = f"/api/v1/datasets/{dataset['id']}"
    for index in range(3):
        response = await api_client.post(
            f"{path}/cases", headers=headers, json={"name": str(index), "input": index}
        )
        assert response.status_code == 201
    page = (await api_client.get(f"{path}/cases?limit=2&offset=1", headers=headers)).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert (await api_client.delete(path, headers=headers)).status_code == 204
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(TestCase)) == 0
    await create_dataset(api_client, headers)
    await create_evaluator(api_client, headers)
    assert (
        await api_client.delete(f"/api/v1/projects/{project_id}", headers=ADMIN)
    ).status_code == 204
    async with test_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Dataset)) == 0
        assert await session.scalar(select(func.count()).select_from(Evaluator)) == 0


@pytest.mark.parametrize(
    "kind,config",
    [
        ("exact_match", {}),
        ("contains", {"case_sensitive": False}),
        ("json_match", {}),
        (
            "gemini_judge",
            {"model": "gemini-test", "rubric": "Check correctness", "pass_threshold": 0.8},
        ),
    ],
)
async def test_evaluator_crud(api_client, kind, config):
    _, headers = await project_key(api_client)
    evaluator = await create_evaluator(api_client, headers, kind=kind, config=config)
    path = f"/api/v1/evaluators/{evaluator['id']}"
    assert (await api_client.get(path, headers=headers)).json() == evaluator
    changed = await api_client.patch(path, headers=headers, json={"name": "New name"})
    assert changed.status_code == 200
    assert changed.json()["config"] == evaluator["config"]
    assert (
        await api_client.patch(path, headers=headers, json={"type": "contains"})
    ).status_code == 422
    assert (
        await api_client.patch(path, headers=headers, json={"config": {"unknown": True}})
    ).status_code == 422
    assert (await api_client.get(path, headers=headers)).json()["config"] == evaluator["config"]
    assert (await api_client.delete(path, headers=headers)).status_code == 204
    assert (await api_client.get(path, headers=headers)).status_code == 404


async def test_configuration_patch_replaces_and_validates(api_client):
    _, headers = await project_key(api_client)
    evaluator = await create_evaluator(
        api_client, headers, config={"case_sensitive": False, "strip_whitespace": True}
    )
    path = f"/api/v1/evaluators/{evaluator['id']}"
    changed = await api_client.patch(path, headers=headers, json={"config": {}})
    assert changed.json()["config"] == {"case_sensitive": True, "strip_whitespace": False}
    for payload in ({}, {"name": None}, {"config": None}, {"config": {"case_sensitive": "false"}}):
        assert (await api_client.patch(path, headers=headers, json=payload)).status_code == 422


@pytest.mark.parametrize(
    "kind,config",
    [
        ("unknown", {}),
        ("exact_match", {"rubric": "x"}),
        ("contains", {"case_sensitive": 0}),
        ("json_match", {"strip_whitespace": True}),
        ("gemini_judge", {}),
        ("gemini_judge", {"model": "gemini-test", "rubric": " "}),
        ("gemini_judge", {"model": "../secret", "rubric": "x"}),
        ("gemini_judge", {"model": "gemini-test", "rubric": "x", "pass_threshold": 1.1}),
        ("gemini_judge", {"model": "gemini-test", "rubric": "x", "api_key": "secret"}),
    ],
)
async def test_invalid_configuration(api_client, kind, config):
    _, headers = await project_key(api_client)
    response = await api_client.post(
        "/api/v1/evaluators", headers=headers, json={"name": "x", "type": kind, "config": config}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_resource_validation(api_client):
    _, headers = await project_key(api_client)
    for payload in (
        {"name": " "},
        {"name": "x", "project_id": str(uuid4())},
        {"name": "x", "description": "x" * 4097},
    ):
        assert (
            await api_client.post("/api/v1/datasets", headers=headers, json=payload)
        ).status_code == 422
    dataset = await create_dataset(api_client, headers)
    path = f"/api/v1/datasets/{dataset['id']}"
    for payload in ({}, {"name": None}):
        assert (await api_client.patch(path, headers=headers, json=payload)).status_code == 422
    for payload in (
        {"name": "x"},
        {"name": "x", "input": "é" * 32768},
        {"name": "x", "input": None, "metadata": {"x": "a" * 16384}},
        {"name": "x", "input": 1, "metadata": None},
    ):
        response = await api_client.post(f"{path}/cases", headers=headers, json=payload)
        assert response.status_code == 422
        UUID(response.json()["error"]["request_id"])
    response = await api_client.post(
        f"{path}/cases",
        headers={**headers, "Content-Type": "application/json"},
        content='{"name":"x","input":NaN}',
    )
    assert response.status_code == 422


async def test_saved_case_and_evaluator_produce_a_score(api_client):
    from tracegrade_evaluators import evaluate

    _, headers = await project_key(api_client)
    dataset = await create_dataset(api_client, headers)
    evaluator = await create_evaluator(api_client, headers, kind="json_match")
    created = await api_client.post(
        f"/api/v1/datasets/{dataset['id']}/cases",
        headers=headers,
        json={
            "name": "Review",
            "input": {"diff": "+query(user)"},
            "expected_output": {"safe": False, "finding": "SQL injection"},
        },
    )
    assert created.status_code == 201
    cases = (
        await api_client.get(f"/api/v1/datasets/{dataset['id']}/cases", headers=headers)
    ).json()
    saved = (await api_client.get(f"/api/v1/evaluators/{evaluator['id']}", headers=headers)).json()
    case = cases["items"][0]
    result = await evaluate(
        saved["type"],
        saved["config"],
        input=case["input"],
        expected_output=case["expected_output"],
        output='{"finding":"SQL injection","safe":false}',
    )
    assert result.score == 1.0 and result.passed
