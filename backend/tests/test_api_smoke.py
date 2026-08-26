"""Smoke test cho điểm vào FastAPI và các router đang được đăng ký."""

import pytest

from app.api.main import app, health_check


def test_openapi_registers_current_backend_routes() -> None:
    """OpenAPI phải có các nhóm route chính của backend hiện tại."""
    paths = app.openapi()["paths"]

    assert "/health" in paths
    assert any(path.startswith("/api/v1/vehicles") for path in paths)
    assert any(path.startswith("/api/v1/telematics") for path in paths)
    assert any(path.startswith("/api/v1/telemetry") for path in paths)
    assert any(path.startswith("/api/v1/charging-stations") for path in paths)
    assert any(path.startswith("/api/v1/charging-sessions") for path in paths)


@pytest.mark.asyncio
async def test_health_endpoint_returns_healthy_status() -> None:
    """Health check trả trạng thái healthy mà không cần database."""
    response = await health_check()

    assert response["status"] == "healthy"
    assert "version" in response
