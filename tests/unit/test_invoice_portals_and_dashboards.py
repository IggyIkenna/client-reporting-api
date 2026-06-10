"""Unit tests for invoice portal views and fee dashboards.

Covers portals.py (38 uncovered lines) and dashboards.py (8 uncovered lines).
"""

from __future__ import annotations

import functools
from collections.abc import Generator
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import invoices as _inv_mod
from client_reporting_api.api.routes.invoices import dashboards as _dash_mod
from client_reporting_api.api.routes.invoices import portals as _portals_mod

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _at_hwm_client(client_id: str = "PR") -> dict[str, object]:
    return {
        "client_id": client_id,
        "full_name": "Test Client",
        "currency": "USDT",
        "current_equity": Decimal("110000"),
        "current_equity_usd": Decimal("110000"),
        "effective_hwm": Decimal("100000"),
        "deposits_since_hwm": Decimal("0"),
        "pnl_above_odum_hwm": Decimal("10000"),
        "odum_fee": Decimal("3400"),
        "trader_fee_gross": Decimal("1000"),
        "trader_fee_net": Decimal("900"),
        "credit_applied": Decimal("100"),
        "trader_already_paid": Decimal("0"),
        "trader_hwm": Decimal("100000"),
        "pnl_above_trader_hwm": Decimal("10000"),
        "introducer_fee": Decimal("0"),
    }


def _underwater_client(client_id: str = "NN") -> dict[str, object]:
    return {
        "client_id": client_id,
        "full_name": "Underwater Client",
        "currency": "USDT",
        "current_equity": Decimal("95000"),
        "current_equity_usd": Decimal("95000"),
        "odum_hwm": Decimal("100000"),
        "recovery_required": Decimal("5000"),
        "trader_credits": Decimal("0"),
        "server_cost": Decimal("50"),
    }


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
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
    app.dependency_overrides.pop(_portals_mod._require_auth, None)
    app.dependency_overrides.pop(_dash_mod._require_auth, None)


@pytest.fixture
def _internal_portals() -> Generator[None]:
    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_portals_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_portals_mod._require_auth, None)


@pytest.fixture
def _internal_dashboards() -> Generator[None]:
    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[_dash_mod._require_auth] = _fake
    yield
    app.dependency_overrides.pop(_dash_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/portal/admin
# ---------------------------------------------------------------------------


class TestPortalAdmin:
    def test_admin_portal_with_clients(self, _internal_portals: None) -> None:
        """GET /portal/admin returns clients grouped by HWM and underwater status."""
        summary = {
            "at_hwm": [_at_hwm_client("PR")],
            "underwater": [_underwater_client("NN")],
        }
        with (
            patch("client_reporting_api.api.routes.invoices.portals.state_mgr") as mock_mgr,
        ):
            mock_mgr.get_dashboard_summary.return_value = summary
            mock_mgr.get_invoices.return_value = []
            mock_mgr.get_introducer_state.return_value = None
            mock_mgr.compute_current_fees.return_value = None

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/portal/admin")

        assert response.status_code == 200
        data = response.json()
        assert "clients" in data
        assert len(data["clients"]) == 2
        client_statuses = {c["client_id"]: c["status"] for c in data["clients"]}
        assert client_statuses["PR"] == "AT_HWM"
        assert client_statuses["NN"] == "UNDERWATER"

    def test_admin_portal_empty_data(self, _internal_portals: None) -> None:
        """GET /portal/admin returns empty clients when no data."""
        with patch("client_reporting_api.api.routes.invoices.portals.state_mgr") as mock_mgr:
            mock_mgr.get_dashboard_summary.return_value = {"at_hwm": [], "underwater": []}
            mock_mgr.get_invoices.return_value = []
            mock_mgr.get_introducer_state.return_value = None

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/portal/admin")

        assert response.status_code == 200
        assert response.json()["clients"] == []


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/portal/trader
# ---------------------------------------------------------------------------


class TestPortalTrader:
    def test_trader_portal_with_pnl(self, _internal_portals: None) -> None:
        """GET /portal/trader includes clients with positive PnL."""
        at_hwm = [_at_hwm_client("PR")]
        with patch("client_reporting_api.api.routes.invoices.portals.state_mgr") as mock_mgr:
            mock_mgr.get_dashboard_summary.return_value = {
                "at_hwm": at_hwm,
                "underwater": [_underwater_client("NN")],
            }
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/portal/trader")

        assert response.status_code == 200
        data = response.json()
        assert "performance_fees" in data
        assert len(data["performance_fees"]) == 1
        assert data["performance_fees"][0]["client_id"] == "PR"
        assert "total_due" in data

    def test_trader_portal_no_pnl_clients(self, _internal_portals: None) -> None:
        """GET /portal/trader excludes clients with zero PnL."""
        zero_pnl_client: dict[str, object] = {
            "client_id": "ET",
            "pnl_above_trader_hwm": Decimal("0"),
        }
        with patch("client_reporting_api.api.routes.invoices.portals.state_mgr") as mock_mgr:
            mock_mgr.get_dashboard_summary.return_value = {
                "at_hwm": [zero_pnl_client],
                "underwater": [],
            }
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/portal/trader")

        assert response.status_code == 200
        assert response.json()["performance_fees"] == []


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/portal/introducer/{introducer_id}
# ---------------------------------------------------------------------------


class TestPortalIntroducer:
    def test_introducer_portal_no_linked_clients(self, _internal_portals: None) -> None:
        """GET /portal/introducer/{id} returns empty clients when none linked."""
        with patch("client_reporting_api.api.routes.invoices.portals.state_mgr") as mock_mgr:
            mock_mgr.get_introducer_state.return_value = None

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/portal/introducer/INT-001")

        assert response.status_code == 200
        data = response.json()
        assert data["introducer_id"] == "INT-001"
        assert data["clients"] == []
        assert data["total_balance_due"] == 0.0

    def test_introducer_portal_with_linked_client(self, _internal_portals: None) -> None:
        """GET /portal/introducer/{id} returns clients linked to the introducer."""
        intro_state = {
            "introducer_id": "INT-001",
            "introducer_name": "Jane Smith",
            "rate_pct": 0.10,
            "cumulative_odum_invoiced": 10000.0,
            "paid": 500.0,
        }
        fees = {
            "odum_fee": Decimal("3400"),
            "full_name": "Test Client",
            "currency": "USDT",
            "current_equity": Decimal("110000"),
            "current_equity_usd": Decimal("110000"),
            "effective_hwm": Decimal("100000"),
            "pnl_above_odum_hwm": Decimal("10000"),
        }
        with patch("client_reporting_api.api.routes.invoices.portals.state_mgr") as mock_mgr:
            mock_mgr.get_introducer_state.return_value = intro_state
            mock_mgr.compute_current_fees.return_value = fees

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/portal/introducer/INT-001")

        assert response.status_code == 200
        data = response.json()
        assert data["introducer_id"] == "INT-001"
        assert len(data["clients"]) > 0
        assert "balance_due" in data["clients"][0]


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/dashboard/trader-payment  (dashboards.py uncovered)
# ---------------------------------------------------------------------------


class TestDashboardTraderPayment:
    def test_returns_payment_summary_with_fees(self, _internal_dashboards: None) -> None:
        """GET /dashboard/trader-payment returns fee summary with at-HWM clients."""
        at_hwm = [_at_hwm_client("PR")]
        with patch("client_reporting_api.api.routes.invoices.dashboards.state_mgr") as mock_mgr:
            mock_mgr.get_dashboard_summary.return_value = {
                "at_hwm": at_hwm,
                "underwater": [_underwater_client("NN")],
            }
            mock_mgr.get_total_trader_credits.return_value = Decimal("0")

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/dashboard/trader-payment")

        assert response.status_code == 200
        data = response.json()
        assert "performance_fees" in data
        assert "total_due" in data
        assert len(data["performance_fees"]) == 1


# ---------------------------------------------------------------------------
# GET /api/v1/invoices/dashboard/hwm/{client_id}  (dashboards.py uncovered)
# ---------------------------------------------------------------------------


class TestDashboardHwm:
    def test_returns_hwm_state_when_found(self, _internal_dashboards: None) -> None:
        """GET /dashboard/hwm/{client_id} returns HWM state for known client."""
        mock_state = MagicMock()
        mock_state.model_dump.return_value = {
            "client_id": "PR",
            "hwm": 100000.0,
            "current_equity": 110000.0,
        }
        with patch("client_reporting_api.api.routes.invoices.dashboards.state_mgr") as mock_mgr:
            mock_mgr.get_hwm_state.return_value = mock_state

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/dashboard/hwm/PR")

        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "PR"

    def test_returns_404_when_no_hwm_state(self, _internal_dashboards: None) -> None:
        """GET /dashboard/hwm/{client_id} returns 404 when client not found."""
        with patch("client_reporting_api.api.routes.invoices.dashboards.state_mgr") as mock_mgr:
            mock_mgr.get_hwm_state.return_value = None

            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/invoices/dashboard/hwm/NONEXISTENT")

        assert response.status_code == 404
