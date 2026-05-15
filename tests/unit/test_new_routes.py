"""Unit tests for the new reports, pnl, and alerts routes."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app

# Suppress cloud_mock_mode deprecation warnings from test fixture save/restore
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _disable_auth_and_mock_mode() -> Generator[None]:
    """Disable API key auth and mock mode for all route tests in this module.

    Tests mock generate_pnl_report directly, so is_mock_mode() must return False
    to avoid the mock-mode short-circuit in route handlers.
    """
    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True

    # Force live code path so patches on generate_pnl_report take effect
    from client_reporting_api.api.routes import pnl as _pnl_mod
    from client_reporting_api.api.routes import reports as _reports_mod

    reports_cfg = _reports_mod._cloud_cfg
    pnl_cfg = _pnl_mod._cloud_cfg
    orig_reports_data_mode = reports_cfg.data_mode
    orig_pnl_data_mode = pnl_cfg.data_mode
    orig_reports_mock = reports_cfg.cloud_mock_mode
    orig_pnl_mock = pnl_cfg.cloud_mock_mode
    reports_cfg.data_mode = "real"  # type: ignore[misc]
    pnl_cfg.data_mode = "real"  # type: ignore[misc]
    reports_cfg.cloud_mock_mode = False  # type: ignore[misc]
    pnl_cfg.cloud_mock_mode = False  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    reports_cfg.data_mode = orig_reports_data_mode  # type: ignore[misc]
    pnl_cfg.data_mode = orig_pnl_data_mode  # type: ignore[misc]
    reports_cfg.cloud_mock_mode = orig_reports_mock  # type: ignore[misc]
    pnl_cfg.cloud_mock_mode = orig_pnl_mock  # type: ignore[misc]


# ---------------------------------------------------------------------------
# POST /api/reports/generate
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_generate_report_returns_ok_with_data(self) -> None:
        """POST /api/reports/generate returns 200 with report dict when GCS has data."""
        expected: dict[str, object] = {
            "status": "ok",
            "client_id": "client-1",
            "period_month": "2024-01",
            "rows": [{"pnl": 1000.0}],
        }
        with patch(
            "client_reporting_api.api.routes.reports.generate_pnl_report",
            return_value=expected,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/api/reports/generate",
                json={"client_id": "client-1", "period_month": "2024-01"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["client_id"] == "client-1"
        assert data["period_month"] == "2024-01"
        assert isinstance(data["rows"], list)

    def test_generate_report_returns_no_data_when_gcs_empty(self) -> None:
        """POST /api/reports/generate returns no_data status when GCS is empty."""
        expected: dict[str, object] = {
            "status": "no_data",
            "client_id": "client-99",
            "period_month": "2024-01",
            "rows": [],
        }
        with patch(
            "client_reporting_api.api.routes.reports.generate_pnl_report",
            return_value=expected,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.post(
                "/api/reports/generate",
                json={"client_id": "client-99", "period_month": "2024-01"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_data"
        assert data["rows"] == []

    def test_generate_report_returns_500_on_exception(self) -> None:
        """POST /api/reports/generate returns 500 when report generation raises."""
        with patch(
            "client_reporting_api.api.routes.reports.generate_pnl_report",
            side_effect=RuntimeError("GCS connection failed"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/api/reports/generate",
                json={"client_id": "client-1", "period_month": "2024-01"},
            )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /pnl
# ---------------------------------------------------------------------------


class TestGetPnl:
    def test_get_pnl_returns_ok(self) -> None:
        """GET /pnl returns 200 with pnl data from GCS."""
        expected: dict[str, object] = {
            "status": "ok",
            "client_id": "client-1",
            "period_month": "2024-01",
            "rows": [{"pnl": 500.0}],
        }
        with patch(
            "client_reporting_api.api.routes.pnl.generate_pnl_report",
            return_value=expected,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/pnl",
                params={"client_id": "client-1", "period_month": "2024-01"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_get_pnl_missing_params_returns_422(self) -> None:
        """GET /pnl without required query params returns 422."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/pnl")
        assert response.status_code == 422

    def test_get_pnl_returns_500_on_exception(self) -> None:
        """GET /pnl returns 500 when PnLReader raises."""
        with patch(
            "client_reporting_api.api.routes.pnl.generate_pnl_report",
            side_effect=RuntimeError("storage error"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/pnl",
                params={"client_id": "client-1", "period_month": "2024-01"},
            )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /performance  (live path)
# ---------------------------------------------------------------------------


class TestGetPerformance:
    def test_get_performance_returns_status_from_pnl(self) -> None:
        """GET /performance returns status from live PnL report."""
        expected: dict[str, object] = {
            "status": "ok",
            "client_id": "client-1",
            "period_month": "2024-01",
            "rows": [{"pnl": 500.0}],
        }
        with patch(
            "client_reporting_api.api.routes.pnl.generate_pnl_report",
            return_value=expected,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/performance",
                params={"client_id": "client-1", "period_month": "2024-01"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "client-1"
        assert data["period_month"] == "2024-01"
        assert data["status"] == "ok"

    def test_get_performance_returns_500_on_exception(self) -> None:
        """GET /performance returns 500 when PnL computation raises."""
        with patch(
            "client_reporting_api.api.routes.pnl.generate_pnl_report",
            side_effect=RuntimeError("compute error"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get(
                "/performance",
                params={"client_id": "client-1", "period_month": "2024-01"},
            )
        assert response.status_code == 500


# ---------------------------------------------------------------------------
# GET /alerts
# ---------------------------------------------------------------------------


class TestGetAlerts:
    def test_get_alerts_proxies_alerting_service(self) -> None:
        """GET /alerts returns list from alerting-service."""
        fake_alerts = [{"id": "alert-1", "severity": "high"}]
        with patch(
            "client_reporting_api.api.routes.alerts.get_alerts",
            return_value=fake_alerts,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/alerts", headers={"X-API-Key": "dev-mode"})
        assert response.status_code == 200
        assert response.json() == fake_alerts

    def test_get_alerts_returns_503_when_service_unavailable(self) -> None:
        """GET /alerts returns 503 when alerting-service is unreachable."""
        with patch(
            "client_reporting_api.api.routes.alerts.get_alerts",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/alerts", headers={"X-API-Key": "dev-mode"})
        assert response.status_code == 503

    def test_get_alerts_returns_upstream_error_status(self) -> None:
        """GET /alerts propagates HTTP error status from alerting-service."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 502
        with patch(
            "client_reporting_api.api.routes.alerts.get_alerts",
            side_effect=httpx.HTTPStatusError(
                "bad gateway", request=MagicMock(), response=mock_response
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/alerts", headers={"X-API-Key": "dev-mode"})
        assert response.status_code == 502


# ---------------------------------------------------------------------------
# alerts_client unit tests
# ---------------------------------------------------------------------------


class TestAlertsClient:
    def test_get_alerts_calls_correct_url(self) -> None:
        """get_alerts constructs correct URL and returns parsed JSON."""
        from client_reporting_api.core.alerts_client import get_alerts

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.json.return_value = [{"id": "a1"}]
        with patch("client_reporting_api.core.alerts_client.httpx.get", return_value=mock_resp):
            result = get_alerts("http://alerting-service:8080")
        assert result == [{"id": "a1"}]
        mock_resp.raise_for_status.assert_called_once()


# ---------------------------------------------------------------------------
# pnl_reader unit tests
# ---------------------------------------------------------------------------


class TestPnLReader:
    def test_generate_returns_ok_with_rows(self) -> None:
        """generate_pnl_report returns ok status and rows when data available."""
        from client_reporting_api.core.pnl_reader import generate_pnl_report

        mock_source = MagicMock()
        mock_source.read.return_value = pd.DataFrame({"pnl": [100.0, 200.0]})
        with patch(
            "client_reporting_api.core.pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            result = generate_pnl_report("client-1", "2024-01")
        assert result["status"] == "ok"
        assert len(result["rows"]) == 2  # type: ignore[arg-type]

    def test_generate_returns_no_data_when_empty(self) -> None:
        """generate_pnl_report returns no_data when GCS returns empty DataFrame."""
        from client_reporting_api.core.pnl_reader import generate_pnl_report

        mock_source = MagicMock()
        mock_source.read.return_value = pd.DataFrame()
        with patch(
            "client_reporting_api.core.pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            result = generate_pnl_report("client-x", "2024-02")
        assert result["status"] == "no_data"
        assert result["rows"] == []

    def test_generate_returns_no_data_on_file_not_found(self) -> None:
        """generate_pnl_report returns no_data when source raises FileNotFoundError."""
        from client_reporting_api.core.pnl_reader import generate_pnl_report

        mock_source = MagicMock()
        mock_source.read.side_effect = FileNotFoundError("not found")
        with patch(
            "client_reporting_api.core.pnl_reader.get_data_source",
            return_value=mock_source,
        ):
            result = generate_pnl_report("client-x", "2024-03")
        assert result["status"] == "no_data"
