"""Unit tests for per-DAY PnL timeseries (core.pnl_timeseries).

The dashboard PnL-over-time graph's real daily series: one row per
(date x strategy_id x coin) folded from the canonical run's per-DAY attribution
parquet, with the realized / unrealized / carry / total split.
"""

from __future__ import annotations

from decimal import Decimal

# ---------------------------------------------------------------------------
# pnl_timeseries_series — per-DAY x strategy x coin series for the dashboard graph
# ---------------------------------------------------------------------------


class TestPnlTimeseriesSeries:
    def _row(
        self,
        date_str: str,
        strategy_id: str,
        instrument_id: str,
        factor: str,
        amount: str,
        venue: str = "LIDO",
    ) -> dict[str, object]:
        return {
            "strategy_id": strategy_id,
            "instrument_id": instrument_id,
            "timestamp": f"{date_str}T00:00:00+00:00",
            "factor": factor,
            "layer": "STRATEGY",
            "venue": venue,
            "amount": amount,
        }

    def test_empty_rows_yields_empty_series(self) -> None:
        from client_reporting_api.core.pnl_timeseries import pnl_timeseries_series

        assert pnl_timeseries_series([]) == []

    def test_groups_by_date_strategy_coin_and_splits_factors(self) -> None:
        from client_reporting_api.core.pnl_timeseries import pnl_timeseries_series

        lido = "CARRY_STAKED_BASIS@lido-uniswapv3-deribit"
        jito = "CARRY_STAKED_BASIS@jito-jupiter-drift"
        rows = [
            # 2026-05-20 lido ETH leg: CARRY + BASIS + FUNDING (settled) + FEES
            self._row("2026-05-20", lido, "LIDO:STAKING:stETH", "CARRY", "9.5"),
            self._row("2026-05-20", lido, "LIDO:STAKING:stETH", "BASIS", "3.5"),
            self._row("2026-05-20", lido, "LIDO:STAKING:stETH", "FUNDING", "-3.5", venue="DERIBIT"),
            self._row("2026-05-20", lido, "LIDO:STAKING:stETH", "FEES", "0"),
            # 2026-05-20 jito SOL leg
            self._row("2026-05-20", jito, "JITO:STAKING:JitoSOL", "CARRY", "7.0", venue="JITO"),
            self._row("2026-05-20", jito, "JITO:STAKING:JitoSOL", "BASIS", "2.0", venue="JITO"),
            # 2026-05-21 lido ETH leg (next day, distinct row)
            self._row("2026-05-21", lido, "LIDO:STAKING:stETH", "CARRY", "10.0"),
        ]
        series = pnl_timeseries_series(rows)
        # 3 groups: (05-20, lido, ETH), (05-20, jito, SOL), (05-21, lido, ETH)
        assert len(series) == 3
        # Sorted by (date, strategy, coin) deterministically.
        assert [s["date"] for s in series] == ["2026-05-20", "2026-05-20", "2026-05-21"]

        by_key = {(s["date"], s["strategy_id"], s["coin"]): s for s in series}
        eth_2020 = by_key[("2026-05-20", lido, "ETH")]
        assert eth_2020["coin"] == "ETH"  # stETH -> ETH
        assert eth_2020["carry"] == "9.5"
        # realized = BASIS + FUNDING + FEES = 3.5 - 3.5 + 0 = 0 (Decimal preserves scale -> "0.0")
        assert Decimal(str(eth_2020["realized"])) == Decimal("0")
        # No MTM factor row -> unrealized honest null
        assert eth_2020["unrealized"] is None
        # total = realized(0) + unrealized(None->0) + carry(9.5) = 9.5
        assert Decimal(str(eth_2020["total"])) == Decimal("9.5")

        sol_2020 = by_key[("2026-05-20", jito, "SOL")]
        assert sol_2020["coin"] == "SOL"  # JitoSOL -> SOL
        assert sol_2020["carry"] == "7.0"
        assert sol_2020["realized"] == "2.0"
        assert sol_2020["unrealized"] is None
        assert sol_2020["total"] == "9.0"

    def test_unrealized_populated_when_mtm_factor_present(self) -> None:
        from client_reporting_api.core.pnl_timeseries import pnl_timeseries_series

        lido = "CARRY_STAKED_BASIS@lido-uniswapv3-deribit"
        rows = [
            self._row("2026-05-20", lido, "LIDO:STAKING:stETH", "CARRY", "5"),
            self._row("2026-05-20", lido, "DERIBIT:PERP:ETH-PERP", "DELTA", "12", venue="DERIBIT"),
        ]
        series = pnl_timeseries_series(rows)
        # Both legs collapse onto coin ETH (stETH and ETH-PERP), same date+strategy.
        assert len(series) == 1
        entry = series[0]
        assert entry["coin"] == "ETH"
        assert entry["carry"] == "5"
        assert entry["unrealized"] == "12"  # DELTA is a MTM factor -> populated, not null
        assert entry["realized"] == "0"
        assert entry["total"] == "17"  # 0 + 12 + 5
