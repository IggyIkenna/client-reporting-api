"""Unit tests for invoice analytics and transition routes.

Covers analytics.py (orders, coin breakdown, aggregated, performance) and
transitions.py — routes previously uncovered that caused coverage to fall
below the 70% fail_under floor.
"""

from __future__ import annotations

import functools
from collections.abc import Generator
from unittest.mock import patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import invoices as _inv_mod
from client_reporting_api.api.routes.invoices import analytics as _analytics_mod
from client_reporting_api.api.routes.invoices import transitions as _trans_mod
from client_reporting_api.core.trade_analytics import TradeAnalytics

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Disable auth and set mock mode — mirrors test_invoices.py fixture."""
    _original_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    cfg = _inv_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(_original_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
    app.dependency_overrides.pop(_analytics_mod._require_auth, None)
    app.dependency_overrides.pop(_trans_mod._require_auth, None)


@pytest.fixture
def _internal_analytics() -> Generator[None]:
    """Inject internal AuthContext for analytics routes."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_analytics_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_analytics_mod._require_auth, None)


@pytest.fixture
def _internal_transitions() -> Generator[None]:
    """Inject internal AuthContext for transition routes."""

    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_trans_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_trans_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/orders/{client_id}
# ---------------------------------------------------------------------------


class TestOrdersRoute:
    def test_returns_empty_when_no_orders_file(self, _internal_analytics: None) -> None:
        """Orders endpoint returns empty payload when orders.json does not exist."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/v1/invoices/orders/NONEXISTENT_CLIENT_XYZ")
        assert response.status_code == 200
        data = response.json()
        assert data["orders"] == []
        assert data["total"] == 0
        assert "limit" in data
        assert "offset" in data


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/analytics/{client_id}  and  GET /api/v1/invoices/analytics
# ---------------------------------------------------------------------------


class TestAnalyticsRoute:
    def test_coin_breakdown_returns_client_analytics(self, _internal_analytics: None) -> None:
        """GET /analytics/{client_id} delegates to compute_coin_breakdown."""
        fake_ta = TradeAnalytics(client_id="IK")
        with patch(
            "client_reporting_api.api.routes.invoices.analytics.compute_coin_breakdown",
            return_value=fake_ta,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/analytics/IK")
        assert response.status_code == 200
        assert response.json()["client_id"] == "IK"

    def test_aggregated_analytics_internal_only(self, _internal_analytics: None) -> None:
        """GET /analytics aggregates across all clients for internal callers."""
        fake_ta = TradeAnalytics(client_id="ALL")
        with patch(
            "client_reporting_api.api.routes.invoices.analytics.aggregate_clients",
            return_value=fake_ta,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/analytics")
        assert response.status_code == 200
        assert response.json()["client_id"] == "ALL"


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/performance/{client_id}
# ---------------------------------------------------------------------------


class TestPerformanceRoute:
    def test_returns_404_when_equity_curve_empty(self, _internal_analytics: None) -> None:
        """GET /performance/{client_id} returns 404 when no equity data exists."""
        with patch(
            "client_reporting_api.api.routes.invoices.analytics.get_equity_curve",
            return_value=[],
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/performance/IK")
        assert response.status_code == 404

    def test_returns_performance_stats_when_data_exists(self, _internal_analytics: None) -> None:
        """GET /performance/{client_id} returns stats dict when equity curve is present."""
        equity: list[dict[str, str | float]] = [
            {"date": "2026-01-01", "equity_usd": 10000.0},
            {"date": "2026-01-02", "equity_usd": 10100.0},
        ]
        stats: dict[str, float | str] = {"total_return_pct": 1.0, "sharpe_ratio": 1.5}
        with (
            patch(
                "client_reporting_api.api.routes.invoices.analytics.get_equity_curve",
                return_value=equity,
            ),
            patch(
                "client_reporting_api.api.routes.invoices.analytics.compute_performance_stats",
                return_value=stats,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/performance/IK")
        assert response.status_code == 200
        assert "total_return_pct" in response.json()


# ---------------------------------------------------------------------------
# PUT /api/v1/invoices/{invoice_id}/transition
# ---------------------------------------------------------------------------


class TestTransitionRoute:
    def test_draft_to_issued_happy_path(self, _internal_transitions: None) -> None:
        """PUT /transition issues a draft invoice via the state machine."""
        client = TestClient(app, raise_server_exceptions=True)
        gen_resp = client.post(
            "/api/v1/invoices/generate",
            json={
                "org_id": "org-transition-test",
                "period_month": "2026-04",
                "invoice_type": "management_fee",
                "currency": "USD",
            },
        )
        assert gen_resp.status_code == 200
        invoice_id = gen_resp.json()["invoice_id"]

        response = client.put(
            f"/api/v1/invoices/{invoice_id}/transition",
            json={"action": "issue", "note": "billing approved"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "issued"

    def test_returns_404_for_unknown_invoice(self, _internal_transitions: None) -> None:
        """PUT /transition returns 404 when invoice ID does not exist."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.put(
            "/api/v1/invoices/INV-NONEXISTENT-9999/transition",
            json={"action": "issue"},
        )
        assert response.status_code == 404

    def test_returns_400_for_unknown_action(self, _internal_transitions: None) -> None:
        """PUT /transition returns 400 for an unrecognized action string."""
        client = TestClient(app, raise_server_exceptions=True)
        response = client.put(
            "/api/v1/invoices/INV-2026-001/transition",
            json={"action": "bogus_action"},
        )
        assert response.status_code == 400

    def test_returns_409_for_illegal_state_transition(self, _internal_transitions: None) -> None:
        """PUT /transition returns 409 when target state is not reachable from current state.

        INV-2026-001 starts as 'draft'; 'pay' targets 'paid' which requires 'accepted' state.
        """
        client = TestClient(app, raise_server_exceptions=True)
        response = client.put(
            "/api/v1/invoices/INV-2026-001/transition",
            json={"action": "pay"},
        )
        assert response.status_code == 409
