"""Service startup smoke tests for client-reporting-api.

Verifies that:
1. The package imports cleanly without real cloud connections.
2. Core FastAPI app can be instantiated with auth disabled.
3. /health and /readiness probes return expected status.
4. SSE /api/v1/stream/reports endpoint is registered.
5. S2S verify_service_token is callable.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent real cloud calls during startup."""
    monkeypatch.setenv("CLOUD_PROVIDER", "local")
    monkeypatch.setenv("CLOUD_MOCK_MODE", "true")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("DISABLE_AUTH", "true")


class TestClientReportingApiStartup:
    """Smoke tests: package imports and app instantiation."""

    def test_import_package(self) -> None:
        """Package import must succeed."""
        import client_reporting_api  # noqa: F401

    def test_import_auth_module(self) -> None:
        """auth.py must import cleanly."""
        import client_reporting_api.auth  # noqa: F401

    def test_verify_service_token_exists(self) -> None:
        """verify_service_token must be exported from auth module."""
        from client_reporting_api.auth import verify_service_token

        assert callable(verify_service_token)

    def test_verify_api_key_exists(self) -> None:
        """verify_api_key must be exported from auth module."""
        from client_reporting_api.auth import verify_api_key

        assert callable(verify_api_key)

    def test_app_instantiates(self) -> None:
        """FastAPI app must exist and have the correct title."""
        from client_reporting_api.api.main import app

        assert app is not None
        assert app.title == "Client Reporting Service"

    def test_sse_stream_route_registered(self) -> None:
        """GET /api/v1/stream/reports SSE endpoint must be registered."""
        from client_reporting_api.api.main import app

        routes = [r.path for r in app.routes]  # type: ignore[attr-defined]
        assert "/api/v1/stream/reports" in routes


class TestClientReportingApiHealthProbes:
    """Tests for /health and /readiness HTTP probes."""

    @pytest.fixture()
    def client(self):
        """Return TestClient with auth disabled."""
        from fastapi.testclient import TestClient

        from client_reporting_api.api.main import app

        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_health_returns_200(self, client) -> None:
        """GET /health must return 200 with status ok."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "client-reporting-api"

    def test_readiness_returns_200(self, client) -> None:
        """GET /readiness must return 200 with status ready."""
        response = client.get("/readiness")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"

    def test_sse_stream_endpoint_is_reachable(self, client) -> None:
        """GET /api/v1/stream/reports must be a registered route (route resolves without 404)."""
        from client_reporting_api.api.main import app

        routes = {r.path for r in app.routes}  # type: ignore[attr-defined]
        assert "/api/v1/stream/reports" in routes, "SSE route /api/v1/stream/reports must be registered"
