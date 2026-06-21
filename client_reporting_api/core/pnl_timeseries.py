"""Per-DAY PnL timeseries — one row per (date x strategy_id x coin) for the graph.

The dashboard's PnL-over-time graph renders a REAL daily series (not snapshot
bars). The natural per-day source is the canonical run's attribution parquet —
already per-DAY per-strategy with the economic factors CARRY / BASIS / FUNDING /
FEES (the daily carry accrual IS the per-day series). This module folds those raw
:class:`PnLAttributionRow` dicts (read run-scoped by
``attribution_reader.read_attribution_rows`` for THE canonical run) into one entry
per ``(date, strategy_id, coin)`` with the realized / unrealized / carry / total
split.

``coin`` derives canonically from the row's ``instrument_id`` (the staking leg
nets onto its underlying: ``LIDO:STAKING:stETH`` -> ``ETH``,
``JITO:STAKING:JitoSOL`` -> ``SOL``) — never a hand-threaded metadata map.

Honest absence: ``unrealized`` is a null when no mark-to-market factor row exists
for a group (on the flat determinism corpus the per-day price-move unrealized is
genuinely not computed — null, never a fabricated 0 that reads as a real
mark-to-market). Empty rows -> empty series, never mock data.

SSOT: codex/09-strategy/operational/pnl-attribution.md (canonical attribution
factors). Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import cast

#: Carry-family factors — the staking/lending accrual that IS the per-day carry
#: P&L for the carry_staked_basis archetype (the natural per-day series). Summed
#: into the row's ``carry`` component, NOT ``realized`` (so the dashboard graph can
#: stack carry distinctly from settled realised flows).
_CARRY_FACTORS: frozenset[str] = frozenset({"CARRY", "CARRY_BASE", "CARRY_AVS_CONTINUOUS", "CARRY_ISSUER_SEASONAL"})

#: Mark-to-market factors — price-move / option-greeks P&L that is NOT booked as a
#: settled cash flow. On the flat determinism corpus these are genuinely absent
#: (no price-move attribution emitted per day), so the row's ``unrealized`` is an
#: HONEST null when no MTM-factor row exists — never a fabricated 0 that reads as a
#: real mark-to-market.
_UNREALIZED_FACTORS: frozenset[str] = frozenset({"DELTA", "GREEKS"})

#: LST / staked-derivative symbol -> underlying coin. The carry archetype's
#: staking legs are denominated in a liquid-staking token (``stETH`` / ``JitoSOL`` /
#: ``rETH`` / ``cbETH`` / ``mSOL``) that economically IS its underlying coin — the
#: per-coin series must net the staking leg onto its underlying (an ``stETH`` carry
#: leg is ETH P&L), so the dashboard groups ETH vs SOL, not stETH vs JitoSOL.
_LST_TO_COIN: Mapping[str, str] = {
    "STETH": "ETH",
    "WSTETH": "ETH",
    "RETH": "ETH",
    "CBETH": "ETH",
    "WETH": "ETH",
    "JITOSOL": "SOL",
    "MSOL": "SOL",
    "BSOL": "SOL",
    "WSOL": "SOL",
}

#: Perp / future / spot suffixes stripped to reach the bare underlying symbol
#: (``ETH-PERP`` -> ``ETH``), so a hedge perp leg nets onto the same coin as the
#: spot/LST leg of that underlying.
_LEG_SUFFIXES: tuple[str, ...] = ("-PERPETUAL", "-PERP", "_PERP", "-FUTURE", "-FUT", "-USDC", "-USDT", "-USD")


def _attr_amount(row: Mapping[str, object]) -> Decimal:
    """Parse a ``PnLAttributionRow`` ``amount`` (string-or-Decimal) to Decimal; 0 on garbage."""
    try:
        return Decimal(str(row.get("amount", "0")))
    except (ArithmeticError, ValueError):
        return Decimal(0)


def coin_from_instrument(instrument_id: object) -> str:
    """Derive the canonical coin (``ETH`` / ``SOL`` / …) from an attribution ``instrument_id``.

    The attribution ``instrument_id`` is the canonical ``VENUE:INSTRUMENT_TYPE:SYMBOL``
    key (``LIDO:STAKING:stETH`` / ``JITO:STAKING:JitoSOL`` / ``DERIBIT:PERP:ETH-PERP``).
    The coin is the SYMBOL's underlying: take the last ``:``-segment, strip any
    perp/future/quote suffix, then map a liquid-staking token onto its underlying via
    :data:`_LST_TO_COIN` (``stETH`` -> ``ETH``, ``JitoSOL`` -> ``SOL``). A bare spot
    symbol (``ETH`` / ``SOL``) returns unchanged (upper-cased). Returns ``"unknown"``
    for an empty/garbage id — never fabricated.
    """
    raw = str(instrument_id or "").strip()
    if not raw:
        return "unknown"
    symbol = raw.split(":")[-1].strip().upper()
    for suffix in _LEG_SUFFIXES:
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
            break
    if symbol in _LST_TO_COIN:
        return _LST_TO_COIN[symbol]
    return symbol or "unknown"


def _attr_row_date(row: Mapping[str, object]) -> str:
    """The ISO ``YYYY-MM-DD`` date of an attribution row from its ``timestamp``.

    The attribution parquet is per-DAY (one shard per ``date=`` GCS partition), and
    every row carries the day's ``timestamp``; the date is the first 10 chars of the
    ISO timestamp. Returns ``"unknown"`` when no parseable timestamp is present (the
    row is still surfaced — never silently dropped — under an ``unknown`` date key).
    """
    ts = row.get("timestamp")
    if ts is None:
        return "unknown"
    if isinstance(ts, datetime):
        return ts.date().isoformat()
    text = str(ts).strip()
    return text[:10] if len(text) >= 10 else "unknown"


def pnl_timeseries_series(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Fold per-DAY attribution rows into one entry per (date x strategy_id x coin).

    The per-day series the dashboard's PnL-over-time graph renders: each
    :class:`PnLAttributionRow` carries ``timestamp`` (the day), ``strategy_id``,
    ``instrument_id`` (-> coin), ``factor`` and ``amount``. This groups by
    ``(date, strategy_id, coin)`` and partitions each group's factor amounts:

    - ``carry`` — Σ of the CARRY-family factors (:data:`_CARRY_FACTORS`); the
      staking/lending accrual that IS the per-day carry P&L for carry_staked_basis.
    - ``unrealized`` — Σ of the mark-to-market factors (:data:`_UNREALIZED_FACTORS`,
      DELTA/GREEKS); ``None`` (HONEST null) when no MTM-factor row exists for the
      group — on the flat corpus the price-move unrealized is genuinely not computed
      per day, so it is null, not a fabricated 0.
    - ``realized`` — Σ of every OTHER (settled, non-carry, non-MTM) factor
      (BASIS / FUNDING / FEES / SLIPPAGE / SETTLEMENT / REBATE / …); the booked
      daily cash flows.
    - ``total`` — ``realized + (unrealized or 0) + carry``.

    Deterministic ordering: sorted by ``(date, strategy_id, coin)`` so the series is
    stable across calls (a graph reads it left-to-right). Empty ``rows`` -> ``[]``
    (honest empty, never mock).
    """
    # (date, strategy_id, coin) -> {realized, carry, unrealized, has_unrealized}
    groups: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        date_key = _attr_row_date(row)
        strategy_id = str(row.get("strategy_id", "")) or "unknown"
        coin = coin_from_instrument(row.get("instrument_id"))
        factor = str(row.get("factor", "")).upper()
        amount = _attr_amount(row)
        key = (date_key, strategy_id, coin)
        agg = groups.setdefault(
            key,
            {
                "realized": Decimal(0),
                "carry": Decimal(0),
                "unrealized": Decimal(0),
                "has_unrealized": False,
            },
        )
        if factor in _CARRY_FACTORS:
            agg["carry"] = cast("Decimal", agg["carry"]) + amount
        elif factor in _UNREALIZED_FACTORS:
            agg["unrealized"] = cast("Decimal", agg["unrealized"]) + amount
            agg["has_unrealized"] = True
        else:
            agg["realized"] = cast("Decimal", agg["realized"]) + amount

    series: list[dict[str, object]] = []
    for (date_key, strategy_id, coin), agg in sorted(groups.items()):
        realized = cast("Decimal", agg["realized"])
        carry = cast("Decimal", agg["carry"])
        has_unrealized = cast("bool", agg["has_unrealized"])
        unrealized: Decimal | None = cast("Decimal", agg["unrealized"]) if has_unrealized else None
        total = realized + (unrealized or Decimal(0)) + carry
        series.append(
            {
                "date": date_key,
                "strategy_id": strategy_id,
                "coin": coin,
                "realized": str(realized),
                "unrealized": None if unrealized is None else str(unrealized),
                "total": str(total),
                "carry": str(carry),
            }
        )
    return series
