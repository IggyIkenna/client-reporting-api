"""Unit tests for MiFID II compliance routes (trade reporting, best execution, positions)."""

from __future__ import annotations

from collections.abc import Generator

import pytest
import unified_cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Disable auth and ensure mock mode is active for all compliance tests."""
    import functools

    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    from client_reporting_api.api.routes import compliance as _comp_mod

    cfg = _comp_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


class TestComplianceRoutes:
    """Tests for /api/v1/compliance routes in mock mode."""

    def test_trade_reporting_returns_data(self) -> None:
        """GET /api/v1/compliance/trade-reporting returns transaction reporting data."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/compliance/trade-reporting",
            params={"org_id": "org-alpha", "period_month": "2026-02"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == "org-alpha"
        assert data["reporting_status"] == "complete"
        assert data["total_transactions"] == 4285
        assert "transactions" in data
        assert isinstance(data["transactions"], list)
        assert len(data["transactions"]) > 0

    def test_best_execution_returns_venues(self) -> None:
        """GET /api/v1/compliance/best-execution returns venue performance data."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/compliance/best-execution",
            params={"org_id": "org-alpha", "period_month": "2026-02"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == "org-alpha"
        assert data["total_orders_analyzed"] == 3150
        assert "by_venue" in data
        assert isinstance(data["by_venue"], list)
        assert len(data["by_venue"]) == 3
        venue_names = [v["venue"] for v in data["by_venue"]]
        assert "binance" in venue_names
        assert "deribit" in venue_names

    def test_positions_returns_data(self) -> None:
        """GET /api/v1/compliance/positions returns large position reporting data."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/compliance/positions",
            params={"org_id": "org-alpha"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == "org-alpha"
        assert "positions" in data
        assert isinstance(data["positions"], list)
        assert len(data["positions"]) > 0
        assert "threshold_eur" in data
        assert "total_nav_eur" in data

    def test_dashboard_returns_sections(self) -> None:
        """GET /api/v1/compliance/dashboard returns compliance summary with sections."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(
            "/api/v1/compliance/dashboard",
            params={"org_id": "org-alpha", "period_month": "2026-02"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["org_id"] == "org-alpha"
        assert data["overall_status"] == "compliant"
        assert "sections" in data
        assert isinstance(data["sections"], list)
        assert len(data["sections"]) == 5
        section_names = [s["name"] for s in data["sections"]]
        assert "Transaction Reporting" in section_names
        assert "Best Execution" in section_names
        assert "alerts" in data
        assert "score_pct" in data
