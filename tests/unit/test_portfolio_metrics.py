"""Unit tests for Phase-10 portfolio metrics (net views / per-strategy / bps / ROE / backtest).

Covers ``client_reporting_api.core.portfolio_metrics`` — the pure metric folds the
``/net-views`` / ``/per-strategy`` / ``/bps-pnl`` / ``/roe`` / ``/backtest`` routes
render. ``per_strategy_breakdown`` is fed the strategy-keyed InstructionLedger
``LedgerRow`` tape (P11.9 — the row ``strategy_id`` is authoritative) plus the
manifest ``strategy_ids`` enumeration universe, mirroring the live
``firm-paper-determinism`` run: it returns EVERY declared strategy (real numbers where
the tape has activity, honest zeros where it does not), sums reconcile to ``overall``
with no double-count, and ``net_views`` keeps its venue-mapped fixtures. Assertions are
EXACT Decimal strings / typed honest-None — never fabricated 0.

SSOT: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md Phase 10.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts import EventOrigin, EventType, LedgerAssetClass, LedgerRow, PositionLedgerRow

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


def _trade(
    *,
    row_id: str,
    strategy_id: str,
    venue: str,
    asset_canonical_id: str,
    asset_class: LedgerAssetClass,
    delta: Decimal,
    price: Decimal,
    ts: datetime = _AS_OF,
) -> LedgerRow:
    """A strategy-keyed TRADE ``LedgerRow`` (P11.9: ``strategy_id`` is authoritative)."""
    return LedgerRow(
        event_id=f"evt-{row_id}",
        row_id=row_id,
        event_origin=EventOrigin.INSTRUCTION,
        event_type=EventType.TRADE,
        strategy_id=strategy_id,
        trade_id=f"{venue}:SPOT:{asset_canonical_id}|inst_{row_id}|{ts.isoformat()}",
        timestamp_utc=ts,
        asset_group="defi",
        venue=venue,
        account_id="acct",
        client_id="client-A",
        asset_symbol=asset_canonical_id,
        asset_canonical_id=asset_canonical_id,
        asset_class=asset_class,
        delta=delta,
        price=price,
        quote_currency="USDC",
        fees_in_quote=Decimal(0),
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


def _two_strategy_tape() -> list[LedgerRow]:
    """ETH strategy: BUY 100 @3000 (UNISWAP_V3). SOL strategy: BUY 10 @150 (JITO).

    Strategy-keyed via the row ``strategy_id`` (P11.9) — NOT venue-mapped. Each is a
    single open BUY leg, so avg-cost realised is 0; total = unrealized (0, no marks).
    """
    return [
        _trade(
            row_id="eth1",
            strategy_id=_ETH_SID,
            venue="UNISWAP_V3",
            asset_canonical_id="ETH",
            asset_class=LedgerAssetClass.SPOT_TOKEN,
            delta=Decimal(100),
            price=Decimal(3000),
        ),
        _trade(
            row_id="sol1",
            strategy_id=_SOL_SID,
            venue="JITO",
            asset_canonical_id="SOL",
            asset_class=LedgerAssetClass.LST,
            delta=Decimal(10),
            price=Decimal(150),
        ),
    ]


def _breakdown(rows: list[LedgerRow], strategy_ids: tuple[str, ...], window_days: Decimal) -> dict[str, object]:
    return per_strategy_breakdown(
        rows,
        strategy_ids,
        marks={},
        as_of=_AS_OF,
        share_class_of={},
        instrument_key_by_row_id={r.row_id: r.trade_id.split("|")[0] for r in rows if r.trade_id},
        window_days=window_days,
    )


def test_per_strategy_breakdown_keys_by_row_strategy_id_and_computes_turnover() -> None:
    breakdown = _breakdown(_two_strategy_tape(), _STRATEGY_IDS, Decimal(6))
    by_sid = {s["strategy_id"]: s for s in breakdown["strategies"]}

    eth = by_sid[_ETH_SID]
    assert eth["trade_count"] == 1
    assert eth["turnover_usd"] == "300000"  # |100| * 3000
    # single open BUY leg → realised 0 (avg-cost), no marks → unrealized 0 → total 0.
    assert eth["total_pnl"] == "0"
    # zero pnl over non-zero turnover → 0 bps (not None — turnover exists).
    assert eth["bps_pnl_on_turnover"] == str(Decimal(0) / Decimal(300000) * Decimal(10000))

    sol = by_sid[_SOL_SID]
    assert sol["trade_count"] == 1
    assert sol["turnover_usd"] == "1500"  # |10| * 150

    overall = breakdown["overall"]
    assert overall["trade_count"] == 2
    assert overall["turnover_usd"] == "301500"  # 300000 + 1500


def test_per_strategy_breakdown_enumerates_full_manifest_book_with_honest_zeros() -> None:
    """N manifest strategy_ids, ledger activity for only K (<N) → N rows returned.

    The N-K declared-but-idle strategies show HONEST zeros (0 trades / 0 P&L), are
    NEVER dropped; the K active ones show real ledger-derived numbers. Sums reconcile
    and there is no double-count (each ledger row belongs to exactly one strategy).
    """
    idle_a = "CARRY_BASIS_PERP@hyperliquid-eth-1h-usdc-v3-prod"
    idle_b = "ARBITRAGE_PRICE_DISPERSION@uniswapv3-linkweth-spot-usdc-v2-prod"
    # N = 4 manifest ids (+ the bare archetype, which is NOT a strategy and is excluded);
    # K = 2 have tape activity (_ETH_SID, _SOL_SID).
    manifest = ("CARRY_STAKED_BASIS", _ETH_SID, _SOL_SID, idle_a, idle_b)
    breakdown = _breakdown(_two_strategy_tape(), manifest, Decimal(6))
    strategies = breakdown["strategies"]
    ids = [s["strategy_id"] for s in strategies]

    # All 4 @-qualified manifest strategies appear (the bare archetype excluded) → N rows.
    assert set(ids) == {_ETH_SID, _SOL_SID, idle_a, idle_b}
    assert "CARRY_STAKED_BASIS" not in ids  # bare archetype is NOT a strategy
    assert len(ids) == len(set(ids))  # no duplicate strategy rows

    by_sid = {s["strategy_id"]: s for s in strategies}
    # The N-K idle strategies are present with HONEST zeros (not dropped).
    for idle in (idle_a, idle_b):
        assert by_sid[idle]["trade_count"] == 0
        assert by_sid[idle]["position_count"] == 0
        assert by_sid[idle]["realized_pnl"] == "0"
        assert by_sid[idle]["total_pnl"] == "0"
        assert by_sid[idle]["bps_pnl_on_turnover"] is None  # 0 turnover → honest None
    # The K active strategies carry their real ledger-derived numbers.
    assert by_sid[_ETH_SID]["trade_count"] == 1
    assert by_sid[_SOL_SID]["trade_count"] == 1

    # Sums reconcile to overall with NO double-count (one row → one strategy partition).
    overall = breakdown["overall"]
    assert sum(int(s["trade_count"]) for s in strategies) == overall["trade_count"] == 2
    assert sum(Decimal(str(s["turnover_usd"])) for s in strategies) == Decimal(str(overall["turnover_usd"]))
    assert sum(Decimal(str(s["realized_pnl"])) for s in strategies) == Decimal(str(overall["realized_pnl"]))
    assert sum(Decimal(str(s["unrealized_pnl"])) for s in strategies) == Decimal(str(overall["unrealized_pnl"]))


def test_per_strategy_breakdown_includes_tape_only_strategy_not_in_manifest() -> None:
    """A strategy on the tape but absent from the manifest is still enumerated (UNION)."""
    tape_only = "CARRY_STAKED_BASIS@rocketpool-curve-okx-f100-usdc-1h-usdc-v2-prod"
    rows = [
        *_two_strategy_tape(),
        _trade(
            row_id="rp1",
            strategy_id=tape_only,
            venue="ROCKETPOOL",
            asset_canonical_id="ETH",
            asset_class=LedgerAssetClass.LST,
            delta=Decimal(5),
            price=Decimal(3000),
        ),
    ]
    # Manifest does NOT declare tape_only — UNION must still surface it.
    breakdown = _breakdown(rows, (_ETH_SID, _SOL_SID), Decimal(6))
    ids = {s["strategy_id"] for s in breakdown["strategies"]}
    assert tape_only in ids
    by_sid = {s["strategy_id"]: s for s in breakdown["strategies"]}
    assert by_sid[tape_only]["trade_count"] == 1
    assert by_sid[tape_only]["turnover_usd"] == "15000"  # |5| * 3000


def test_per_strategy_breakdown_zero_turnover_is_honest_none_bps() -> None:
    # A passive-only strategy (no TRADE rows) → turnover 0 → bps None (honest, no division).
    passive = LedgerRow(
        event_id="evt-f1",
        row_id="f1",
        event_origin=EventOrigin.PASSIVE,
        event_type=EventType.FUNDING_ACCRUAL,
        strategy_id=_ETH_SID,
        timestamp_utc=_AS_OF,
        asset_group="defi",
        venue="DERIBIT",
        account_id="acct",
        client_id="client-A",
        asset_symbol="ETH-PERP",
        asset_canonical_id="ETH-PERP",
        asset_class=LedgerAssetClass.PERP,
        delta=Decimal(50),
        quote_currency="USDC",
    )
    breakdown = per_strategy_breakdown(
        [passive],
        _STRATEGY_IDS,
        marks={},
        as_of=_AS_OF,
        share_class_of={},
        window_days=Decimal(6),
    )
    by_sid = {s["strategy_id"]: s for s in breakdown["strategies"]}
    eth = by_sid[_ETH_SID]
    assert eth["trade_count"] == 0
    assert eth["realized_pnl"] == "50"  # passive accrual flows to realised
    assert eth["bps_pnl_on_turnover"] is None  # turnover 0 → undefined bps


def test_per_strategy_breakdown_empty_is_honest_zero() -> None:
    breakdown = per_strategy_breakdown([], (), marks={}, as_of=_AS_OF, share_class_of={}, window_days=Decimal(6))
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


def test_backtest_surface_smart_with_measured_alpha() -> None:
    """SMART fill_model + sidecar present → real execution_alpha, not structural zero."""
    surface = backtest_surface(
        fill_model="SMART",
        window_start=datetime(2026, 5, 16, tzinfo=UTC),
        window_end=datetime(2026, 5, 22, tzinfo=UTC),
        paper_total_pnl=Decimal("100"),
        batch_total_pnl=None,
        batch_run_id=None,
        execution_alpha_bps=Decimal("10"),
    )
    assert surface["execution_cost"]["execution_alpha"] == "10"
    assert surface["execution_cost"]["execution_alpha_is_structural_zero"] is False
    assert surface["execution_assumptions"]["fill_model"] == "SMART"


def test_backtest_surface_smart_pending_when_no_sidecar() -> None:
    """SMART fill_model + no sidecar yet → execution_alpha is None (honest PENDING)."""
    surface = backtest_surface(
        fill_model="SMART",
        window_start=datetime(2026, 5, 16, tzinfo=UTC),
        window_end=datetime(2026, 5, 22, tzinfo=UTC),
        paper_total_pnl=Decimal("100"),
        batch_total_pnl=None,
        batch_run_id=None,
        execution_alpha_bps=None,
    )
    assert surface["execution_cost"]["execution_alpha"] is None
    assert surface["execution_cost"]["execution_alpha_is_structural_zero"] is False
