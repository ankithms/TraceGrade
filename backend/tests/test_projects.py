from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import authenticate_project_api_key
from app.api.errors import ApiError
from app.core.config import get_settings
from app.models import ApiKey

ADMIN_HEADERS = {"X-TraceGrade-Admin-Key": get_settings().admin_key}


async def create_project(
    client: AsyncClient,
    name: str,
    slug: str | None = None,
) -> dict:
    payload = {"name": name}
    if slug is not None:
        payload["slug"] = slug
    response = await client.post("/api/v1/projects", json=payload, headers=ADMIN_HEADERS)
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_admin_key_is_required(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/projects")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_admin_key"
    UUID(response.json()["error"]["request_id"])


@pytest.mark.asyncio
async def test_project_crud(api_client: AsyncClient) -> None:
    project = await create_project(api_client, "AI Code Reviewer")
    project_id = project["id"]

    assert project["slug"] == "ai-code-reviewer"

    list_response = await api_client.get("/api/v1/projects", headers=ADMIN_HEADERS)
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["id"] == project_id

    update_response = await api_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Code Reviewer Demo"},
        headers=ADMIN_HEADERS,
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Code Reviewer Demo"
    assert update_response.json()["slug"] == "ai-code-reviewer"

    delete_response = await api_client.delete(
        f"/api/v1/projects/{project_id}",
        headers=ADMIN_HEADERS,
    )
    assert delete_response.status_code == 204

    missing_response = await api_client.get(
        f"/api/v1/projects/{project_id}",
        headers=ADMIN_HEADERS,
    )
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "project_not_found"


@pytest.mark.asyncio
async def test_duplicate_project_slug_returns_conflict(api_client: AsyncClient) -> None:
    await create_project(api_client, "First", "shared-slug")

    response = await api_client.post(
        "/api/v1/projects",
        json={"name": "Second", "slug": "shared-slug"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_slug_conflict"


@pytest.mark.asyncio
async def test_api_key_lifecycle_and_project_isolation(
    api_client: AsyncClient,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first_project = await create_project(api_client, "First Project")
    second_project = await create_project(api_client, "Second Project")

    create_response = await api_client.post(
        f"/api/v1/projects/{first_project['id']}/api-keys",
        json={"name": "Demo SDK"},
        headers=ADMIN_HEADERS,
    )
    assert create_response.status_code == 201
    created_key = create_response.json()
    plaintext_key = created_key["key"]
    assert plaintext_key.startswith(f"{created_key['prefix']}_")
    assert "secret_hash" not in created_key

    list_response = await api_client.get(
        f"/api/v1/projects/{first_project['id']}/api-keys",
        headers=ADMIN_HEADERS,
    )
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert "key" not in list_response.json()["items"][0]

    async with test_session_factory() as session:
        authenticated_project = await authenticate_project_api_key(
            plaintext_key,
            session,
            get_settings(),
        )
        stored_key = await session.scalar(
            select(ApiKey).where(ApiKey.id == UUID(created_key["id"]))
        )
        assert authenticated_project.id == UUID(first_project["id"])
        assert stored_key is not None
        assert stored_key.last_used_at is not None
        assert stored_key.secret_hash != plaintext_key

    cross_project_response = await api_client.delete(
        f"/api/v1/projects/{second_project['id']}/api-keys/{created_key['id']}",
        headers=ADMIN_HEADERS,
    )
    assert cross_project_response.status_code == 404

    revoke_response = await api_client.delete(
        f"/api/v1/projects/{first_project['id']}/api-keys/{created_key['id']}",
        headers=ADMIN_HEADERS,
    )
    assert revoke_response.status_code == 204

    async with test_session_factory() as session:
        with pytest.raises(ApiError) as error:
            await authenticate_project_api_key(plaintext_key, session, get_settings())
        assert error.value.code == "invalid_api_key"


@pytest.mark.asyncio
async def test_validation_errors_follow_api_contract(api_client: AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/projects",
        json={"name": ""},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
