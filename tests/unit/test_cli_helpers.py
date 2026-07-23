"""Coverage tests for ``client_reporting_api.cli`` helpers.

The CLI module is largely composed of small pure helpers that wrap CCXT,
GCS, and the credentials registry. These tests mock those external
dependencies so we can drive each helper end-to-end without network or
secret-manager access.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import ccxt
import pytest

from client_reporting_api import cli

# ── Pure helpers ───────────────────────────────────────────────────────────


class TestDecimalEncoder:
    def test_encodes_decimal_as_float(self) -> None:
        encoder = cli.DecimalEncoder()
        assert encoder.default(Decimal("1.25")) == 1.25

    def test_encodes_datetime_as_iso(self) -> None:
        encoder = cli.DecimalEncoder()
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert encoder.default(dt) == "2026-01-01T00:00:00+00:00"

    def test_falls_through_for_unknown(self) -> None:
        with pytest.raises(TypeError):
            cli.DecimalEncoder().default(object())


class TestGetActiveClients:
    def test_filters_inactive_and_unmanaged(self) -> None:
        registry: dict[str, dict[str, object]] = {
            "A": {"is_active": True, "tranche": "managed", "secret_names": {"api_key": "exec-a-okx-api-key"}},
            "B": {"is_active": False, "tranche": "managed", "secret_names": {"api_key": "exec-b-okx-api-key"}},
            "C": {"is_active": True, "tranche": "fund_of_fund", "secret_names": {"api_key": "exec-c-okx-api-key"}},
            "D": {"is_active": True, "tranche": "managed", "secret_names": {}},
        }
        active = cli._get_active_clients(registry)
        assert [cid for cid, _ in active] == ["A"]

    def test_filter_by_specific_client(self) -> None:
        registry: dict[str, dict[str, object]] = {
            "A": {"is_active": True, "tranche": "managed", "secret_names": {"api_key": "exec-a-okx-api-key"}},
            "B": {"is_active": True, "tranche": "managed", "secret_names": {"api_key": "exec-b-okx-api-key"}},
        }
        active = cli._get_active_clients(registry, client_filter="B")
        assert [cid for cid, _ in active] == ["B"]


class TestCreateExchange:
    def test_unsupported_venue_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported venue"):
            cli._create_exchange("bybit", "k", "s", "")

    def test_okx_includes_passphrase(self) -> None:
        ex = cli._create_exchange("okx", "k", "s", "phrase")
        assert ex.password == "phrase"

    def test_binance_sets_swap_default(self) -> None:
        ex = cli._create_exchange("binance", "k", "s", "")
        assert ex.options.get("defaultType") == "swap"


class TestGetUsdPrice:
    def test_stablecoin_returns_one(self) -> None:
        ex = MagicMock()
        assert cli._get_usd_price(ex, "USDT") == 1.0
        ex.fetch_ticker.assert_not_called()

    def test_uses_first_successful_ticker(self) -> None:
        ex = MagicMock()
        ex.fetch_ticker.return_value = {"last": 50000.0}
        assert cli._get_usd_price(ex, "BTC") == 50000.0

    def test_skips_failed_quotes(self) -> None:
        ex = MagicMock()
        ex.fetch_ticker.side_effect = [ccxt.BaseError("boom"), {"last": 0, "close": 1234.5}]
        assert cli._get_usd_price(ex, "WEIRD") == 1234.5

    def test_returns_zero_when_all_fail(self) -> None:
        ex = MagicMock()
        ex.fetch_ticker.side_effect = ccxt.BaseError("boom")
        assert cli._get_usd_price(ex, "WEIRD") == 0.0


class TestFetchCredentials:
    def test_returns_none_when_secret_missing(self) -> None:
        with patch("client_reporting_api.cli.shared._get_secret", side_effect=RuntimeError("no")):
            assert cli._fetch_credentials("PR", "okx") is None

    def test_returns_none_when_secret_returns_none(self) -> None:
        # UTL's get_secret returns None (not RuntimeError) on a missing secret.
        # A missing key must yield a clean absence, never a None-filled creds dict.
        with patch("client_reporting_api.cli.shared._get_secret", return_value=None):
            assert cli._fetch_credentials("PR", "binance") is None

    def test_returns_none_when_only_secret_missing(self) -> None:
        # api-key present but api-secret missing (None) must still be absence.
        seq = iter(["the-key", None])
        with patch("client_reporting_api.cli.shared._get_secret", side_effect=lambda _name: next(seq)):
            assert cli._fetch_credentials("PR", "binance") is None

    def test_returns_dict_with_passphrase_for_okx(self) -> None:
        seq = iter(["the-key", "the-secret", "the-passphrase"])
        with patch("client_reporting_api.cli.shared._get_secret", side_effect=lambda _name: next(seq)):
            creds = cli._fetch_credentials("PR", "okx")
        assert creds == {
            "api_key": "the-key",
            "api_secret": "the-secret",
            "passphrase": "the-passphrase",
        }

    def test_passphrase_empty_for_binance(self) -> None:
        seq = iter(["k", "s"])
        with patch("client_reporting_api.cli.shared._get_secret", side_effect=lambda _name: next(seq)):
            creds = cli._fetch_credentials("PR", "binance")
        assert creds == {"api_key": "k", "api_secret": "s", "passphrase": ""}


# ── Onboard helpers ────────────────────────────────────────────────────────


class TestOnboardHelpers:
    def test_validate_credentials_returns_total_usd(self) -> None:
        ex = MagicMock()
        ex.fetch_balance.return_value = {
            "total": {"USDT": 1000.0, "BTC": 0.5, "info": "x", "free": "x"},
        }
        with (
            patch("client_reporting_api.cli.onboard_command._create_exchange", return_value=ex),
            patch(
                "client_reporting_api.cli.onboard_command._get_usd_price",
                side_effect=lambda _e, c: 1.0 if c == "USDT" else 50000.0,
            ),
        ):
            total = cli._validate_onboard_credentials("okx", "k", "s", "p")
        assert total == 1000.0 + 0.5 * 50000.0

    def test_validate_credentials_returns_none_on_ccxt_error(self) -> None:
        with patch(
            "client_reporting_api.cli.onboard_command._create_exchange",
            side_effect=ccxt.BaseError("boom"),
        ):
            assert cli._validate_onboard_credentials("okx", "k", "s", "p") is None

    def test_store_secrets_swallows_runtime_error(self) -> None:
        """The best-effort path must not raise when create_secret_if_not_exists blows up."""

        def boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("SM down")

        with patch(
            "client_reporting_api.cli.onboard_command.create_secret_if_not_exists",
            side_effect=boom,
        ):
            cli._store_onboard_secrets("base", "okx", "k", "s", "p")  # must not raise

    def test_register_new_client_inserts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        registry = {"organisations": {}, "clients": {}, "strategies": {}}
        registry_path = tmp_path / "registry.yaml"
        import yaml

        registry_path.write_text(yaml.safe_dump(registry))
        from client_reporting_api.cli import onboard_command, shared

        monkeypatch.setattr(shared, "REGISTRY_PATH", registry_path)
        monkeypatch.setattr(onboard_command, "REGISTRY_PATH", registry_path)
        cli._register_new_client("NEW", "Newco", "newco", "strategy_x", "USDT", "okx", "exec-new-okx", 50000.0)
        loaded = yaml.safe_load(registry_path.read_text())
        assert "NEW" in loaded["clients"]
        assert "newco" in loaded["organisations"]
        assert loaded["clients"]["NEW"]["initial_deposit_usd"] == 50000.0


# ── Update helpers ────────────────────────────────────────────────────────


class TestParseBalances:
    def test_separates_usdt_and_btc(self) -> None:
        ex = MagicMock()
        with patch("client_reporting_api.cli.equity_update._get_usd_price", return_value=50000.0):
            usdt, btc, btc_price = cli._parse_balances(ex, {"USDT": 100.0, "BTC": 0.25, "info": "x"})
        assert usdt == 100.0
        assert btc == 0.25
        assert btc_price == 50000.0


class TestBuildEquityPoint:
    def test_minimal_usdt_only(self) -> None:
        point = cli._build_equity_point("2026-01-01", 100.0, 100.0, 0.0, 0.0)
        assert point["date"] == "2026-01-01"
        assert point["usdt_balance"] == 100.0
        assert "btc_balance" not in point

    def test_btc_adds_btc_fields(self) -> None:
        point = cli._build_equity_point("2026-01-01", 1100.0, 100.0, 0.02, 50000.0)
        assert point["btc_balance"] == 0.02
        assert point["btc_price_usd"] == 50000.0


class TestDetectTransfer:
    def test_returns_zero_when_change_under_threshold(self) -> None:
        ex = MagicMock()
        prev = {"equity_usd": 100000.0, "btc_balance": 0.0, "btc_price_usd": 0.0}
        with patch("client_reporting_api.cli.equity_update._fetch_pnl_since", return_value=99.0):
            transfer = cli._detect_transfer(
                ex,
                "okx",
                "2026-01-01",
                "2026-01-02",
                prev,
                total_usd=100100.0,
                btc_bal=0.0,
                btc_price=0.0,
            )
        assert transfer == 0.0

    def test_returns_value_when_above_threshold(self) -> None:
        ex = MagicMock()
        prev = {"equity_usd": 50000.0, "btc_balance": 0.0, "btc_price_usd": 0.0}
        with patch("client_reporting_api.cli.equity_update._fetch_pnl_since", return_value=0.0):
            transfer = cli._detect_transfer(
                ex,
                "okx",
                "2026-01-01",
                "2026-01-02",
                prev,
                total_usd=60000.0,
                btc_bal=0.0,
                btc_price=0.0,
            )
        assert transfer == 10000.0


class TestFillGapDays:
    def test_fills_carry_forward(self) -> None:
        curve: list[dict[str, str | float]] = [{"date": "2026-01-01", "equity_usd": 100.0}]
        last_dt = datetime(2026, 1, 1, tzinfo=UTC)
        today_dt = datetime(2026, 1, 4, tzinfo=UTC)
        cli._fill_gap_days(curve, last_dt, today_dt)
        # Should add 2026-01-02 and 2026-01-03 (today_dt itself is not filled here)
        assert [str(p["date"]) for p in curve] == ["2026-01-01", "2026-01-02", "2026-01-03"]
        for p in curve:
            assert p["equity_usd"] == 100.0


class TestPersistAndUpdate:
    def test_persist_balance_snapshot_writes_file(self, tmp_path: Path) -> None:
        balance_raw = {"free": {"USDT": 1.0}, "used": {"USDT": 0.0}, "total": {"USDT": 1.0}}
        assets = cli._persist_balance_snapshot(tmp_path, balance_raw, balance_raw["total"])
        assert "USDT" in assets
        assert (tmp_path / "balance.json").exists()

    def test_persist_positions_logs_on_failure(self, tmp_path: Path) -> None:
        ex = MagicMock()
        ex.fetch_positions.side_effect = ccxt.BaseError("boom")
        cli._persist_positions(tmp_path, ex, "PR")  # must not raise

    def test_persist_positions_writes_file(self, tmp_path: Path) -> None:
        ex = MagicMock()
        ex.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT",
                "side": "long",
                "contracts": 1.0,
                "entryPrice": 50000.0,
                "markPrice": 51000.0,
                "unrealizedPnl": 1000.0,
                "leverage": 10.0,
                "notional": 50000.0,
                "liquidationPrice": None,
            }
        ]
        cli._persist_positions(tmp_path, ex, "PR")
        data = json.loads((tmp_path / "positions.json").read_text())
        assert len(data) == 1
        assert data[0]["symbol"] == "BTC/USDT"

    def test_update_summary_creates_or_updates(self, tmp_path: Path) -> None:
        path = tmp_path / "summary.json"
        cli._update_summary(path, [{"date": "2026-01-01"}], {"USDT": {"total": 1.0}})
        loaded = json.loads(path.read_text())
        assert loaded["equity_curve_days"] == 1


# ── Order/trade helpers ───────────────────────────────────────────────────


class TestOrderHelpers:
    def test_load_existing_orders_empty_when_missing(self, tmp_path: Path) -> None:
        rows, ids = cli._load_existing_orders(tmp_path / "orders.json")
        assert rows == []
        assert ids == set()

    def test_load_existing_orders_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "orders.json"
        path.write_text(json.dumps([{"id": "abc", "symbol": "BTC/USDT"}]))
        rows, ids = cli._load_existing_orders(path)
        assert ids == {"abc"}
        assert len(rows) == 1

    def test_extract_cancel_reason_okx(self) -> None:
        info = {"cancelSource": "user", "cancelSourceReason": "manual"}
        assert cli._extract_cancel_reason(info, "okx") == "user: manual"

    def test_extract_cancel_reason_binance(self) -> None:
        assert cli._extract_cancel_reason({"status": "EXPIRED"}, "binance") == "EXPIRED"

    def test_extract_cancel_reason_returns_none_otherwise(self) -> None:
        assert cli._extract_cancel_reason({}, "okx") is None
        assert cli._extract_cancel_reason({"status": "FILLED"}, "binance") is None

    def test_slippage_bps_returns_none_when_missing_inputs(self) -> None:
        record = {"price": None, "average": None}
        assert cli._slippage_bps({}, record) is None

    def test_slippage_bps_buy_positive_when_filled_above(self) -> None:
        record = {"price": 100.0, "average": 101.0}
        assert cli._slippage_bps({"side": "buy"}, record) == 100.0

    def test_slippage_bps_sign_inverted_for_sell(self) -> None:
        record = {"price": 100.0, "average": 101.0}
        assert cli._slippage_bps({"side": "sell"}, record) == -100.0

    def test_project_order_record_includes_all_fields(self) -> None:
        order = {
            "id": "o1",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": 1.0,
            "price": 100.0,
            "average": 101.0,
            "filled": 1.0,
            "remaining": 0.0,
            "cost": 101.0,
            "fee": {"cost": 0.1, "currency": "USDT"},
            "timestamp": 1735689600000,
            "info": {},
        }
        record = cli._project_order_record(order, "binance")
        assert record["id"] == "o1"
        assert record["fee_currency"] == "USDT"
        assert record["slippage_bps"] == 100.0


class TestTradeHelpers:
    def test_load_existing_trades_empty_when_missing(self, tmp_path: Path) -> None:
        rows, ids = cli._load_existing_trades(tmp_path / "trades.json")
        assert rows == []
        assert ids == set()

    def test_resolve_trade_symbols_includes_core_pairs(self) -> None:
        ex = MagicMock()
        ex.fetch_positions.side_effect = ccxt.BaseError("boom")
        symbols = cli._resolve_trade_symbols(ex)
        assert "BTC/USDT:USDT" in symbols


# ── Status command ────────────────────────────────────────────────────────


class TestStatusCommand:
    def test_format_age_seconds(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert cli._format_age(now, "2026-01-01T11:30:00+00:00") == "30m ago"

    def test_format_age_hours(self) -> None:
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert cli._format_age(now, "2026-01-01T08:00:00+00:00") == "4h ago"

    def test_format_age_days(self) -> None:
        now = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)
        assert cli._format_age(now, "2026-01-01T12:00:00+00:00") == "4d ago"

    def test_format_age_returns_dash_on_invalid(self) -> None:
        assert cli._format_age(datetime.now(tz=UTC), "not-an-iso") == "-"

    def test_read_status_summary_when_missing(self, tmp_path: Path) -> None:
        days, last_update, age = cli._read_status_summary(tmp_path / "summary.json", datetime.now(tz=UTC))
        assert (days, last_update, age) == ("-", "no data", "-")

    def test_read_status_equity_handles_missing(self, tmp_path: Path) -> None:
        assert cli._read_status_equity(tmp_path / "equity_curve.json") == "-"

    def test_read_status_equity_formats(self, tmp_path: Path) -> None:
        path = tmp_path / "equity_curve.json"
        path.write_text(json.dumps([{"date": "2026-01-01", "equity_usd": 12345.6}]))
        assert cli._read_status_equity(path) == "$12,345.60"

    def test_cmd_status_no_clients(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from client_reporting_api.cli import status_command

        monkeypatch.setattr(status_command, "_load_registry", lambda: {})
        result = cli.cmd_status(argparse.Namespace(client=None))
        assert result == 0


# ── Print summary ─────────────────────────────────────────────────────────


class TestPrintSummary:
    def test_prints_aggregated_counts(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level("INFO"):
            cli._print_summary("BACKFILL", {"PR": True, "ET": False})
        text = caplog.text
        assert "PR" in text
        assert "ET" in text
        assert "1/2 succeeded" in text
