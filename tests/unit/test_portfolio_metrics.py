"""Unit tests for Phase-10 portfolio metrics (net views / per-strategy / bps / ROE / backtest).

Covers ``client_reporting_api.core.portfolio_metrics`` — the pure metric folds the
``/net-views`` / ``/per-strategy`` / ``/bps-pnl`` / ``/roe`` / ``/backtest`` routes
render. Fixtures mirror the live ``firm-paper-determinism`` 2-strategy carry run:
ETH legs (UNISWAP_V3 / LIDO / DERIBIT) → the lido strategy, SOL legs (JUPITER /
JITO / DRIFT) → the jito strategy. Assertions are EXACT Decimal strings / typed
honest-None — never fabricated 0.

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md Phase 10.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts import LedgerAssetClass, PositionLedgerRow
from unified_api_contracts.internal import TradeFillRecord

from client_reporting_api.core.portfolio_metrics import (
    backtest_surface,
    net_views,
    per_strategy_breakdown,
    strategy_id_for_venue,
)

_AS_OF = datetime(2026, 5, 22, 0, 0, 0, tzinfo=UTC)
_ETH_SID = "CARRY_STAKED_BASIS@lido-uniswapv3-deribit-f100-usdc-1h-usdc-v2-prod"
_SOL_SID = "CARRY_STAKED_BASIS@jito-jupiter-drift-f100-usdc-1h-usdc-v2-prod"
_STRATEGY_IDS = ("CARRY_STAKED_BASIS", _ETH_SID, _SOL_SID)


def _pos(
    *,
    venue: str,
    instrument_key: str,
    asset_canonical_id: str,
    asset_class: LedgerAssetClass,
    net_qty: Decimal,
    mark: Decimal | None,
    realized: Decimal = Decimal(0),
    unrealized: Decimal | None = Decimal(0),
) -> PositionLedgerRow:
    return PositionLedgerRow(
        as_of=_AS_OF,
        account_id="acct",
        client_id="client-A",
        venue=venue,
        instrument_key=instrument_key,
        asset_canonical_id=asset_canonical_id,
        asset_symbol=asset_canonical_id,
        asset_class=asset_class,
        share_class=asset_canonical_id,
        net_qty=net_qty,
        avg_cost=mark,
        mark_price=mark,
        quote_currency="USDC",
        realized_pnl=realized,
        unrealized_pnl=unrealized,
    )


def _fill(*, venue: str, instrument_key: str, side: str, qty: Decimal, price: Decimal) -> TradeFillRecord:
    return TradeFillRecord(
        trade_key=f"{instrument_key}|{side}|{qty}",
        instrument_key=instrument_key,
        strategy_instruction_id="inst_CARRY_STAKED_BASIS_00000001",
        tick_timestamp=_AS_OF,
        venue=venue,
        side=side,
        qty=qty,
        fill_price=price,
        fees_in_quote=Decimal(0),
        fill_model="BENCHMARK",
        correlation_id="corr-1",
        client_order_id="ord-1",
    )


def _eth_book() -> list[PositionLedgerRow]:
    # ETH spot long 100 + perp short 100 @3000 → delta-neutral (≈0 per coin).
    return [
        _pos(
            venue="UNISWAP_V3",
            instrument_key="UNISWAP_V3:DEX_POOL:ETH",
            asset_canonical_id="ETH",
            asset_class=LedgerAssetClass.SPOT_TOKEN,
            net_qty=Decimal(100),
            mark=Decimal(3000),
        ),
        _pos(
            venue="DERIBIT",
            instrument_key="DERIBIT:PERPETUAL:ETH-PERP",
            asset_canonical_id="ETH-PERP",
            asset_class=LedgerAssetClass.PERP,
            net_qty=Decimal(-100),
            mark=Decimal(3000),
        ),
    ]


# ---------------------------------------------------------------------------
# strategy_id_for_venue — canonical venue → @-qualified id mapping
# ---------------------------------------------------------------------------


def test_strategy_id_for_venue_maps_each_venue_to_its_qualified_id() -> None:
    assert strategy_id_for_venue("UNISWAP_V3", _STRATEGY_IDS) == _ETH_SID
    assert strategy_id_for_venue("LIDO", _STRATEGY_IDS) == _ETH_SID
    assert strategy_id_for_venue("DERIBIT", _STRATEGY_IDS) == _ETH_SID
    assert strategy_id_for_venue("JUPITER", _STRATEGY_IDS) == _SOL_SID
    assert strategy_id_for_venue("JITO", _STRATEGY_IDS) == _SOL_SID
    assert strategy_id_for_venue("DRIFT", _STRATEGY_IDS) == _SOL_SID


def test_strategy_id_for_venue_falls_back_to_bare_then_unknown() -> None:
    assert strategy_id_for_venue("BINANCE", _STRATEGY_IDS) == "CARRY_STAKED_BASIS"
    assert strategy_id_for_venue("BINANCE", (_ETH_SID,)) == "unknown"


# ---------------------------------------------------------------------------
# net_views — net-$/net-coin/delta-per-coin
# ---------------------------------------------------------------------------


def test_net_views_delta_neutral_book_nets_to_zero_per_coin() -> None:
    views = net_views(_eth_book())
    # net_in_coin sums spot + perp on the base coin ETH → 100 - 100 = 0.
    assert views["net_in_coin"] == [{"asset": "ETH", "net_qty": "0"}]
    # delta-per-coin: long 100*3000 + short -100*3000 = 0 USD → delta-neutral.
    assert views["delta_per_coin"] == [{"asset": "ETH", "delta_usd": "0", "priced": True}]
    assert views["net_in_dollars"] == "0"
    # gross is the deployed notional: |300000| + |-300000| = 600000.
    assert views["gross_in_dollars"] == "600000"


def test_net_views_empty_is_honest_zero() -> None:
    views = net_views([])
    assert views["net_in_dollars"] == "0"
    assert views["gross_in_dollars"] == "0"
    assert views["net_in_coin"] == []
    assert views["delta_per_coin"] == []


def test_net_views_staking_leg_valued_at_per_asset_usd_price() -> None:
    # A LIDO staking leg carries an exchange-rate mark (1), valued at the per-asset
    # USD price (3000 from the perp leg), NOT at 1.
    book = [
        _pos(
            venue="LIDO",
            instrument_key="LIDO:STAKING:ETH",
            asset_canonical_id="ETH",
            asset_class=LedgerAssetClass.LST,
            net_qty=Decimal(100),
            mark=Decimal(1),
        ),
        _pos(
            venue="DERIBIT",
            instrument_key="DERIBIT:PERPETUAL:ETH-PERP",
            asset_canonical_id="ETH-PERP",
            asset_class=LedgerAssetClass.PERP,
            net_qty=Decimal(-100),
            mark=Decimal(3000),
        ),
    ]
    views = net_views(book)
    # staking 100*3000 (per-asset USD) + perp -100*3000 = 0 → delta-neutral.
    assert views["delta_per_coin"] == [{"asset": "ETH", "delta_usd": "0", "priced": True}]


# ---------------------------------------------------------------------------
# per_strategy_breakdown — grouping + bps + ROE
# ---------------------------------------------------------------------------


def test_per_strategy_breakdown_groups_by_venue_and_computes_bps_roe() -> None:
    positions = [
        _pos(
            venue="UNISWAP_V3",
            instrument_key="UNISWAP_V3:DEX_POOL:ETH",
            asset_canonical_id="ETH",
            asset_class=LedgerAssetClass.SPOT_TOKEN,
            net_qty=Decimal(100),
            mark=Decimal(3000),
            realized=Decimal(50),
            unrealized=Decimal(0),
        ),
        _pos(
            venue="JITO",
            instrument_key="JITO:STAKING:SOL",
            asset_canonical_id="SOL",
            asset_class=LedgerAssetClass.LST,
            net_qty=Decimal(10),
            mark=Decimal(150),
            realized=Decimal(0),
            unrealized=Decimal(0),
        ),
    ]
    fills = [
        _fill(
            venue="UNISWAP_V3",
            instrument_key="UNISWAP_V3:DEX_POOL:ETH",
            side="BUY",
            qty=Decimal(100),
            price=Decimal(3000),
        ),
        _fill(venue="JITO", instrument_key="JITO:STAKING:SOL", side="BUY", qty=Decimal(10), price=Decimal(150)),
    ]
    breakdown = per_strategy_breakdown(positions, fills, _STRATEGY_IDS, window_days=Decimal(6))
    by_sid = {s["strategy_id"]: s for s in breakdown["strategies"]}

    eth = by_sid[_ETH_SID]
    assert eth["trade_count"] == 1
    assert eth["turnover_usd"] == "300000"  # 100 * 3000
    assert eth["total_pnl"] == "50"
    # bps = 50 / 300000 * 1e4 = 1.666...
    assert eth["bps_pnl_on_turnover"] == str(Decimal(50) / Decimal(300000) * Decimal(10000))
    # ROE = (50/300000) * (365/6) * 100
    assert eth["roe_annualised_pct"] == str(Decimal(50) / Decimal(300000) * (Decimal(365) / Decimal(6)) * Decimal(100))

    sol = by_sid[_SOL_SID]
    assert sol["total_pnl"] == "0"
    # zero pnl over non-zero turnover → 0 bps (not None — turnover exists). Decimal
    # preserves the exponent of 0/1500*1e4 → the string is the canonical "0E+4".
    assert sol["bps_pnl_on_turnover"] == str(Decimal(0) / Decimal(1500) * Decimal(10000))

    overall = breakdown["overall"]
    assert overall["total_pnl"] == "50"
    assert overall["turnover_usd"] == "301500"  # 300000 + 1500


def test_per_strategy_breakdown_zero_turnover_is_honest_none_bps() -> None:
    # A position with no fills → turnover 0 → bps None (honest, no division).
    positions = [
        _pos(
            venue="UNISWAP_V3",
            instrument_key="UNISWAP_V3:DEX_POOL:ETH",
            asset_canonical_id="ETH",
            asset_class=LedgerAssetClass.SPOT_TOKEN,
            net_qty=Decimal(100),
            mark=Decimal(3000),
            realized=Decimal(50),
        ),
    ]
    breakdown = per_strategy_breakdown(positions, [], _STRATEGY_IDS, window_days=Decimal(6))
    eth = breakdown["strategies"][0]
    assert eth["bps_pnl_on_turnover"] is None  # turnover 0 → undefined bps


def test_per_strategy_breakdown_empty_is_honest_zero() -> None:
    breakdown = per_strategy_breakdown([], [], _STRATEGY_IDS, window_days=Decimal(6))
    assert breakdown["strategies"] == []
    assert breakdown["overall"]["total_pnl"] == "0"
    assert breakdown["overall"]["bps_pnl_on_turnover"] is None
    assert breakdown["overall"]["roe_annualised_pct"] is None


# ---------------------------------------------------------------------------
# backtest_surface — historical PnL + execution cost + assumptions
# ---------------------------------------------------------------------------


def test_backtest_surface_pending_when_no_batch() -> None:
    surface = backtest_surface(
        fill_model="BENCHMARK",
        window_start=datetime(2026, 5, 16, tzinfo=UTC),
        window_end=datetime(2026, 5, 22, tzinfo=UTC),
        paper_total_pnl=Decimal("-3.5"),
        batch_total_pnl=None,
        batch_run_id=None,
    )
    assert surface["status"] == "PENDING"
    assert surface["historical_pnl"] is None
    assert surface["paper_vs_batch"]["paper_minus_batch"] is None
    # benchmark fill model → execution alpha is a STRUCTURAL zero, not measured.
    assert surface["execution_cost"]["execution_alpha"] == "0"
    assert surface["execution_cost"]["execution_alpha_is_structural_zero"] is True
    assert surface["execution_assumptions"]["fill_model"] == "BENCHMARK"
    assert surface["execution_assumptions"]["fidelity_tier"] == "OHLCV"


def test_backtest_surface_ok_when_batch_present_computes_paper_minus_batch() -> None:
    surface = backtest_surface(
        fill_model="BENCHMARK",
        window_start=datetime(2026, 5, 16, tzinfo=UTC),
        window_end=datetime(2026, 5, 22, tzinfo=UTC),
        paper_total_pnl=Decimal("-3.5"),
        batch_total_pnl=Decimal("-3.5"),
        batch_run_id="batch-xyz",
        matched_trades=42,
    )
    assert surface["status"] == "OK"
    assert surface["historical_pnl"] == "-3.5"
    # determinism ε=0 — Decimal subtraction preserves scale → "0.0".
    assert surface["paper_vs_batch"]["paper_minus_batch"] == str(Decimal("-3.5") - Decimal("-3.5"))
    assert surface["paper_vs_batch"]["matched_trades"] == 42
