from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_liveness() -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "tracegrade-api"}


def test_readiness_when_database_is_available(monkeypatch) -> None:
    readiness_check = AsyncMock(return_value=True)
    monkeypatch.setattr("app.api.routes.health.database_is_ready", readiness_check)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok"}}


def test_readiness_when_database_is_unavailable(monkeypatch) -> None:
    readiness_check = AsyncMock(return_value=False)
    monkeypatch.setattr("app.api.routes.health.database_is_ready", readiness_check)

    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "error"}}
