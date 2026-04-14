"""Tests for the unified transfer pipeline (collector + store + tear sheet)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from unified_api_contracts.internal import TransferRecord

from client_reporting_api.core import transfer_collector, transfer_store

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def _tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with test data."""
    client_dir = tmp_path / "TEST_CLIENT"
    client_dir.mkdir()

    # equity curve with transfers
    curve = [
        {"date": "2026-01-01", "equity_usd": 100000},
        {"date": "2026-01-02", "equity_usd": 101000},
        {"date": "2026-01-03", "equity_usd": 151500, "transfer_usd": 50000},
        {"date": "2026-01-04", "equity_usd": 152000},
        {"date": "2026-01-05", "equity_usd": 141000, "transfer_usd": -10000},
    ]
    (client_dir / "equity_curve.json").write_text(json.dumps(curve))

    # summary
    (client_dir / "summary.json").write_text(json.dumps({"venue": "okx"}))

    # empty transfers.json
    (client_dir / "transfers.json").write_text(
        json.dumps({"deposits": [], "withdrawals": []})
    )

    return tmp_path


@pytest.fixture()
def _btc_data_dir(tmp_path: Path) -> Path:
    """BTC account test data."""
    client_dir = tmp_path / "BTC_CLIENT"
    client_dir.mkdir()

    curve = [
        {
            "date": "2026-01-01",
            "btc_balance": 1.0,
            "usdt_balance": 5000,
            "btc_price_usd": 50000,
        },
        {
            "date": "2026-01-02",
            "btc_balance": 1.01,
            "usdt_balance": 5100,
            "btc_price_usd": 51000,
        },
        {
            "date": "2026-01-03",
            "btc_balance": 2.01,
            "usdt_balance": 5100,
            "btc_price_usd": 52000,
            "transfer_usd": 52000,
        },
    ]
    (client_dir / "equity_curve.json").write_text(json.dumps(curve))
    (client_dir / "summary.json").write_text(json.dumps({"venue": "okx"}))
    (client_dir / "transfers.json").write_text(
        json.dumps({"deposits": [], "withdrawals": []})
    )

    return tmp_path


@pytest.fixture()
def _ccxt_data_dir(tmp_path: Path) -> Path:
    """Test data with CCXT raw transfers."""
    client_dir = tmp_path / "CCXT_CLIENT"
    client_dir.mkdir()

    transfers = {
        "deposits": [
            {
                "id": "dep-001",
                "currency": "USDT",
                "amount": 50000.0,
                "status": "ok",
                "timestamp": 1735689600000,  # 2025-01-01T00:00:00Z
                "txid": "0xabc123",
                "address": "0x1234",
                "network": "ETH",
                "fee": {"currency": "USDT", "cost": 0},
            }
        ],
        "withdrawals": [
            {
                "id": "wd-001",
                "currency": "BTC",
                "amount": 0.5,
                "status": "ok",
                "timestamp": 1735776000000,  # 2025-01-02T00:00:00Z
                "txid": "0xdef456",
                "address": "bc1q...",
                "network": "BTC",
                "fee": {"currency": "BTC", "cost": 0.0001},
            }
        ],
    }
    (client_dir / "transfers.json").write_text(json.dumps(transfers))
    (client_dir / "summary.json").write_text(json.dumps({"venue": "binance"}))

    # Minimal equity curve
    curve = [
        {"date": "2025-01-01", "equity_usd": 50000},
        {"date": "2025-01-02", "equity_usd": 49500},
    ]
    (client_dir / "equity_curve.json").write_text(json.dumps(curve))

    return tmp_path


# ── TransferCollector Tests ───────────────────────────────────────────────────


class TestTransferCollector:
    """Test transfer_collector module."""

    def test_collect_from_equity_curve_usdt(self, _tmp_data_dir: Path) -> None:
        """USDT transfers detected from equity curve transfer_usd flags."""
        with patch.object(transfer_collector, "_DATA_DIR", _tmp_data_dir):
            records = transfer_collector.collect_from_equity_curve("TEST_CLIENT")

        assert len(records) == 2

        # First transfer: $50K deposit
        dep = records[0]
        assert dep.direction == "deposit"
        assert dep.currency == "USDT"
        assert dep.amount == Decimal("50000")
        assert dep.usd_amount == Decimal("50000")

        # Second transfer: $10K withdrawal
        wd = records[1]
        assert wd.direction == "withdrawal"
        assert wd.currency == "USDT"
        assert wd.amount == Decimal("10000")

    def test_collect_from_equity_curve_btc(self, _btc_data_dir: Path) -> None:
        """BTC transfers detected from balance changes on transfer_usd days."""
        with patch.object(transfer_collector, "_DATA_DIR", _btc_data_dir):
            records = transfer_collector.collect_from_equity_curve("BTC_CLIENT")

        # Should detect the 1 BTC deposit on Jan 3
        assert len(records) >= 1
        btc_dep = [r for r in records if r.currency == "BTC"]
        assert len(btc_dep) == 1
        assert btc_dep[0].direction == "deposit"
        assert btc_dep[0].amount == Decimal("1")
        # USD equivalent should be BTC price * amount
        assert btc_dep[0].usd_amount == Decimal("52000.00")

    def test_collect_from_ccxt_backfill(self, _ccxt_data_dir: Path) -> None:
        """CCXT raw deposit/withdrawal records parsed correctly."""
        with patch.object(transfer_collector, "_DATA_DIR", _ccxt_data_dir):
            records = transfer_collector.collect_from_ccxt_backfill("CCXT_CLIENT")

        assert len(records) == 2

        dep = next(r for r in records if r.direction == "deposit")
        assert dep.currency == "USDT"
        assert dep.amount == Decimal("50000")
        assert dep.usd_amount == Decimal("50000")  # stablecoin
        assert dep.venue == "binance"
        assert dep.tx_hash == "0xabc123"

        wd = next(r for r in records if r.direction == "withdrawal")
        assert wd.currency == "BTC"
        assert wd.amount == Decimal("0.5")
        assert wd.usd_amount == Decimal("0")  # BTC, no price lookup in backfill
        assert wd.fee == Decimal("0.0001")

    def test_collect_all_prioritises_ccxt(self, _ccxt_data_dir: Path) -> None:
        """CCXT records take priority over equity curve when both exist."""
        with patch.object(transfer_collector, "_DATA_DIR", _ccxt_data_dir):
            records = transfer_collector.collect_all_transfers("CCXT_CLIENT")

        # CCXT has 2 records, equity curve has none (no transfer_usd flags)
        assert len(records) == 2

    def test_empty_client(self, tmp_path: Path) -> None:
        """Non-existent client returns empty list."""
        with patch.object(transfer_collector, "_DATA_DIR", tmp_path):
            records = transfer_collector.collect_all_transfers("NONEXISTENT")
        assert records == []


# ── TransferStore Tests ───────────────────────────────────────────────────────


class TestTransferStore:
    """Test transfer_store module."""

    def test_get_transfers_caches(self, _tmp_data_dir: Path) -> None:
        """Results are cached per client."""
        with patch.object(transfer_collector, "_DATA_DIR", _tmp_data_dir):
            transfer_store.clear_cache()
            first = transfer_store.get_transfers("TEST_CLIENT")
            second = transfer_store.get_transfers("TEST_CLIENT")
            assert first is second  # same object = cached
            transfer_store.clear_cache()

    def test_get_net_deposits(self, _tmp_data_dir: Path) -> None:
        """Net deposits computed correctly from transfers."""
        with patch.object(transfer_collector, "_DATA_DIR", _tmp_data_dir):
            transfer_store.clear_cache()
            result = transfer_store.get_net_deposits("TEST_CLIENT")

        # $50K deposit - $10K withdrawal = $40K net
        assert result["net_deposits_usd"] == Decimal("40000")
        assert result["total_deposits_usd"] == Decimal("50000")
        assert result["total_withdrawals_usd"] == Decimal("10000")
        assert result["primary_currency"] == "USDT"
        transfer_store.clear_cache()

    def test_get_daily_transfers(self, _tmp_data_dir: Path) -> None:
        """Daily transfer amounts keyed by date."""
        with patch.object(transfer_collector, "_DATA_DIR", _tmp_data_dir):
            transfer_store.clear_cache()
            daily = transfer_store.get_daily_transfers("TEST_CLIENT")

        assert daily["2026-01-03"] == 50000.0
        assert daily["2026-01-05"] == -10000.0
        assert "2026-01-02" not in daily
        transfer_store.clear_cache()

    def test_transfer_count(self, _tmp_data_dir: Path) -> None:
        """Transfer count matches number of records."""
        with patch.object(transfer_collector, "_DATA_DIR", _tmp_data_dir):
            transfer_store.clear_cache()
            count = transfer_store.get_transfer_count("TEST_CLIENT")
        assert count == 2
        transfer_store.clear_cache()

    def test_btc_net_deposits(self, _btc_data_dir: Path) -> None:
        """BTC account net deposits computed in BTC and USD."""
        with patch.object(transfer_collector, "_DATA_DIR", _btc_data_dir):
            transfer_store.clear_cache()
            result = transfer_store.get_net_deposits("BTC_CLIENT")

        by_btc = result["by_currency"].get("BTC", {})
        assert by_btc.get("deposits", Decimal("0")) == Decimal("1")
        transfer_store.clear_cache()


# ── TearSheet Tests ───────────────────────────────────────────────────────────


class TestTearSheet:
    """Test tear_sheet_generator module."""

    def test_compute_client_data(self, _tmp_data_dir: Path) -> None:
        """Client data computation produces expected structure."""
        from client_reporting_api.core import backfill_store, tear_sheet_generator

        with patch.object(backfill_store, "_DATA_DIR", _tmp_data_dir):
            data = tear_sheet_generator._compute_client_data("TEST_CLIENT")

        assert "dates" in data
        assert "equity_series" in data
        assert "twr_equity_series" in data
        assert "drawdown_series" in data
        assert "rolling_sharpe" in data
        assert "stats" in data
        assert "monthly_matrix" in data

        assert len(data["dates"]) == 5
        assert data["stats"]["denomination"] == "USD"

    def test_generate_tear_sheet_produces_html(self, _tmp_data_dir: Path) -> None:
        """Tear sheet generates valid HTML file."""
        from client_reporting_api.core import backfill_store, tear_sheet_generator

        with (
            patch.object(backfill_store, "_DATA_DIR", _tmp_data_dir),
            patch.object(tear_sheet_generator, "_OUTPUT_DIR", _tmp_data_dir / "output"),
        ):
            path = tear_sheet_generator.generate_tear_sheet(["TEST_CLIENT"])

        assert path
        content = Path(path).read_text()
        assert "Chart.js" in content or "chart.js" in content
        assert "TEST_CLIENT" in content or "Monthly Returns" in content

    def test_multi_client_overlay(self, _tmp_data_dir: Path) -> None:
        """Multi-client tear sheet includes both clients."""
        from client_reporting_api.core import backfill_store, tear_sheet_generator

        # Create a second client
        client2_dir = _tmp_data_dir / "CLIENT2"
        client2_dir.mkdir()
        curve = [
            {"date": "2026-01-01", "equity_usd": 200000},
            {"date": "2026-01-02", "equity_usd": 202000},
            {"date": "2026-01-03", "equity_usd": 204000},
        ]
        (client2_dir / "equity_curve.json").write_text(json.dumps(curve))
        (client2_dir / "summary.json").write_text(json.dumps({"venue": "binance"}))

        with (
            patch.object(backfill_store, "_DATA_DIR", _tmp_data_dir),
            patch.object(tear_sheet_generator, "_OUTPUT_DIR", _tmp_data_dir / "output"),
        ):
            path = tear_sheet_generator.generate_tear_sheet(
                ["TEST_CLIENT", "CLIENT2"],
                title="Multi Strategy Report",
            )

        assert path
        content = Path(path).read_text()
        assert "Multi Strategy Report" in content

    def test_csv_data_generation(self, _tmp_data_dir: Path) -> None:
        """CSV data is generated for download."""
        from client_reporting_api.core import backfill_store, tear_sheet_generator

        with patch.object(backfill_store, "_DATA_DIR", _tmp_data_dir):
            data = tear_sheet_generator._compute_client_data("TEST_CLIENT")
            csv_str = tear_sheet_generator._generate_csv_data("TEST_CLIENT", data)

        assert "Date,Equity,TWR_Index,Drawdown_Pct" in csv_str
        lines = csv_str.strip().split("\n")
        assert len(lines) == 6  # header + 5 data rows


# ── UAC TransferRecord Schema Tests ──────────────────────────────────────────


class TestTransferRecordSchema:
    """Test the enhanced TransferRecord schema."""

    def test_usd_amount_field(self) -> None:
        """TransferRecord has usd_amount field with default."""
        record = TransferRecord(
            transfer_id="test-001",
            client_id="PR",
            venue="okx",
            direction="deposit",
            currency="USDT",
            amount=Decimal("50000"),
            status="ok",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert record.usd_amount == Decimal("0")  # default

    def test_usd_amount_explicit(self) -> None:
        """USD amount can be set explicitly."""
        record = TransferRecord(
            transfer_id="test-002",
            client_id="GP",
            venue="okx",
            direction="deposit",
            currency="BTC",
            amount=Decimal("1.5"),
            usd_amount=Decimal("75000"),
            status="ok",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert record.usd_amount == Decimal("75000")
        assert record.currency == "BTC"
        assert record.amount == Decimal("1.5")

    def test_frozen_immutable(self) -> None:
        """TransferRecord is frozen (immutable)."""
        record = TransferRecord(
            transfer_id="test-003",
            client_id="PR",
            venue="okx",
            direction="deposit",
            currency="USDT",
            amount=Decimal("1000"),
            status="ok",
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(Exception):  # noqa: B017 — ValidationError or similar
            record.amount = Decimal("9999")
