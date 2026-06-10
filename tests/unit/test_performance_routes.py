"""Unit tests for performance, trades, clients, and exports routes."""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from client_reporting_api.api.main import app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _enable_mock_mode() -> Generator[None]:
    """Enable mock mode and disable auth for route tests.

    The route-level FastAPI auth dependency built by
    ``unified_trading_library.create_api_auth`` instantiates a fresh
    ``UnifiedCloudConfig`` per request, so we must set the underlying env
    vars (DISABLE_AUTH / DATA_MODE).
    """
    original_env = {
        "DISABLE_AUTH": os.environ.get("DISABLE_AUTH"),
        "DATA_MODE": os.environ.get("DATA_MODE"),
        "CLOUD_MOCK_MODE": os.environ.get("CLOUD_MOCK_MODE"),
    }
    os.environ["DISABLE_AUTH"] = "true"
    os.environ["DATA_MODE"] = "mock"
    os.environ["CLOUD_MOCK_MODE"] = "true"

    # UTL caches the auth config tuple via @lru_cache. Clear the cache so the
    # new env vars are picked up by the route-level dependency.
    import importlib

    _utl_api_auth = importlib.import_module("unified_trading_library.cloud_interface.api_auth")
    _utl_api_auth._get_auth_config.cache_clear()

    from client_reporting_api.api.routes import (
        clients as _clients_mod,
    )
    from client_reporting_api.api.routes import (
        exports as _exports_mod,
    )
    from client_reporting_api.api.routes import (
        performance as _perf_mod,
    )
    from client_reporting_api.api.routes import (
        trades as _trades_mod,
    )

    mods = [_clients_mod, _exports_mod, _perf_mod, _trades_mod]
    originals = []
    for mod in mods:
        cfg = mod._cloud_cfg
        originals.append((cfg, cfg.data_mode, cfg.cloud_mock_mode))
        cfg.data_mode = "mock"  # type: ignore[misc]
        cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    for cfg, dm, cm in originals:
        cfg.data_mode = dm  # type: ignore[misc]
        cfg.cloud_mock_mode = cm  # type: ignore[misc]
    for key, val in original_env.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    _utl_api_auth._get_auth_config.cache_clear()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── Clients ──────────────────────────────────────────────────────────────────


class TestClients:
    def test_list_clients(self, client: TestClient) -> None:
        resp = client.get("/api/v1/clients")
        assert resp.status_code == 200
        data = resp.json()
        # Endpoint returns {"clients": [...], "organisations": [...], "strategies": [...]}
        assert isinstance(data, dict)
        clients_list = data["clients"]
        assert isinstance(clients_list, list)
        assert len(clients_list) >= 5
        assert all("id" in c and "name" in c for c in clients_list)

    def test_get_client_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/clients/PR")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "PR"
        assert data["name"] == "Prism Capital"

    def test_get_client_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/clients/NONEXISTENT")
        assert resp.status_code == 404


# ── Performance ──────────────────────────────────────────────────────────────


class TestPerformance:
    def test_performance_summary(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/performance/summary",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == "PR"
        assert "equity_curve" in data
        assert "monthly_returns" in data
        assert "stats" in data
        assert isinstance(data["equity_curve"], list)
        assert len(data["equity_curve"]) > 0
        assert "sharpe_ratio" in data["stats"]

    def test_open_positions(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/performance/positions",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == "PR"
        assert isinstance(data["positions"], list)
        assert len(data["positions"]) >= 3
        pos = data["positions"][0]
        assert "symbol" in pos
        assert "unrealized_pnl" in pos

    def test_balance_breakdown(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/performance/balances",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == "PR"
        assert "total_equity_usd" in data
        assert isinstance(data["balances"], list)

    def test_coin_breakdown(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/performance/coin-breakdown",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == "PR"
        assert isinstance(data["coins"], list)
        assert len(data["coins"]) >= 3
        coin = data["coins"][0]
        assert "symbol" in coin
        assert "total_pnl" in coin
        assert "allocation_pct" in coin


# ── Trades ───────────────────────────────────────────────────────────────────


class TestTrades:
    def test_trade_history_default(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/trades",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["client_id"] == "PR"
        assert isinstance(data["trades"], list)
        assert data["total"] >= 1
        assert "aggregates" in data
        agg = data["aggregates"]
        assert "total_trades" in agg
        assert "total_volume_usd" in agg

    def test_trade_history_filter_symbol(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/trades",
            params={"client_id": "PR", "symbol": "BTC/USDT"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for t in data["trades"]:
            assert t["symbol"] == "BTC/USDT"

    def test_trade_history_filter_side(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/trades",
            params={"client_id": "PR", "side": "SELL"},
        )
        assert resp.status_code == 200
        data = resp.json()
        for t in data["trades"]:
            assert t["side"] == "SELL"

    def test_trade_history_pagination(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/trades",
            params={"client_id": "PR", "limit": 2, "offset": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert len(data["trades"]) <= 2


# ── Exports ──────────────────────────────────────────────────────────────────


class TestExports:
    def test_export_trades_csv(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/exports/trades",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 2  # header + at least one row
        assert "trade_id" in lines[0]

    def test_export_daily_summary_csv(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/exports/daily-summary",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        lines = resp.text.strip().split("\n")
        assert "year" in lines[0]
        assert "return_pct" in lines[0]

    def test_export_hourly_snapshots_csv(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/exports/hourly-snapshots",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        lines = resp.text.strip().split("\n")
        assert "timestamp" in lines[0]
        assert "equity_usd" in lines[0]

    def test_export_coin_breakdown_csv(self, client: TestClient) -> None:
        resp = client.get(
            "/api/v1/exports/coin-breakdown",
            params={"client_id": "PR"},
        )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        lines = resp.text.strip().split("\n")
        assert "symbol" in lines[0]
        assert len(lines) >= 5  # header + 4 coins


# ── Mock data module ─────────────────────────────────────────────────────────


class TestMockPerformanceData:
    def test_generate_equity_curve_deterministic(self) -> None:
        from client_reporting_api.core.mock_performance_data import (
            get_mock_performance_summary,
        )

        s1 = get_mock_performance_summary("PR")
        s2 = get_mock_performance_summary("PR")
        assert s1["client_id"] == "PR"
        assert s1["equity_curve"] == s2["equity_curve"]

    def test_different_clients_get_different_params(self) -> None:
        from client_reporting_api.core.mock_performance_data import (
            get_mock_performance_summary,
        )

        pr = get_mock_performance_summary("PR")
        et = get_mock_performance_summary("ET")
        assert pr["current_equity_usd"] != et["current_equity_usd"]

    def test_unknown_client_uses_defaults(self) -> None:
        from client_reporting_api.core.mock_performance_data import (
            get_mock_performance_summary,
        )

        s = get_mock_performance_summary("UNKNOWN")
        assert s["client_id"] == "UNKNOWN"
        assert len(s["equity_curve"]) > 0  # type: ignore[arg-type]
