"""Real-vs-fixture honesty tests for exports.py CSV routes.

2026-08-21 CTO handoff P2 fix: ``GET /api/v1/exports/{trades,coin-breakdown,
daily-summary,hourly-snapshots}`` used to return hardcoded fixture data
unconditionally, regardless of ``CLOUD_MOCK_MODE``. These tests prove: mock
mode still returns the fixture (unchanged behaviour), real mode reuses the
real per-route reader, and an empty real result is an explicit "No data"
body rather than a silent fixture.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import unified_trading_library.cloud_interface.api_auth as _uci_auth
from fastapi.testclient import TestClient
from unified_api_contracts.internal import TradeRecord, TradeSide, TradeType

from client_reporting_api.api.main import app
from client_reporting_api.api.routes import exports as _exports_mod
from client_reporting_api.core.trade_analytics import CoinBreakdown, TradeAnalytics

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def _mock_mode() -> Generator[None]:
    """Disable auth (mock-admin bypass) and force CLOUD_MOCK_MODE=true."""
    import functools

    _uci_auth._get_auth_config.cache_clear()
    orig_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, True, None))

    cfg = _exports_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "mock"  # type: ignore[misc]
    cfg.cloud_mock_mode = True  # type: ignore[misc]

    yield

    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(orig_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


@pytest.fixture
def _real_mode() -> Generator[None]:
    """Disable auth (mock-admin bypass) but force CLOUD_MOCK_MODE=false.

    ``is_internal=True`` from the disabled-auth short-circuit still passes
    ``enforce_entitlement`` regardless of ``client_id`` — these tests exist
    to prove the real-vs-fixture data path, not entitlement (covered in
    test_entitlement_backfill.py).
    """
    import functools

    _uci_auth._get_auth_config.cache_clear()
    orig_fn = _uci_auth._get_auth_config.__wrapped__
    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(lambda: (True, False, None))

    cfg = _exports_mod._cloud_cfg
    orig_data_mode = cfg.data_mode
    orig_mock = cfg.cloud_mock_mode
    cfg.data_mode = "real"  # type: ignore[misc]
    cfg.cloud_mock_mode = False  # type: ignore[misc]

    yield

    _uci_auth._get_auth_config = functools.lru_cache(maxsize=1)(orig_fn)
    cfg.data_mode = orig_data_mode  # type: ignore[misc]
    cfg.cloud_mock_mode = orig_mock  # type: ignore[misc]


@pytest.fixture
def http() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


def _csv_header(body: str) -> list[str]:
    return next(csv.reader(io.StringIO(body)))


class TestTradesCsvHonesty:
    def test_mock_mode_returns_fixture(self, http: TestClient, _mock_mode: None) -> None:
        resp = http.get("/api/v1/exports/trades", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert "trade_id" in resp.text

    def test_real_mode_uses_ledger_fills_not_fixture(self, http: TestClient, _real_mode: None) -> None:
        real_row = {
            "trade_id": "REAL-1",
            "venue": "real-venue",
            "symbol": "REAL-SYM",
            "side": "buy",
            "quantity": 1.0,
            "price": 2.0,
            "fee": 0.1,
            "fee_currency": "USDC",
            "realized_pnl": 0.0,
            "timestamp": "2026-08-21T00:00:00+00:00",
            "order_id": "ord-1",
            "trade_type": "paper",
            "notional_usd": 2.0,
        }
        with patch.object(_exports_mod, "_ledger_run_trades", return_value=("run-1", [real_row])):
            resp = http.get("/api/v1/exports/trades", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert "REAL-1" in resp.text
        assert "REAL-SYM" in resp.text

    def test_real_mode_uses_collector_when_no_ledger_and_no_backfill(self, http: TestClient, _real_mode: None) -> None:
        """The rarer third case: live-collector-only state (no ledger run, no backfill history).

        Reuses ``get_collector().get_client_trades(...)`` exactly like
        ``trades.py::get_trade_history``'s own collector fallback, rather than
        reimplementing it (2026-08-21 P3 follow-up to the P2 fixture-honesty fix).
        """
        collector_row = TradeRecord(
            trade_id="COLLECTOR-1",
            client_id="client-A",
            venue="okx",
            symbol="COLLECTOR-SYM",
            side=TradeSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("2"),
            fee=Decimal("0.1"),
            fee_currency="USDT",
            realized_pnl=Decimal("0"),
            timestamp=datetime(2026, 8, 21, tzinfo=UTC),
            order_id="ord-collector-1",
            trade_type=TradeType.MARKET,
            notional_usd=Decimal("2"),
        )
        fake_collector = MagicMock()
        fake_collector.get_client_trades.return_value = [collector_row]
        with (
            patch.object(_exports_mod, "_ledger_run_trades", return_value=(None, [])),
            patch.object(_exports_mod, "_backfill_trades", return_value=[]),
            patch.object(_exports_mod, "get_collector", return_value=fake_collector),
        ):
            resp = http.get("/api/v1/exports/trades", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert "COLLECTOR-1" in resp.text
        assert "COLLECTOR-SYM" in resp.text

    def test_real_mode_empty_returns_explicit_no_data(self, http: TestClient, _real_mode: None) -> None:
        """All three sources empty (ledger, backfill, AND collector) -> explicit "No data"."""
        empty_collector = MagicMock()
        empty_collector.get_client_trades.return_value = []
        with (
            patch.object(_exports_mod, "_ledger_run_trades", return_value=(None, [])),
            patch.object(_exports_mod, "_backfill_trades", return_value=[]),
            patch.object(_exports_mod, "get_collector", return_value=empty_collector),
        ):
            resp = http.get("/api/v1/exports/trades", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert resp.text.strip() == "No data"


class TestCoinBreakdownCsvHonesty:
    def test_mock_mode_returns_fixture_columns(self, http: TestClient, _mock_mode: None) -> None:
        resp = http.get("/api/v1/exports/coin-breakdown", params={"client_id": "client-A"})
        assert resp.status_code == 200
        header = _csv_header(resp.text)
        assert "avg_entry_price" in header  # mock-only column

    def test_real_mode_uses_real_engine_columns(self, http: TestClient, _real_mode: None) -> None:
        analytics = TradeAnalytics(
            client_id="client-A",
            coins=[CoinBreakdown(symbol="BTC", realized_pnl=100.0, trade_count=3)],
        )
        with patch.object(_exports_mod, "compute_coin_breakdown", return_value=analytics):
            resp = http.get("/api/v1/exports/coin-breakdown", params={"client_id": "client-A"})
        assert resp.status_code == 200
        header = _csv_header(resp.text)
        assert "avg_entry_price" not in header  # not a real column — no fixture padding
        assert "trading_fees" in header
        assert "BTC" in resp.text

    def test_real_mode_empty_returns_explicit_no_data(self, http: TestClient, _real_mode: None) -> None:
        analytics = TradeAnalytics(client_id="client-A", coins=[])
        with patch.object(_exports_mod, "compute_coin_breakdown", return_value=analytics):
            resp = http.get("/api/v1/exports/coin-breakdown", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert resp.text.strip() == "No data"


class TestDailySummaryCsvHonesty:
    def test_mock_mode_returns_fixture(self, http: TestClient, _mock_mode: None) -> None:
        resp = http.get("/api/v1/exports/daily-summary", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert resp.status_code == 200

    def test_real_mode_uses_computed_monthly_returns(self, http: TestClient, _real_mode: None) -> None:
        fake_curve = [{"date": "2026-06-01", "equity_usdt": 100.0}, {"date": "2026-07-01", "equity_usdt": 110.0}]
        monthly_return = [{"month": "2026-07", "return_pct": 10.0}]
        with (
            patch.object(_exports_mod, "get_equity_curve", return_value=fake_curve),
            patch.object(_exports_mod, "compute_monthly_returns", return_value=monthly_return),
        ):
            resp = http.get("/api/v1/exports/daily-summary", params={"client_id": "client-A"})
        assert resp.status_code == 200
        header = _csv_header(resp.text)
        assert header == ["month", "return_pct"]
        assert "2026-07" in resp.text

    def test_real_mode_empty_returns_explicit_no_data(self, http: TestClient, _real_mode: None) -> None:
        with patch.object(_exports_mod, "get_equity_curve", return_value=[]):
            resp = http.get("/api/v1/exports/daily-summary", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert resp.text.strip() == "No data"


class TestHourlySnapshotsCsvHonesty:
    def test_mock_mode_returns_fixture(self, http: TestClient, _mock_mode: None) -> None:
        resp = http.get("/api/v1/exports/hourly-snapshots", params={"client_id": "client-A"})
        assert resp.status_code == 200

    def test_real_mode_returns_explicit_no_data(self, http: TestClient, _real_mode: None) -> None:
        # No real hourly-granularity equity store exists — real mode must
        # never relabel daily data as hourly.
        resp = http.get("/api/v1/exports/hourly-snapshots", params={"client_id": "client-A"})
        assert resp.status_code == 200
        assert "not yet captured" in resp.text
