"""Unit tests for /api/reporting/* routes.

Covers reporting/performance.py, reporting/reports_overview.py,
reporting/nav.py, and reporting/trades.py.
"""

from __future__ import annotations

import functools
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient
from unified_trading_library import AuthContext

import client_reporting_api.auth as _auth_module
from client_reporting_api.api.main import app
from client_reporting_api.api.routes import clients as _clients_mod
from client_reporting_api.api.routes import invoices as _inv_mod
from client_reporting_api.api.routes.reporting import nav as _nav_mod
from client_reporting_api.api.routes.reporting import performance as _rp_mod
from client_reporting_api.api.routes.reporting import reports_overview as _ro_mod
from client_reporting_api.api.routes.reporting import trades as _reporting_trades_mod

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

_EC = [
    {"date": "2026-01-01", "equity_usd": 100000},
    {"date": "2026-01-02", "equity_usd": 105000},
    {"date": "2026-01-03", "equity_usd": 102000},
]
_STATS = {
    "sharpe_ratio": 1.2,
    "max_drawdown_pct": 3.0,
    "simple_return_pct": 2.0,
    "total_return_pct": 2.0,
    "high_water_mark_twr": 105000.0,
    "twr_recovery_pct": 0.0,
    "twr_recovery_amount": 0.0,
    "notional_hwm": 105000.0,
    "notional_recovery": 0.0,
    "notional_recovery_pct": 0.0,
}
_PNL_DATA_WITH_TRANSFERS: dict[str, object] = {
    "current_equity": 102000,
    "trading_pnl": 2000,
    "transfers": [{"date": "2026-01-01", "amount": 10000}],
}
_REGISTRY: dict[str, object] = {
    "clients": {
        "PR": {"is_active": True, "currency": "USDT", "venue": "okx"},
        "NN": {"is_active": True, "currency": "USDT", "venue": "okx"},
    }
}

_FAKE_TRADE: dict[str, object] = {
    "id": "TRD-001",
    "symbol": "BTC/USDT:USDT",
    "side": "buy",
    "amount": 0.1,
    "price": 50000.0,
    "cost": 5000.0,
    "datetime": "2026-01-01T10:00:00Z",
    "order": "ORD-001",
    "timestamp": 1735725600,
    "fee": {"cost": 5.0, "currency": "USDT"},
    "info": {"fillPnl": "100.0"},
}


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    original_auth = _auth_module.DISABLE_AUTH
    _auth_module.DISABLE_AUTH = True

    _original_get_auth_config = _uci_auth._get_auth_config
    _uci_auth._get_auth_config.cache_clear()
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    cfg = _inv_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _auth_module.DISABLE_AUTH = original_auth
    _uci_auth._get_auth_config = _original_get_auth_config
    _uci_auth._get_auth_config.cache_clear()
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
    app.dependency_overrides.pop(_rp_mod._require_auth, None)
    app.dependency_overrides.pop(_ro_mod._require_auth, None)
    app.dependency_overrides.pop(_nav_mod._require_auth, None)
    app.dependency_overrides.pop(_reporting_trades_mod._require_auth, None)
    app.dependency_overrides.pop(_clients_mod._require_auth, None)


def _make_internal_fake(module_attr: object) -> None:
    async def _fake() -> AuthContext:
        return AuthContext(
            org_id="internal",
            user_id="admin",
            role="admin",
            subscription_tier="enterprise",
            display_name="Admin",
            is_internal=True,
        )

    app.dependency_overrides[module_attr] = _fake  # type: ignore[index]


@pytest.fixture
def _internal_rp() -> Generator[None]:
    _make_internal_fake(_rp_mod._require_auth)
    yield
    app.dependency_overrides.pop(_rp_mod._require_auth, None)


@pytest.fixture
def _internal_ro() -> Generator[None]:
    _make_internal_fake(_ro_mod._require_auth)
    yield
    app.dependency_overrides.pop(_ro_mod._require_auth, None)


@pytest.fixture
def _internal_nav() -> Generator[None]:
    _make_internal_fake(_nav_mod._require_auth)
    yield
    app.dependency_overrides.pop(_nav_mod._require_auth, None)


@pytest.fixture
def _internal_trades() -> Generator[None]:
    _make_internal_fake(_reporting_trades_mod._require_auth)
    yield
    app.dependency_overrides.pop(_reporting_trades_mod._require_auth, None)


@pytest.fixture
def _internal_clients_live() -> Generator[None]:
    """Override auth + switch clients module to live mode for testing live paths."""
    _make_internal_fake(_clients_mod._require_auth)
    orig_dm = _clients_mod._cloud_cfg.data_mode
    orig_mock = _clients_mod._cloud_cfg.cloud_mock_mode
    _clients_mod._cloud_cfg.data_mode = "real"  # type: ignore[misc]
    _clients_mod._cloud_cfg.cloud_mock_mode = False  # type: ignore[misc]
    yield
    _clients_mod._cloud_cfg.data_mode = orig_dm  # type: ignore[misc]
    _clients_mod._cloud_cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]
    app.dependency_overrides.pop(_clients_mod._require_auth, None)


# ---------------------------------------------------------------------------
# GET /api/reporting/performance/summary
# ---------------------------------------------------------------------------


class TestReportingPerformanceSummary:
    def test_returns_404_when_no_equity_curve(self, _internal_rp: None) -> None:
        with patch(
            "client_reporting_api.api.routes.reporting.performance.get_equity_curve",
            return_value=[],
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/reporting/performance/summary", params={"client_id": "XX"})
        assert response.status_code == 404

    def test_returns_summary_with_equity_curve(self, _internal_rp: None) -> None:
        mock_ta = MagicMock()
        mock_ta.total_trade_count = 42
        with (
            patch(
                "client_reporting_api.api.routes.reporting.performance.get_equity_curve",
                return_value=_EC,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.performance.compute_performance_stats",
                return_value=_STATS,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.performance.compute_pnl_series",
                return_value=_PNL_DATA_WITH_TRANSFERS,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.performance.compute_coin_breakdown",
                return_value=mock_ta,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.performance.get_backfill_summary",
                return_value={"equity_source": "backfill"},
            ),
            patch(
                "client_reporting_api.api.routes.reporting.performance._load_json",
                return_value=[],
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/performance/summary", params={"client_id": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "PR"
        assert "equity_curve" in data
        assert "monthly_returns" in data
        assert data["trade_count"] == 42
        assert data["equity_source"] == "backfill"


# ---------------------------------------------------------------------------
# GET /api/reporting/performance/coin-breakdown
# ---------------------------------------------------------------------------


class TestReportingCoinBreakdown:
    def test_returns_coin_breakdown(self, _internal_rp: None) -> None:
        mock_coin = MagicMock()
        mock_coin.symbol = "BTC"
        mock_coin.realized_pnl = 500.0
        mock_coin.trading_fees = 10.0
        mock_coin.funding_pnl = 5.0
        mock_coin.net_pnl = 495.0
        mock_coin.volume_usd = 50000.0
        mock_coin.trade_count = 10

        mock_ta = MagicMock()
        mock_ta.coins = [mock_coin]
        mock_ta.total_volume_usd = 50000.0

        with patch(
            "client_reporting_api.api.routes.reporting.performance.compute_coin_breakdown",
            return_value=mock_ta,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/api/reporting/performance/coin-breakdown", params={"client_id": "PR"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "PR"
        assert len(data["coins"]) == 1
        assert data["coins"][0]["symbol"] == "BTC"
        assert data["coins"][0]["allocation_pct"] == 1.0


# ---------------------------------------------------------------------------
# GET /api/reporting/performance/positions
# ---------------------------------------------------------------------------


class TestReportingPositions:
    def test_returns_empty_positions_when_no_file(self, _internal_rp: None) -> None:
        with patch(
            "client_reporting_api.api.routes.reporting.performance._load_json",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/api/reporting/performance/positions", params={"client_id": "PR"}
            )
        assert response.status_code == 200
        assert response.json()["positions"] == []

    def test_returns_projected_positions(self, _internal_rp: None) -> None:
        raw_positions = [
            {
                "symbol": "BTC-USDT-SWAP",
                "side": "long",
                "info": {
                    "instId": "BTC-USDT-SWAP",
                    "posSide": "long",
                    "pos": "1.0",
                    "avgPx": "50000",
                    "markPx": "51000",
                    "upl": "1000",
                    "lever": "10",
                    "liqPx": "45000",
                    "notionalUsd": "51000",
                },
            }
        ]
        with patch(
            "client_reporting_api.api.routes.reporting.performance._load_json",
            return_value=raw_positions,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/api/reporting/performance/positions", params={"client_id": "PR"}
            )
        assert response.status_code == 200
        pos = response.json()["positions"][0]
        assert pos["symbol"] == "BTC-USDT-SWAP"
        assert pos["unrealized_pnl"] == 1000.0


# ---------------------------------------------------------------------------
# GET /api/reporting/performance/balances
# ---------------------------------------------------------------------------


class TestReportingBalances:
    def test_returns_empty_when_no_balance_file(self, _internal_rp: None) -> None:
        with patch(
            "client_reporting_api.api.routes.reporting.performance._load_json",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/performance/balances", params={"client_id": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_equity_usd"] == 0
        assert data["balances"] == []

    def test_returns_balance_rows(self, _internal_rp: None) -> None:
        balance_data = {
            "USDT": {"free": "90000", "locked": "5000", "total": "95000", "usd_value": "95000"},
            "BTC": {"free": "0.5", "locked": "0", "total": "0.5", "usd_value": "25000"},
        }
        with patch(
            "client_reporting_api.api.routes.reporting.performance._load_json",
            return_value=balance_data,  # type: ignore[arg-type]
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/performance/balances", params={"client_id": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert data["total_equity_usd"] == 120000.0
        currencies = {b["currency"] for b in data["balances"]}
        assert "USDT" in currencies


# ---------------------------------------------------------------------------
# GET /api/reporting/reports
# ---------------------------------------------------------------------------


class TestReportsOverview:
    def test_returns_empty_overview_when_no_data(self, _internal_ro: None) -> None:
        """GET /reports returns empty aggregates when equity curve is empty for all clients."""
        with (
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.get_equity_curve",
                return_value=[],
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.compute_pnl_series",
                return_value={},
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.state_mgr"
            ) as mock_mgr,
            patch.object(_ro_mod, "_REPORTS_DIR", MagicMock(**{"exists.return_value": False})),
        ):
            mock_mgr.get_invoices.return_value = []
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/reports")
        assert response.status_code == 200
        data = response.json()
        assert data["portfolioSummary"] == []
        assert data["invoices"] == []

    def test_returns_full_overview_with_data(self, _internal_ro: None) -> None:
        """GET /reports covers portfolio, transfers, and generated-reports helpers."""
        mock_report = MagicMock()
        mock_report.stem = "PR_2026-01_report"

        mock_dir = MagicMock()
        mock_dir.exists.return_value = True
        mock_dir.glob.return_value = [mock_report]

        invoices = [
            {
                "invoice_id": "INV-001",
                "client_id": "PR",
                "amount": 1000.0,
                "paid": True,
                "date": "2026-01",
            }
        ]
        with (
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.get_equity_curve",
                return_value=_EC,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.compute_pnl_series",
                return_value=_PNL_DATA_WITH_TRANSFERS,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.compute_performance_stats",
                return_value=_STATS,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview._load_json",
                return_value=[],
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.load_registry",
                return_value=_REGISTRY,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.reports_overview.state_mgr"
            ) as mock_mgr,
            patch.object(_ro_mod, "_REPORTS_DIR", mock_dir),
        ):
            mock_mgr.get_invoices.return_value = invoices
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/reports", params={"client_ids": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["portfolioSummary"]) == 1
        assert data["portfolioSummary"][0]["clientId"] == "PR"
        assert len(data["invoices"]) == 1
        assert data["invoices"][0]["status"] == "paid"
        assert len(data["data"]) == 1
        assert len(data["accountBalances"]) == 1
        assert len(data["recentTransfers"]) == 1

    def test_returns_403_for_external_caller(self) -> None:
        async def _external_fake() -> AuthContext:
            return AuthContext(
                org_id="client-x",
                user_id="user1",
                role="user",
                subscription_tier="basic",
                display_name="User",
                is_internal=False,
            )

        app.dependency_overrides[_ro_mod._require_auth] = _external_fake
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/reporting/reports")
        finally:
            app.dependency_overrides.pop(_ro_mod._require_auth, None)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/reporting/nav
# ---------------------------------------------------------------------------


class TestReportingNav:
    def test_returns_nav_with_empty_registry(self, _internal_nav: None) -> None:
        """GET /nav returns empty investors/flows when registry has no active clients."""
        with (
            patch(
                "client_reporting_api.api.routes.reporting.nav.get_equity_curve",
                return_value=[],
            ),
            patch(
                "client_reporting_api.api.routes.reporting.nav.compute_pnl_series",
                return_value={},
            ),
            patch(
                "client_reporting_api.api.routes.reporting.nav.load_registry",
                return_value={"clients": {}},
            ),
            patch("client_reporting_api.api.routes.reporting.nav.state_mgr") as mock_mgr,
        ):
            mock_mgr.get_invoices.return_value = []
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/nav")
        assert response.status_code == 200
        data = response.json()
        assert data["current_nav"] == 0.0
        assert data["investors"] == []

    def test_returns_nav_with_active_clients(self, _internal_nav: None) -> None:
        """GET /nav covers investor aggregation, hourly NAV series, and capital flows helpers."""
        with (
            patch(
                "client_reporting_api.api.routes.reporting.nav.get_equity_curve",
                return_value=_EC,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.nav.compute_pnl_series",
                return_value=_PNL_DATA_WITH_TRANSFERS,
            ),
            patch(
                "client_reporting_api.api.routes.reporting.nav.load_registry",
                return_value=_REGISTRY,
            ),
            patch("client_reporting_api.api.routes.reporting.nav.state_mgr") as mock_mgr,
        ):
            mock_mgr.get_invoices.return_value = []
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/nav", params={"client_ids": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert data["current_nav"] > 0
        assert len(data["investors"]) == 1
        assert data["investors"][0]["class"] == "USDT"
        assert len(data["hourly_nav"]) > 0
        assert len(data["capital_flows"]) == 1

    def test_returns_403_for_external_caller(self) -> None:
        async def _external_fake() -> AuthContext:
            return AuthContext(
                org_id="client-x",
                user_id="user1",
                role="user",
                subscription_tier="basic",
                display_name="User",
                is_internal=False,
            )

        app.dependency_overrides[_nav_mod._require_auth] = _external_fake
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/reporting/nav")
        finally:
            app.dependency_overrides.pop(_nav_mod._require_auth, None)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/reporting/trades
# ---------------------------------------------------------------------------


class TestReportingTrades:
    def test_returns_empty_trades_when_no_file(self, _internal_trades: None) -> None:
        """GET /trades returns empty list when trades.json is absent."""
        with patch(
            "client_reporting_api.api.routes.reporting.trades._load_json",
            return_value=None,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/trades", params={"client_id": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert data["client_id"] == "PR"
        assert data["trades"] == []
        assert data["total"] == 0

    def test_returns_projected_trades_with_aggregates(self, _internal_trades: None) -> None:
        """GET /trades covers _project_trade and aggregation loop body."""
        with patch(
            "client_reporting_api.api.routes.reporting.trades._load_json",
            return_value=[_FAKE_TRADE],
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/reporting/trades", params={"client_id": "PR"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        trade = data["trades"][0]
        assert trade["trade_id"] == "TRD-001"
        assert trade["symbol"] == "BTC/USDT:USDT"
        assert trade["side"] == "BUY"
        assert trade["fee"] == 5.0
        assert trade["realized_pnl"] == 100.0
        assert data["aggregates"]["total_volume_usd"] == 5000.0

    def test_filter_by_symbol(self, _internal_trades: None) -> None:
        """GET /trades?symbol=ETH filters out non-matching trades."""
        trades = [
            {**_FAKE_TRADE, "symbol": "BTC/USDT:USDT"},
            {**_FAKE_TRADE, "id": "TRD-002", "symbol": "ETH/USDT:USDT"},
        ]
        with patch(
            "client_reporting_api.api.routes.reporting.trades._load_json",
            return_value=trades,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/api/reporting/trades", params={"client_id": "PR", "symbol": "ETH"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert "ETH" in data["trades"][0]["symbol"]

    def test_filter_by_side(self, _internal_trades: None) -> None:
        """GET /trades?side=sell filters out buy trades."""
        trades = [
            {**_FAKE_TRADE, "side": "buy"},
            {**_FAKE_TRADE, "id": "TRD-002", "side": "sell"},
        ]
        with patch(
            "client_reporting_api.api.routes.reporting.trades._load_json",
            return_value=trades,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get(
                "/api/reporting/trades", params={"client_id": "PR", "side": "sell"}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["trades"][0]["side"] == "SELL"


# ---------------------------------------------------------------------------
# GET /api/v1/clients  and  GET /api/v1/clients/{client_id}  — live mode
# ---------------------------------------------------------------------------

_FULL_REGISTRY: dict[str, object] = {
    "clients": {
        "PR": {
            "full_name": "Prism Capital",
            "venue": "okx",
            "currency": "USDT",
            "tranche": "A",
            "is_active": True,
            "is_underwater": False,
            "organisation_id": "ORG-001",
            "strategy_id": "STRAT-001",
        }
    },
    "organisations": {
        "ORG-001": {"name": "Prism Organisation", "type": "institutional"},
    },
    "strategies": {
        "STRAT-001": {"name": "Delta Neutral", "description": "Long/short neutral strategy"},
    },
}


class TestClientsLiveMode:
    def test_list_clients_returns_structured_data(self, _internal_clients_live: None) -> None:
        """GET /api/v1/clients in live mode covers helper functions."""
        with patch(
            "client_reporting_api.api.routes.clients.load_registry",
            return_value=_FULL_REGISTRY,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/clients")
        assert response.status_code == 200
        data = response.json()
        assert len(data["clients"]) == 1
        assert data["clients"][0]["id"] == "PR"
        assert data["clients"][0]["organisation_name"] == "Prism Organisation"
        assert data["clients"][0]["strategy_name"] == "Delta Neutral"
        assert len(data["organisations"]) == 1
        assert len(data["strategies"]) == 1

    def test_list_clients_filter_by_organisation(self, _internal_clients_live: None) -> None:
        """GET /api/v1/clients?organisation_id=X filters by org."""
        with patch(
            "client_reporting_api.api.routes.clients.load_registry",
            return_value=_FULL_REGISTRY,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/clients", params={"organisation_id": "ORG-999"})
        assert response.status_code == 200
        assert response.json()["clients"] == []

    def test_get_client_live_returns_entry(self, _internal_clients_live: None) -> None:
        """GET /api/v1/clients/{client_id} in live mode covers _build_client_entry."""
        with patch(
            "client_reporting_api.api.routes.clients.load_registry",
            return_value=_FULL_REGISTRY,
        ):
            client = TestClient(app, raise_server_exceptions=True)
            response = client.get("/api/v1/clients/PR")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "PR"
        assert data["organisation_name"] == "Prism Organisation"
        assert data["strategy_name"] == "Delta Neutral"

    def test_get_client_live_returns_404_when_missing(self, _internal_clients_live: None) -> None:
        """GET /api/v1/clients/{client_id} returns 404 when client not in live registry."""
        with patch(
            "client_reporting_api.api.routes.clients.load_registry",
            return_value=_FULL_REGISTRY,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/v1/clients/NONEXISTENT")
        assert response.status_code == 404
