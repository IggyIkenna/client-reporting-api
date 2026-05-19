"""High-coverage smoke tests for the largest core modules.

These tests exercise the public API of ``trade_analytics``,
``monthly_report_generator``, ``pnl_chart_generator``,
``dashboard_generator``, and ``invoice_state`` against the real backfill
fixtures shipped under ``data/backfill/`` so that the refactored helper
functions are at least walked end-to-end. They intentionally accept any
non-error result (PnL, equity values, etc. depend on live data) — the goal is
coverage of the happy-path control flow, not numerical regression checks.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from client_reporting_api.core import (
    dashboard_generator,
    invoice_generator,
    invoice_state,
    monthly_report_generator,
    pnl_chart_generator,
    trade_analytics,
)
from client_reporting_api.core.trade_analytics import (
    CoinBreakdown,
    TradeAnalytics,
    _accumulate_coin_into,
    _accumulate_totals_into,
    _aggregate_bills,
    _aggregate_trades,
    _build_coin_breakdowns,
    _compute_holding_times,
    _compute_monthly_pnl,
    _finalise_aggregated_coin,
    _group_trades_by_coin,
    _holding_durations_for_coin,
    _match_sell_against_buy_queue,
    _populate_capital_deployment,
    _populate_daily_series,
    _populate_holding_aggregates,
    _populate_totals,
    _summarise_holding_durations,
    aggregate_clients,
    compute_all_clients,
    compute_coin_breakdown,
)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"


def _real_clients_with_data() -> list[str]:
    """Return the subset of CLIENT_IDS that actually have backfill data on disk."""
    return [cid for cid in trade_analytics.CLIENT_IDS if (_DATA_DIR / cid).exists()]


@pytest.fixture(scope="module")
def real_clients() -> list[str]:
    clients = _real_clients_with_data()
    if not clients:
        pytest.skip("No backfilled client data present — skipping coverage smoke tests")
    return clients


# ── trade_analytics ────────────────────────────────────────────────────────


class TestTradeAnalyticsHelpers:
    """Exercise the small pure helpers used by the public API."""

    def test_group_trades_by_coin_splits_symbol(self) -> None:
        trades: list[dict[str, str | float | None]] = [
            {"symbol": "BTC/USDT", "amount": 1.0, "side": "buy", "timestamp": 1},
            {"symbol": "ETH/USDT", "amount": 2.0, "side": "buy", "timestamp": 2},
            {"symbol": "BTC/USDT", "amount": 0.5, "side": "sell", "timestamp": 3},
            {"symbol": None, "amount": 0.0, "side": "buy", "timestamp": 4},
        ]
        grouped = _group_trades_by_coin(trades)
        assert set(grouped.keys()) == {"BTC", "ETH", ""}
        assert len(grouped["BTC"]) == 2
        assert len(grouped["ETH"]) == 1

    def test_match_sell_against_buy_queue_partial_fill(self) -> None:
        buy_queue: list[tuple[float, float]] = [(0.0, 2.0), (1000.0, 1.0)]
        durations = _match_sell_against_buy_queue(sell_ts=3 * 1000 * 3600.0, sell_amount=2.5, buy_queue=buy_queue)
        # First buy fully consumed (2 BTC), second buy partial (0.5 BTC)
        assert len(durations) == 2
        assert all(d > 0 for d in durations)
        assert buy_queue == [(1000.0, 0.5)]

    def test_holding_durations_for_coin_orders_chronologically(self) -> None:
        trades: list[dict[str, str | float | None]] = [
            {"symbol": "BTC/USDT", "side": "sell", "amount": 1.0, "timestamp": 5_000},
            {"symbol": "BTC/USDT", "side": "buy", "amount": 1.0, "timestamp": 1_000},
        ]
        durations = _holding_durations_for_coin(trades)
        assert len(durations) == 1
        assert durations[0] > 0

    def test_summarise_holding_durations(self) -> None:
        result = _summarise_holding_durations([1.0, 2.0, 3.0])
        assert result == {"avg_hours": 2.0, "total_hours": 6.0, "round_trips": 3}

    def test_compute_holding_times_only_includes_round_trips(self) -> None:
        trades: list[dict[str, str | float | None]] = [
            {"symbol": "ONLY_BUY/USDT", "side": "buy", "amount": 1.0, "timestamp": 1},
            {"symbol": "BTC/USDT", "side": "buy", "amount": 1.0, "timestamp": 1_000},
            {"symbol": "BTC/USDT", "side": "sell", "amount": 1.0, "timestamp": 2_000},
        ]
        out = _compute_holding_times(trades)
        assert "BTC" in out
        assert "ONLY_BUY" not in out

    def test_aggregate_bills_classifies_by_sub_type(self) -> None:
        bills: list[dict[str, str | float]] = [
            {"inst_id": "BTC-USDT", "change": 100.0, "sub_type": "5"},
            {"inst_id": "BTC-USDT", "change": -2.5, "sub_type": "3"},
            {"inst_id": "BTC-USDT", "change": 0.5, "sub_type": "173"},
            {"inst_id": "ETH-USDT", "change": 1.0, "type": "8"},
            {"inst_id": "", "change": 99.0, "sub_type": "5"},  # skipped
        ]
        agg = _aggregate_bills(bills)
        assert agg.realized["BTC"] == 100.0
        assert agg.fees["BTC"] == -2.5
        assert agg.funding["BTC"] == 0.5
        assert agg.funding["ETH"] == 1.0

    def test_aggregate_trades_counts_by_side(self) -> None:
        trades: list[dict[str, str | float | None]] = [
            {"symbol": "BTC/USDT", "cost": 50.0, "side": "buy", "datetime": "2026-01-01T00:00:00Z"},
            {
                "symbol": "BTC/USDT",
                "cost": 60.0,
                "side": "sell",
                "datetime": "2026-01-02T00:00:00Z",
            },
            {"symbol": "ETH/USDT", "cost": 30.0, "side": "buy", "datetime": "2026-01-01T00:00:00Z"},
        ]
        agg = _aggregate_trades(trades)
        assert agg.volume["BTC"] == 110.0
        assert agg.counts["BTC"] == 2
        assert agg.buys["BTC"] == 1
        assert agg.sells["BTC"] == 1
        assert agg.daily_volume["2026-01-01"] == 80.0

    def test_build_coin_breakdowns_returns_sorted_by_abs_pnl(self) -> None:
        bills_agg = trade_analytics._BillsAggregate()
        bills_agg.realized["BTC"] = 100.0
        bills_agg.realized["ETH"] = -200.0
        trades_agg = trade_analytics._TradesAggregate()
        trades_agg.volume["BTC"] = 1000.0
        trades_agg.counts["BTC"] = 5
        coins = _build_coin_breakdowns(bills_agg, trades_agg, {})
        assert coins[0].symbol == "ETH"  # bigger |pnl|
        assert coins[1].symbol == "BTC"

    def test_populate_totals_and_daily_series(self) -> None:
        analytics = TradeAnalytics(client_id="X")
        bills_agg = trade_analytics._BillsAggregate()
        bills_agg.realized["BTC"] = 100.0
        bills_agg.fees["BTC"] = -1.0
        trades_agg = trade_analytics._TradesAggregate()
        trades_agg.volume["BTC"] = 500.0
        trades_agg.counts["BTC"] = 3
        trades_agg.daily_volume["2026-01-01"] = 250.0
        trades_agg.daily_volume["2026-01-02"] = 250.0
        _populate_totals(analytics, bills_agg, trades_agg)
        assert analytics.total_realized_pnl == 100.0
        assert analytics.total_volume_usd == 500.0
        _populate_daily_series(analytics, trades_agg.daily_volume)
        assert analytics.trading_days == 2
        assert analytics.first_trade_date == "2026-01-01"

    def test_populate_holding_aggregates(self) -> None:
        analytics = TradeAnalytics(client_id="X")
        coin_holding: dict[str, dict[str, float | int]] = {
            "BTC": {"avg_hours": 4.0, "total_hours": 12.0, "round_trips": 3},
            "ETH": {"avg_hours": 0.0, "total_hours": 0.0, "round_trips": 0},
        }
        _populate_holding_aggregates(analytics, coin_holding)
        assert analytics.total_round_trips == 3
        assert analytics.avg_holding_hours == 4.0

    def test_populate_capital_deployment_zero_volume_is_noop(self, tmp_path: Path) -> None:
        analytics = TradeAnalytics(client_id="X")
        analytics.avg_daily_volume_usd = 0.0
        # Tmp _DATA_DIR has no equity_curve.json — function returns early.
        _populate_capital_deployment(analytics, "X")
        assert analytics.capital_deployment_ratio == 0.0

    def test_compute_monthly_pnl_buckets_by_month(self) -> None:
        bills: list[dict[str, str | float]] = [
            {"inst_id": "BTC-USDT", "timestamp": 1735689600000, "change": 10.0},
            {"inst_id": "BTC-USDT", "timestamp": 1735776000000, "change": 5.0},
            {"inst_id": "BTC-USDT", "timestamp": 1738368000000, "change": 7.0},
            {"inst_id": "", "timestamp": 1735689600000, "change": 999.0},  # skipped
        ]
        out = _compute_monthly_pnl(bills)
        months = {row["month"] for row in out}
        assert len(months) >= 1

    def test_accumulate_helpers(self) -> None:
        a = CoinBreakdown(
            symbol="BTC",
            realized_pnl=10,
            trading_fees=-1,
            funding_pnl=2,
            net_pnl=11,
            volume_usd=100,
            trade_count=5,
            buy_count=3,
            sell_count=2,
            round_trips=1,
            total_holding_hours=5.0,
        )
        b = CoinBreakdown(
            symbol="BTC",
            realized_pnl=20,
            trading_fees=-2,
            funding_pnl=3,
            net_pnl=21,
            volume_usd=200,
            trade_count=10,
            buy_count=6,
            sell_count=4,
            round_trips=2,
            total_holding_hours=10.0,
        )
        _accumulate_coin_into(a, b)
        assert a.realized_pnl == 30
        assert a.trade_count == 15

        ta1 = TradeAnalytics(client_id="X", total_realized_pnl=10.0, total_volume_usd=100.0)
        ta2 = TradeAnalytics(client_id="Y", total_realized_pnl=20.0, total_volume_usd=200.0)
        _accumulate_totals_into(ta1, ta2)
        assert ta1.total_realized_pnl == 30.0
        assert ta1.total_volume_usd == 300.0

    def test_finalise_aggregated_coin_rounds(self) -> None:
        coin = CoinBreakdown(
            symbol="BTC",
            realized_pnl=10.123456,
            trading_fees=-1.987654,
            funding_pnl=0.5,
            net_pnl=8.635802,
            volume_usd=1000.0,
            trade_count=2,
            round_trips=1,
            total_holding_hours=4.0,
        )
        _finalise_aggregated_coin(coin)
        assert coin.avg_trade_size_usd == 500.0
        assert coin.avg_holding_hours == 4.0
        assert coin.realized_pnl == 10.12


class TestTradeAnalyticsRealData:
    """Exercise the full public API against real backfilled data."""

    def test_compute_coin_breakdown_for_each_real_client(self, real_clients: list[str]) -> None:
        for cid in real_clients:
            result = compute_coin_breakdown(cid)
            assert isinstance(result, TradeAnalytics)
            assert result.client_id == cid

    def test_compute_all_clients_returns_list(self, real_clients: list[str]) -> None:
        results = compute_all_clients()
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_aggregate_clients(self, real_clients: list[str]) -> None:
        agg = aggregate_clients(real_clients)
        assert agg.client_id == "AGGREGATE"
        assert agg.total_volume_usd >= 0


# ── monthly_report_generator ───────────────────────────────────────────────


class TestMonthlyReportGenerator:
    def test_slice_equity_for_month_returns_none_when_empty(self) -> None:
        result = monthly_report_generator._slice_equity_for_month([], "2026-01")
        assert result is None

    def test_slice_equity_for_month_extracts_transfers(self) -> None:
        ec = [
            {"date": "2026-01-01", "equity_usd": 1000.0},
            {"date": "2026-01-02", "equity_usd": 1100.0, "transfer_usd": 50.0},
            {"date": "2026-01-03", "equity_usd": 1200.0},
        ]
        snap = monthly_report_generator._slice_equity_for_month(ec, "2026-01")
        assert snap is not None
        assert snap.transfers_total == 50.0
        assert len(snap.transfers_list) == 1

    def test_aggregate_month_bills_filters_by_month(self) -> None:
        ts_jan = int(datetime(2026, 1, 15, tzinfo=UTC).timestamp() * 1000)
        ts_feb = int(datetime(2026, 2, 15, tzinfo=UTC).timestamp() * 1000)
        bills: list[dict[str, str | float]] = [
            {"inst_id": "BTC-USDT", "timestamp": ts_jan, "change": 10.0, "sub_type": "5"},
            {"inst_id": "BTC-USDT", "timestamp": ts_feb, "change": 5.0, "sub_type": "5"},
        ]
        agg = monthly_report_generator._aggregate_month_bills(bills, "2026-01")
        assert agg["BTC"]["realized"] == 10.0

    def test_add_month_trade_volume_filters(self) -> None:
        coin_data: dict[str, dict[str, float]] = defaultdict(
            lambda: {"realized": 0.0, "fees": 0.0, "funding": 0.0, "volume": 0.0, "trades": 0.0}
        )
        trades: list[dict[str, str | float | None]] = [
            {"datetime": "2026-01-15T00:00:00", "symbol": "BTC/USDT", "cost": 100.0},
            {"datetime": "2026-02-15T00:00:00", "symbol": "BTC/USDT", "cost": 200.0},
        ]
        total = monthly_report_generator._add_month_trade_volume(coin_data, trades, "2026-01")
        assert total == 1
        assert coin_data["BTC"]["volume"] == 100.0

    def test_coin_rows_from_aggregate_skips_empty(self) -> None:
        coin_data: dict[str, dict[str, float]] = {
            "BTC": {"realized": 100.0, "fees": -1.0, "funding": 0.5, "volume": 1000.0, "trades": 5},
            "EMPTY": {"realized": 0.0, "fees": 0.0, "funding": 0.0, "volume": 0.0, "trades": 0},
        }
        rows = monthly_report_generator._coin_rows_from_aggregate(coin_data)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC"

    def test_generate_monthly_report_returns_path_for_real_clients(
        self, real_clients: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(monthly_report_generator, "_REPORTS_DIR", tmp_path)
        # Pick the first real client; use an arbitrary recent month.
        for cid in real_clients:
            for year, month in [(2026, 1), (2025, 11), (2025, 9)]:
                path = monthly_report_generator.generate_monthly_report(cid, year, month)
                if path is not None:
                    assert Path(path).exists()
                    return
        pytest.skip("No client/month combination produced a monthly report")

    def test_generate_all_monthly_reports_for_one_client(
        self, real_clients: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(monthly_report_generator, "_REPORTS_DIR", tmp_path)
        paths = monthly_report_generator.generate_all_monthly_reports(real_clients[0])
        assert isinstance(paths, list)


# ── pnl_chart_generator ────────────────────────────────────────────────────


class TestPnLChartGenerator:
    def test_max_drawdown_increasing_curve_returns_zero(self) -> None:
        assert pnl_chart_generator._max_drawdown([1.0, 2.0, 3.0]) == 0.0

    def test_max_drawdown_handles_drawdown(self) -> None:
        dd = pnl_chart_generator._max_drawdown([1.0, 2.0, 1.5, 2.5])
        assert dd == pytest.approx(0.25)

    def test_btc_transfer_entry_records_changes(self) -> None:
        point = {"btc_balance": 2.0, "usdt_balance": 1000.0}
        prev = {"btc_balance": 1.0, "usdt_balance": 100.0}
        entry = pnl_chart_generator._btc_transfer_entry("2026-01-01", 1.0, point, prev, 1.0)
        assert entry["btc_change"] == 1.0
        assert entry["usdt_change"] == 900.0  # > 500 threshold

    def test_compute_pnl_series_for_real_clients(self, real_clients: list[str]) -> None:
        for cid in real_clients:
            data = pnl_chart_generator.compute_pnl_series(cid)
            if not data:
                continue
            # When data is present, ensure expected keys exist
            assert "dates" in data
            assert "pnl_series" in data
            return
        pytest.skip("No client produced PnL series — data likely incomplete")

    def test_generate_pnl_chart_writes_file(
        self, real_clients: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pnl_chart_generator, "_CHARTS_DIR", tmp_path)
        for cid in real_clients:
            path = pnl_chart_generator.generate_pnl_chart(cid)
            if path is not None:
                assert Path(path).exists()
                return
        pytest.skip("No client produced a PnL chart")


# ── dashboard_generator ────────────────────────────────────────────────────


class TestDashboardGenerator:
    def test_compute_daily_pnl_empty(self) -> None:
        assert dashboard_generator._compute_daily_pnl([]) == []

    def test_compute_daily_pnl_diffs_consecutive_values(self) -> None:
        out = dashboard_generator._compute_daily_pnl([100.0, 110.0, 105.0])
        assert out[0] == 100.0
        assert out[1] == 10.0
        assert out[2] == -5.0

    def test_load_orders_returns_empty_when_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dashboard_generator, "_ORDERS_DIR", tmp_path)
        assert dashboard_generator._load_orders("MISSING_CLIENT") == []

    def test_generate_dashboard_for_real_client(
        self, real_clients: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dashboard_generator, "_DASHBOARDS_DIR", tmp_path)
        for cid in real_clients:
            path = dashboard_generator.generate_dashboard(cid)
            if path is not None:
                # generate_dashboard returns the file path; verify it was written
                # and contains an HTML document.
                content = Path(path).read_text(encoding="utf-8")
                assert "<html" in content.lower() or "<!doctype" in content.lower()
                return
        pytest.skip("No client produced a dashboard")


# ── invoice_state ──────────────────────────────────────────────────────────


class TestInvoiceState:
    @pytest.fixture()
    def manager(self) -> invoice_state.InvoiceStateManager:
        return invoice_state.InvoiceStateManager()

    def test_get_total_trader_credits_returns_decimal(self, manager: invoice_state.InvoiceStateManager) -> None:
        total = manager.get_total_trader_credits()
        assert isinstance(total, Decimal)
        assert total <= Decimal("0")  # credits are negative

    def test_get_invoices_returns_seed_records(self, manager: invoice_state.InvoiceStateManager) -> None:
        invoices = manager.get_invoices()
        assert isinstance(invoices, list)
        assert len(invoices) > 0

    def test_get_dashboard_summary_has_expected_buckets(self, manager: invoice_state.InvoiceStateManager) -> None:
        summary = manager.get_dashboard_summary()
        for key in ("at_hwm", "underwater", "fund_of_fund", "prop", "totals"):
            assert key in summary

    def test_compute_current_fees_for_all_seed_clients(self, manager: invoice_state.InvoiceStateManager) -> None:
        any_result = False
        for cid in invoice_state._HWM_SEED:
            result = manager.compute_current_fees(cid)
            if result is not None:
                any_result = True
                assert "client_id" in result
                assert "status" in result
        assert any_result

    def test_load_equity_curve_returns_none_for_missing(self, manager: invoice_state.InvoiceStateManager) -> None:
        assert manager._load_equity_curve("DOES_NOT_EXIST") is None

    def test_net_usdt_moved_after_filters_dates(self) -> None:
        curve = [
            {"date": "2026-01-01", "usdt_balance": 1000.0, "btc_balance": 1.0, "transfer_usd": 100},
            {
                "date": "2026-02-01",
                "usdt_balance": 6000.0,
                "btc_balance": 1.0,
                "transfer_usd": 5000,
            },
        ]
        # Tracking starts mid-Jan: only Feb row counts
        net = invoice_state.InvoiceStateManager._net_usdt_moved_after(curve, "2026-01-15")
        assert net == Decimal("5000.0")


# ── invoice_generator ──────────────────────────────────────────────────────


class TestInvoiceGenerator:
    def test_fmt_handles_negative(self) -> None:
        assert invoice_generator._fmt(Decimal("-1234.56")) == "-$1,234.56"

    def test_fmt_handles_positive_with_thousands(self) -> None:
        assert invoice_generator._fmt(Decimal("1234567.89")) == "$1,234,567.89"

    def test_get_all_2026_invoices_returns_list(self) -> None:
        invoices = invoice_generator.get_all_2026_invoices()
        assert isinstance(invoices, list)

    def test_generate_invoice_html_renders(self) -> None:
        inv = invoice_generator.InvoiceData(
            invoice_id="TEST-001",
            client_id="PR",
            client_name="Test Client",
            invoice_date=datetime(2026, 1, 1, tzinfo=UTC).strftime("%Y-%m-%d"),
            due_date="2026-02-01",
            currency="USDT",
            previous_hwm=Decimal("100000"),
            deposits_since_hwm=Decimal("0"),
            withdrawals_since_hwm=Decimal("0"),
            effective_hwm=Decimal("100000"),
            current_balance=Decimal("110000"),
            profit=Decimal("10000"),
            fee_rate_pct=Decimal("0.30"),
            fee_label="Performance Fee (30%)",
            total_due=Decimal("3000"),
            line_items=[invoice_generator.InvoiceLineItem(description="Performance fee", amount=Decimal("3000"))],
            payment_info=invoice_generator.ODUM_PAYMENT_INFO,
        )
        html = invoice_generator.generate_invoice_html(inv)
        assert "Test Client" in html
        assert "$3,000.00" in html or "3,000" in html
