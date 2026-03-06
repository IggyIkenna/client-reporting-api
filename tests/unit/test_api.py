"""Unit tests for FastAPI app routes (health, metrics)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from client_reporting_api.api.main import app


def test_health_endpoint_returns_ok() -> None:
    """GET /health returns 200 with expected JSON payload."""
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "client-reporting-api"


def test_metrics_endpoint_returns_200() -> None:
    """GET /metrics returns 200 with text/plain content type."""
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
