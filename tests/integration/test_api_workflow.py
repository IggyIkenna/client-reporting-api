"""API workflow integration tests for client-reporting-api.

Exercises real FastAPI routes under CLOUD_MOCK_MODE=true, DISABLE_AUTH=true
using TestClient. Tests health, report listing, PnL, and error flows.
"""

from __future__ import annotations

import os

os.environ.setdefault("CLOUD_MOCK_MODE", "true")
os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("DISABLE_AUTH", "true")
os.environ.setdefault("MOCK_STATE_MODE", "deterministic")

import pytest
from fastapi.testclient import TestClient
from unified_trading_library import setup_events

setup_events("client-reporting-api", "test")

from client_reporting_api.api.main import app

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Create a TestClient for the client-reporting-api app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health & readiness
# ---------------------------------------------------------------------------


class TestHealthWorkflow:
    """Health and readiness probe tests."""

    def test_health_returns_200(self, client: TestClient) -> None:
        """GET /health returns 200 with service identity."""
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "client-reporting-api"
        assert body["status"] == "ok"

    def test_readiness_returns_200(self, client: TestClient) -> None:
        """GET /readiness returns 200 with ready status."""
        resp = client.get("/readiness")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"

    def test_metrics_returns_prometheus_format(self, client: TestClient) -> None:
        """GET /metrics returns Prometheus text format."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get(
            "content-type", ""
        ) or "text/plain" in resp.headers.get("Content-Type", "")


# ---------------------------------------------------------------------------
# Reports workflow
# ---------------------------------------------------------------------------


class TestReportsWorkflow:
    """Report listing and generation workflow tests."""

    def test_list_reports(self, client: TestClient) -> None:
        """GET /api/reports returns a list of reports in mock mode."""
        resp = client.get("/api/reports")
        assert resp.status_code == 200
        reports = resp.json()
        assert isinstance(reports, list)

    def test_generate_report(self, client: TestClient) -> None:
        """POST /api/reports/generate creates a report in mock mode."""
        payload = {
            "client_id": "test-client-001",
            "period_month": "2026-01",
        }
        resp = client.post("/api/reports/generate", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_id"] == "test-client-001"
        assert body["period_month"] == "2026-01"
        assert "report_id" in body

    def test_generate_report_missing_fields_returns_422(self, client: TestClient) -> None:
        """POST /api/reports/generate with missing fields returns 422."""
        resp = client.post("/api/reports/generate", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PnL workflow
# ---------------------------------------------------------------------------


class TestPnLWorkflow:
    """PnL data endpoint tests."""

    def test_get_pnl_with_required_params(self, client: TestClient) -> None:
        """GET /pnl with client_id and period_month returns PnL data."""
        resp = client.get("/pnl?client_id=test-client&period_month=2026-01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_id"] == "test-client"
        assert body["period_month"] == "2026-01"

    def test_get_pnl_missing_params_returns_422(self, client: TestClient) -> None:
        """GET /pnl without required params returns 422."""
        resp = client.get("/pnl")
        assert resp.status_code == 422

    def test_get_performance_with_params(self, client: TestClient) -> None:
        """GET /performance returns performance metrics."""
        resp = client.get("/performance?client_id=test-client&period_month=2026-01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["client_id"] == "test-client"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Error response tests."""

    def test_nonexistent_endpoint_returns_404(self, client: TestClient) -> None:
        """GET /nonexistent returns 404."""
        resp = client.get("/api/v1/does-not-exist")
        assert resp.status_code in (404, 405)

    def test_correlation_id_header_propagated(self, client: TestClient) -> None:
        """Requests get X-Correlation-ID in the response."""
        resp = client.get("/health")
        assert "x-correlation-id" in resp.headers
