"""Phase-10 portfolio metrics — net views, per-strategy breakdown, bps/ROE, backtest.

The reader/metrics layer the UI renders for the operator desk (Phase 10 of
``citadel_paper_batch_live_reconciliation_2026_06_19.md``). Pure functions over
the SAME canonical-run ledger surface ``ledger_views`` already reads — no new I/O
seam, no all-runs concat: the route resolves THE canonical run, reads its TRADE
tape + PricingLedger marks + ``RunManifest``, and these functions fold them.

Surfaces (all run-scoped, honest typed-empty where a source is absent):

- :func:`net_views` — **net-in-dollars** (Σ position USD value at marks),
  **net-in-coin** (net qty per coin / ``asset_canonical_id``), **delta-per-coin**
  (net SIGNED USD delta exposure per coin — ≈0 for a delta-neutral book).
- :func:`per_strategy_breakdown` — trades / positions / P&L / turnover grouped by
  ``strategy_id`` (the ``@``-qualified manifest id, mapped from venue) + an overall
  roll-up.
- bps PnL on turnover (``total_pnl / Σ|notional| * 1e4``) + % ROE annualised — both
  per strategy and overall, computed inside :func:`per_strategy_breakdown` so a
  single fold yields the whole metric set coherently.
- :func:`backtest_surface` — the ``__batch__`` rerun's historical PnL + the
  execution-cost (execution alpha = smart - benchmark; ≈0 in the benchmark-only
  paper/batch, stated honestly) + the execution ASSUMPTIONS (fill-model fidelity
  tier + the ``RunManifest.fill_model``). Honest PENDING when the canonical run has
  no batch rerun yet.

**strategy_id derivation (canonical, NOT a hand map):** every ``LedgerRow`` of the
run's InstructionLedger tape carries its OWN ``@``-qualified ``strategy_id`` (P11.9 —
the authoritative key), so :func:`per_strategy_breakdown` partitions the tape by
``row.strategy_id`` and folds EACH partition into its own ``PositionLedger`` — the
per-strategy numbers are real ledger-derived figures, not a venue-collapsed guess.
The enumeration UNIVERSE is the ``RunManifest.strategy_ids`` (the full canonical book
— every strategy the run declared, e.g. all 145) UNION the strategy_ids actually
present on the tape: a strategy with ledger activity shows its real numbers, one with
none shows HONEST zeros, and none is ever dropped (the prior implementation enumerated
only the attribution-parquet / venue-mapped subset → the book looked tiny). The legacy
:func:`strategy_id_for_venue` (venue → ``@``-qualified id) is retained ONLY for the
``/transfers`` panel's pre-P11.9 fallback, never for per-strategy enumeration.

SSOT: codex/09-strategy/operational/pnl-attribution.md (bps/ROE + multi-dim attr).
Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md Phase 10.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from unified_api_contracts import EventType, LedgerRow, PositionLedgerRow
from unified_trading_library.ledger.materialize import (  # noqa: qg-deep-import
    materialize_position_ledger,  # not re-exported at UTL top level
)

#: Strategy bucket label for a ``LedgerRow`` carrying no ``strategy_id`` (a
#: non-strategy / pre-P11.9 row). Kept distinct from a real strategy id so the
#: per-strategy enumeration never silently folds it into a strategy.
_UNATTRIBUTED_STRATEGY = "unattributed"

#: Days per year used for ROE annualisation (calendar, simple linear scaling of the
#: run-window return — NOT compounded; the run window is days-to-weeks).
_DAYS_PER_YEAR = Decimal("365")

#: bps scale factor (1 bps = 1e-4).
_BPS = Decimal("10000")


def _norm_venue(venue: str) -> str:
    """Normalise a venue to its slug token: lower-case, strip ``_`` (``UNISWAP_V3`` → ``uniswapv3``)."""
    return venue.replace("_", "").lower()


def _base_coin(asset_canonical_id: str) -> str:
    """The underlying coin of an asset, collapsing the perp/future leg onto its spot.

    ``ETH-PERP`` / ``ETH-PERPETUAL`` / ``ETH_PERP`` → ``ETH`` so the perp HEDGE nets
    against the spot/LST leg in the per-coin delta (a delta-neutral book holds long
    spot/LST + short perp of the SAME underlying — the residual per-coin delta is the
    UNHEDGED exposure, ≈0 when balanced). A spot/LST id is returned unchanged.
    """
    upper = asset_canonical_id.upper()
    for suffix in ("-PERP", "-PERPETUAL", "_PERP", "-FUT", "-FUTURE"):
        if upper.endswith(suffix):
            return asset_canonical_id[: -len(suffix)]
    return asset_canonical_id


def _slug_of(strategy_id: str) -> str:
    """The post-``@`` slug of a strategy_id with ``-`` stripped, else ``""`` for a bare id."""
    if "@" not in strategy_id:
        return ""
    return strategy_id.split("@", 1)[1].replace("-", "")


def strategy_id_for_venue(
    venue: str,
    strategy_ids: Sequence[str],
) -> str:
    """Map a ``venue`` to the canonical ``@``-qualified strategy_id from the manifest.

    Matches the SINGLE manifest strategy whose post-``@`` slug embeds the venue's
    normalised token (``LIDO`` → ``lido`` ⊂ ``lido-uniswapv3-deribit``). When no
    ``@``-qualified id covers the venue, falls back to the bare base id (the one
    without ``@``) if present, else ``"unknown"`` — never a fabricated id.
    """
    norm = _norm_venue(venue)
    for sid in strategy_ids:
        slug = _slug_of(sid)
        if slug and norm in slug:
            return sid
    bare = [s for s in strategy_ids if "@" not in s]
    return bare[0] if bare else "unknown"


def _usd_price_by_asset(positions: Sequence[PositionLedgerRow]) -> dict[str, Decimal]:
    """Best USD mark per BASE COIN from the position legs.

    A staking (LST) leg carries an exchange-rate mark (``LIDO:STAKING:ETH`` mark=1),
    NOT a USD price; the DEX-pool / perp leg of the SAME underlying carries the USD
    mark (3000). Keyed on the BASE coin (:func:`_base_coin`, collapsing ``ETH-PERP``
    → ``ETH``) so the perp's USD price is available to value the staking leg. The
    per-coin USD price is the LARGEST mark across that coin's legs (the exchange-rate
    ~1 never wins over a real USD price). Used only to value coin-denominated legs
    (staking qty in ETH) in USD; a coin is absent (honest) when no leg has a usable
    mark — the caller surfaces null, not a fabricated 0.
    """
    out: dict[str, Decimal] = {}
    for p in positions:
        if p.mark_price is None:
            continue
        coin = _base_coin(p.asset_canonical_id)
        cur = out.get(coin)
        if cur is None or p.mark_price > cur:
            out[coin] = p.mark_price
    return out


def _position_usd_value(p: PositionLedgerRow, usd_price: Mapping[str, Decimal]) -> Decimal | None:
    """Signed USD value of a position leg, or ``None`` when no USD mark is resolvable.

    ``net_qty`` is in the leg's BASE unit. A perp/DEX leg's own ``mark_price`` IS the
    USD price, so ``net_qty * mark`` is the USD value directly. A staking leg's mark
    is an exchange-rate (~1), so its USD value uses the per-coin USD price
    (``usd_price[base_coin]``) instead. Returns ``None`` (honest) when neither a leg
    mark nor a per-coin USD price exists.
    """
    asset_usd = usd_price.get(_base_coin(p.asset_canonical_id))
    # A leg whose own mark is materially > 1 is already a USD price (perp/DEX);
    # a ~1 staking exchange-rate mark must be valued at the per-asset USD price.
    leg_mark = p.mark_price
    if leg_mark is not None and leg_mark > Decimal("1"):
        return p.net_qty * leg_mark
    if asset_usd is not None:
        return p.net_qty * asset_usd
    if leg_mark is not None:
        return p.net_qty * leg_mark
    return None


def net_views(positions: Sequence[PositionLedgerRow]) -> dict[str, object]:
    """**net-in-dollars** / **net-in-coin** / **delta-per-coin** for the run.

    - ``net_in_dollars`` — Σ over legs of the leg's USD value (long legs + short
      legs net). ``gross_usd`` is Σ|leg USD| (the deployed notional).
    - ``net_in_coin`` — Σ ``net_qty`` per ``asset_canonical_id`` (coin units): a
      delta-neutral book holds long spot/LST + short perp of the SAME coin, so the
      coin net is the residual exposure in coin units.
    - ``delta_per_coin`` — the SIGNED USD delta exposure per coin (Σ signed USD
      value of that coin's legs); ≈0 per coin for the delta-neutral books (the long
      spot/LST USD ≈ the short perp USD).

    Empty positions → all-empty + ``"0"`` totals (honest zero, never fabricated).
    A leg with no resolvable USD mark is counted in ``net_in_coin`` but contributes
    ``unpriced`` (not a fabricated 0) to the USD views.
    """
    usd_price = _usd_price_by_asset(positions)

    coin_qty: OrderedDict[str, Decimal] = OrderedDict()
    coin_delta_usd: OrderedDict[str, Decimal] = OrderedDict()
    coin_unpriced: set[str] = set()
    net_usd = Decimal(0)
    gross_usd = Decimal(0)

    for p in positions:
        coin = _base_coin(p.asset_canonical_id)
        coin_qty[coin] = coin_qty.get(coin, Decimal(0)) + p.net_qty
        usd_value = _position_usd_value(p, usd_price)
        if usd_value is None:
            coin_unpriced.add(coin)
            coin_delta_usd.setdefault(coin, Decimal(0))
            continue
        net_usd += usd_value
        gross_usd += abs(usd_value)
        coin_delta_usd[coin] = coin_delta_usd.get(coin, Decimal(0)) + usd_value

    net_in_coin = [{"asset": coin, "net_qty": str(qty)} for coin, qty in coin_qty.items()]
    delta_per_coin = [
        {
            "asset": coin,
            "delta_usd": str(coin_delta_usd.get(coin, Decimal(0))),
            "priced": coin not in coin_unpriced,
        }
        for coin in coin_qty
    ]
    return {
        "net_in_dollars": str(net_usd),
        "gross_in_dollars": str(gross_usd),
        "net_in_coin": net_in_coin,
        "delta_per_coin": delta_per_coin,
    }


def _trade_notional(row: LedgerRow) -> Decimal:
    """|delta| x price — the absolute traded notional of one TRADE ``LedgerRow`` (turnover unit).

    ``delta`` is the signed BASE quantity; ``price`` the per-unit quote price. A row
    with no ``price`` (should not happen on a TRADE row) contributes 0 — never a guess.
    """
    if row.price is None:
        return Decimal(0)
    return abs(row.delta) * row.price


def _bps_on_turnover(total_pnl: Decimal, turnover: Decimal) -> str | None:
    """``total_pnl / turnover * 1e4`` (bps). ``None`` when turnover is 0 (honest — no division)."""
    if turnover == 0:
        return None
    return str(total_pnl / turnover * _BPS)


def _annualised_roe(total_pnl: Decimal, equity: Decimal, window_days: Decimal) -> str | None:
    """% ROE annualised: ``(pnl/equity) * (365/window_days) * 100``.

    ``None`` when equity is 0 or the window is non-positive (honest — undefined ROE).
    Linear (simple) annualisation of the window return — the run window is
    days-to-weeks; compounding a sub-month window would overstate.
    """
    if equity == 0 or window_days <= 0:
        return None
    period_return = total_pnl / equity
    return str(period_return * (_DAYS_PER_YEAR / window_days) * Decimal("100"))


def _row_strategy_id(row: LedgerRow) -> str:
    """The row's canonical ``strategy_id`` (or ``_UNATTRIBUTED_STRATEGY`` when ``None``)."""
    return row.strategy_id or _UNATTRIBUTED_STRATEGY


def _enumerate_strategy_universe(
    manifest_strategy_ids: Sequence[str],
    rows: Sequence[LedgerRow],
) -> list[str]:
    """The FULL per-strategy enumeration: manifest ``strategy_ids`` union with tape strategy_ids.

    The universe is the canonical run's declared book (``RunManifest.strategy_ids`` —
    every strategy the run launched, e.g. all 145) UNION the distinct ``strategy_id``s
    actually stamped on the InstructionLedger tape — so a strategy declared-but-idle
    AND a strategy that traded but somehow wasn't in the manifest both appear, and none
    is dropped. Manifest order first (deterministic, the operator's declared order),
    then any tape-only strategy_ids in first-seen order. The bare archetype (a
    ``strategy_id`` without ``@``) is NOT a strategy and is excluded from the manifest
    contribution (manifest ids are all ``@``-qualified); a genuinely-unattributed tape
    row surfaces only via the ``unattributed`` bucket when present on the tape.
    """
    seen: OrderedDict[str, None] = OrderedDict()
    for sid in manifest_strategy_ids:
        if "@" in sid:  # the bare archetype is not a strategy
            seen.setdefault(sid, None)
    for row in rows:
        seen.setdefault(_row_strategy_id(row), None)
    return list(seen.keys())


@dataclass(frozen=True)
class _StrategyMetrics:
    """One strategy's (or the overall roll-up's) raw Decimal/int metrics before serialisation.

    Kept as typed Decimals/ints so the ``overall`` roll-up can SUM the per-strategy
    folds exactly (no string round-trip) and recompute bps/ROE from the aggregate.
    """

    trade_count: int
    position_count: int
    turnover: Decimal
    gross: Decimal
    realized: Decimal
    unrealized: Decimal


def _serialize_metrics(m: _StrategyMetrics, window_days: Decimal) -> dict[str, object]:
    """Project a :class:`_StrategyMetrics` to the API string/typed-None metric dict."""
    total = m.realized + m.unrealized
    return {
        "trade_count": m.trade_count,
        "position_count": m.position_count,
        "turnover_usd": str(m.turnover),
        "gross_usd": str(m.gross),
        "realized_pnl": str(m.realized),
        "unrealized_pnl": str(m.unrealized),
        "total_pnl": str(total),
        "bps_pnl_on_turnover": _bps_on_turnover(total, m.turnover),
        "roe_annualised_pct": _annualised_roe(total, m.gross, window_days),
    }


def _sum_metrics(metrics: Iterable[_StrategyMetrics]) -> _StrategyMetrics:
    """Sum per-strategy metrics into the overall roll-up (double-count-free aggregate)."""
    trade_count = position_count = 0
    turnover = gross = realized = unrealized = Decimal(0)
    for m in metrics:
        trade_count += m.trade_count
        position_count += m.position_count
        turnover += m.turnover
        gross += m.gross
        realized += m.realized
        unrealized += m.unrealized
    return _StrategyMetrics(
        trade_count=trade_count,
        position_count=position_count,
        turnover=turnover,
        gross=gross,
        realized=realized,
        unrealized=unrealized,
    )


def per_strategy_breakdown(
    rows: Sequence[LedgerRow],
    strategy_ids: Sequence[str],
    *,
    marks: Mapping[str, Decimal],
    as_of: datetime,
    share_class_of: Mapping[str, str],
    instrument_key_by_row_id: Mapping[str, str] | None = None,
    window_days: Decimal,
) -> dict[str, object]:
    """Per-strategy detail + overall roll-up over the FULL declared book.

    Enumerates EVERY strategy in :func:`_enumerate_strategy_universe`
    (``RunManifest.strategy_ids`` union with tape strategy_ids — the whole book, e.g. all 145),
    then partitions the run's InstructionLedger ``LedgerRow`` tape by the AUTHORITATIVE
    per-row ``strategy_id`` (P11.9) and folds each partition INDEPENDENTLY into its own
    ``PositionLedger`` (avg-cost, marks joined). Each strategy entry carries:
    ``trade_count`` (its TRADE rows), ``position_count`` (its materialised legs),
    ``turnover_usd`` (Σ|delta·price| over its TRADE rows), ``realized_pnl`` /
    ``unrealized_pnl`` / ``total_pnl`` (Σ over its position legs + its PASSIVE accruals),
    ``gross_usd`` (its deployed notional = equity proxy), ``bps_pnl_on_turnover`` and
    ``roe_annualised_pct``.

    A strategy with ledger activity shows its real ledger-derived numbers; a strategy
    declared in the manifest but with NO tape rows shows HONEST zeros (zero trades /
    positions / P&L) — never dropped, never fabricated. The ``overall`` block is the
    SUM of the per-strategy folds (each ledger row belongs to exactly ONE strategy
    partition, so summing is double-count-free) — NOT a re-fold of the whole tape: a
    whole-tape re-fold would collide the SAME ``instrument_key`` traded by different
    strategies into one netted position (wrong avg-cost / realised). The bare archetype
    is never a strategy, so it is never a partition. ``overall`` bps/ROE are recomputed
    from the aggregated total / turnover / gross.

    ``window_days`` (from the ``RunManifest``) drives the ROE annualisation. ``bps`` /
    ``roe`` are ``None`` (honest) when their denominator is 0. Empty tape + empty
    manifest → empty ``strategies`` + zeroed ``overall``.
    """
    rows_by_strategy: OrderedDict[str, list[LedgerRow]] = OrderedDict()
    for row in rows:
        rows_by_strategy.setdefault(_row_strategy_id(row), []).append(row)

    def _metrics(strat_rows: Sequence[LedgerRow]) -> _StrategyMetrics:
        strat_trades = [r for r in strat_rows if r.event_type == EventType.TRADE]
        strat_passive = [r for r in strat_rows if r.event_origin.value == "passive"]
        positions = materialize_position_ledger(
            strat_trades,
            marks=marks,
            as_of=as_of,
            share_class_of=share_class_of,
            instrument_key_by_row_id=instrument_key_by_row_id,
        )
        usd_price = _usd_price_by_asset(positions)
        passive_pnl = sum((r.delta for r in strat_passive), Decimal(0))
        realized = sum((p.realized_pnl or Decimal(0) for p in positions), Decimal(0)) + passive_pnl
        unrealized = sum((p.unrealized_pnl or Decimal(0) for p in positions), Decimal(0))
        turnover = sum((_trade_notional(r) for r in strat_trades), Decimal(0))
        gross = sum(
            (abs(v) for v in (_position_usd_value(p, usd_price) for p in positions) if v is not None),
            Decimal(0),
        )
        return _StrategyMetrics(
            trade_count=len(strat_trades),
            position_count=len(positions),
            turnover=turnover,
            gross=gross,
            realized=realized,
            unrealized=unrealized,
        )

    per_strategy_metrics = [
        (sid, _metrics(rows_by_strategy.get(sid, []))) for sid in _enumerate_strategy_universe(strategy_ids, rows)
    ]
    strategies = [{"strategy_id": sid, **_serialize_metrics(m, window_days)} for sid, m in per_strategy_metrics]

    # overall = SUM of the per-strategy folds (double-count-free: one row → one
    # strategy), NOT a whole-tape re-fold (which collides cross-strategy instrument_keys).
    overall_metrics = _sum_metrics(m for _sid, m in per_strategy_metrics)
    return {
        "strategies": strategies,
        "overall": _serialize_metrics(overall_metrics, window_days),
        "window_days": str(window_days),
    }


#: The deterministic-fill fidelity tiers, coarsest → finest (UAC market-data ladder).
#: The paper/batch benchmark fill is at the OHLCV (signal-candle close) tier.
_FILL_FIDELITY_LADDER = ["OHLCV", "BBO", "DEPTH", "TRADES", "MBO"]


def backtest_surface(
    *,
    fill_model: str,
    window_start: datetime,
    window_end: datetime,
    paper_total_pnl: Decimal,
    batch_total_pnl: Decimal | None,
    batch_run_id: str | None,
    matched_trades: int | None = None,
    execution_alpha_bps: Decimal | None = None,
) -> dict[str, object]:
    """Paper-vs-batch backtest payload: historical PnL + execution cost + assumptions.

    - ``historical_pnl`` — the ``__batch__`` rerun's total P&L (``batch_total_pnl``),
      or ``None`` (honest PENDING) when the canonical run has no batch rerun yet.
    - ``execution_cost`` / ``execution_alpha`` — smart-matching fill - benchmark
      fill. In the benchmark-only paper/batch BOTH sides use the benchmark fill, so
      execution alpha is structurally **0** (stated honestly via
      ``execution_alpha_is_structural_zero=True``), NOT a measured non-zero. The real
      execution alpha only appears at the live boundary (live - paper).
    - ``execution_assumptions`` — the ``RunManifest.fill_model`` (BENCHMARK) + the
      fill-model fidelity tier (OHLCV signal-candle close for the benchmark) + the
      full fidelity ladder.
    - ``paper_vs_batch`` — the determinism comparison (``paper - batch`` should be
      ≈0; this is the UI's unified-view payload).

    Honest typed-empty: ``status=PENDING`` when no batch rerun, ``OK`` once present.
    """
    has_batch = batch_total_pnl is not None
    paper_minus_batch = (paper_total_pnl - batch_total_pnl) if has_batch else None
    return {
        "status": "OK" if has_batch else "PENDING",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "historical_pnl": str(batch_total_pnl) if has_batch else None,
        "batch_run_id": batch_run_id,
        "paper_total_pnl": str(paper_total_pnl),
        "paper_vs_batch": {
            "paper_total_pnl": str(paper_total_pnl),
            "batch_total_pnl": str(batch_total_pnl) if has_batch else None,
            "paper_minus_batch": str(paper_minus_batch) if paper_minus_batch is not None else None,
            "matched_trades": matched_trades,
            "note": (
                "paper - batch should be ≈0 (determinism proof, ε=0)"
                if has_batch
                else "batch rerun has not landed for this run yet — historical PnL PENDING"
            ),
        },
        "execution_cost": {
            "execution_alpha": "0" if fill_model.upper() == "BENCHMARK" else (
                str(execution_alpha_bps) if execution_alpha_bps is not None else None
            ),
            "execution_alpha_is_structural_zero": fill_model.upper() == "BENCHMARK",
            "definition": (
                "execution alpha = smart-matching fill - benchmark fill (~0 while paper/batch use the "
                "benchmark fill; real alpha appears live-paper)"
            ),
        },
        "execution_assumptions": {
            "fill_model": fill_model,
            "fidelity_tier": "OHLCV",
            "fidelity_tier_detail": "benchmark fill at the signal candle's close (deterministic yardstick)",
            "fidelity_ladder": list(_FILL_FIDELITY_LADDER),
            "slippage_cost_model": (
                "benchmark (liquidity-adjusted at signal-candle close); smart-matching cost model "
                "applies only at the execution-service smart-fill tier"
            ),
        },
    }


__all__ = [
    "backtest_surface",
    "net_views",
    "per_strategy_breakdown",
    "strategy_id_for_venue",
]
