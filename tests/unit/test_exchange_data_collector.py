"""Unit tests for exchange_data_collector module."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from client_reporting_api.core.exchange_data_collector import (
    ExchangeDataCollector,
    _create_exchange,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class TestCreateExchange:
    def test_binance_exchange(self) -> None:
        ex = _create_exchange("binance", "key", "secret", "")
        assert ex.id == "binance"

    def test_okx_exchange(self) -> None:
        ex = _create_exchange("okx", "key", "secret", "pass123")
        assert ex.id == "okx"

    def test_unknown_venue_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported venue"):
            _create_exchange("unknown_venue", "key", "secret", "")


class TestExchangeDataCollector:
    def test_get_exchange_no_config(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value=None,
        ):
            result = collector._get_exchange("UNKNOWN")
        assert result is None

    def test_get_exchange_no_secret_name(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value={"secret_name": "", "venue": "binance"},
        ):
            result = collector._get_exchange("MANUAL_CLIENT")
        assert result is None

    def test_get_exchange_missing_credentials(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value={
                "secret_name": "exec-test-binance",
                "venue": "binance",
            },
        ):
            result = collector._get_exchange("TEST")
        assert result is None

    def test_get_exchange_creates_and_caches(self) -> None:
        secrets = {
            "exec-test-binance": {
                "api_key": "k",
                "api_secret": "s",
                "passphrase": "",
            },
        }
        collector = ExchangeDataCollector(secrets=secrets)
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value={
                "secret_name": "exec-test-binance",
                "venue": "binance",
            },
        ):
            ex1 = collector._get_exchange("TEST")
            ex2 = collector._get_exchange("TEST")
        assert ex1 is not None
        assert ex1 is ex2  # cached

    def test_get_client_snapshot_no_exchange(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value=None,
        ):
            result = collector.get_client_snapshot("UNKNOWN")
        assert result is None

    def test_get_client_trades_no_exchange(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value=None,
        ):
            result = collector.get_client_trades("UNKNOWN")
        assert result == []

    def test_get_client_transfers_no_exchange(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        with patch(
            "client_reporting_api.core.exchange_data_collector.get_client_config",
            return_value=None,
        ):
            result = collector.get_client_transfers("UNKNOWN")
        assert result == []

    def test_get_all_active_client_ids(self) -> None:
        collector = ExchangeDataCollector(secrets={})
        mock_registry = {
            "clients": {
                "PR": {"is_active": True, "tranche": "managed"},
                "ET": {"is_active": True, "tranche": "managed"},
                "OLD": {"is_active": False, "tranche": "managed"},
                "FOF": {"is_active": True, "tranche": "fund_of_fund"},
            },
        }
        with patch(
            "client_reporting_api.core.exchange_data_collector.load_registry",
            return_value=mock_registry,
        ):
            ids = collector.get_all_active_client_ids()
        assert set(ids) == {"PR", "ET"}
        assert "OLD" not in ids  # inactive
        assert "FOF" not in ids  # fund_of_fund, not managed

    def test_parse_balances_skips_metadata_keys(self) -> None:
        balance_raw: dict[str, dict[str, float | None]] = {
            "total": {
                "USDT": 1000.0,
                "info": 0.0,
                "free": 0.0,
                "BTC": 0.5,
            },
            "free": {"USDT": 800.0, "BTC": 0.3},
            "used": {"USDT": 200.0, "BTC": 0.2},
        }
        total, free_usd, locked_usd, assets = ExchangeDataCollector._parse_balances(balance_raw)
        assert total == Decimal("1000.0") + Decimal("0.5")
        assert free_usd == Decimal("800.0") + Decimal("0.3")
        assert locked_usd == Decimal("200.0") + Decimal("0.2")
        assert len(assets) == 2
        symbols = {a.currency for a in assets}
        assert symbols == {"USDT", "BTC"}

    def test_parse_balances_skips_zero(self) -> None:
        balance_raw: dict[str, dict[str, float | None]] = {
            "total": {"USDT": 0.0, "ETH": None},
            "free": {},
            "used": {},
        }
        total, _, _, assets = ExchangeDataCollector._parse_balances(balance_raw)
        assert total == Decimal("0")
        assert len(assets) == 0
