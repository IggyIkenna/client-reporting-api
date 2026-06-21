"""Per-strategy ledger rollups + attribution-breakdown helpers (P11.9).

Split out of :mod:`client_reporting_api.core.ledger_views` (the parent file hit the
900-line cap). Two concern groups, both pure (no I/O):

1. **Per-strategy LedgerRow rollups** — ``filter_rows_by_strategy`` +
   ``by_strategy_pnl``: every ledger row carries a canonical ``@``-qualified
   ``strategy_id`` (P11.9), so a multi-strategy run's positions / PnL split
   per-strategy with no double-count (distinct strategies hold distinct legs).
2. **Attribution-breakdown** — GROUP-BY rollups over the raw ``PnLAttributionRow``
   dicts (``attribution_reader.read_attribution_rows``) by venue / instrument /
   factor / layer / strategy_id, including the nested per-strategy waterfall.

SSOT: ``codex/09-strategy/operational/paper-batch-live-reconciliation.md`` §1+§4.3.
Plan: ``plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md`` (P11.9).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from unified_api_contracts import EventType, LedgerRow
from unified_trading_library.ledger.materialize import (  # noqa: qg-deep-import
    materialize_position_ledger,  # not re-exported at UTL top level
)

#: The strategy bucket label for a ``LedgerRow`` with no ``strategy_id`` stamped
#: (a non-strategy row, or a legacy row written before P11.9). Kept distinct from a
#: real strategy id so a per-strategy rollup never silently folds it into a strategy.
_UNATTRIBUTED_STRATEGY = "unattributed"


def _row_strategy_id(row: LedgerRow) -> str:
    """The row's canonical ``strategy_id`` (or ``_UNATTRIBUTED_STRATEGY`` when ``None``)."""
    return row.strategy_id or _UNATTRIBUTED_STRATEGY


def filter_rows_by_strategy(rows: Sequence[LedgerRow], strategy_id: str | None) -> list[LedgerRow]:
    """Return only the rows whose ``strategy_id`` matches ``strategy_id``.

    Match is case-insensitive, exact-or-substring (so a bare ``carry_staked_basis``
    matches every ``carry_staked_basis@…`` slot, and the full ``@``-qualified id
    matches exactly). ``strategy_id=None``/blank returns all rows unchanged. A row
    with no ``strategy_id`` only matches the literal ``unattributed`` request.
    """
    if not strategy_id or not strategy_id.strip():
        return list(rows)
    req = strategy_id.strip().lower()
    out: list[LedgerRow] = []
    for row in rows:
        sid = _row_strategy_id(row).lower()
        if req == sid or req in sid:
            out.append(row)
    return out


def _strategy_ids_in_order(rows: Sequence[LedgerRow]) -> list[str]:
    """Distinct ``strategy_id``s over ``rows`` in first-seen order (deterministic)."""
    seen: OrderedDict[str, None] = OrderedDict()
    for row in rows:
        seen.setdefault(_row_strategy_id(row), None)
    return list(seen.keys())


def by_strategy_pnl(
    rows: Sequence[LedgerRow],
    *,
    marks: Mapping[str, Decimal],
    as_of: datetime,
    share_class_of: Mapping[str, str],
    instrument_key_by_row_id: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Per-strategy realised / unrealised / passive P&L rollup (sums to the grand total).

    Partitions the ledger tape by the row ``strategy_id`` and folds EACH partition
    INDEPENDENTLY into its own ``PositionLedger`` — so the per-strategy realised +
    unrealised + passive numbers add up to the all-strategies total with no
    double-count (distinct strategies hold distinct legs / instrument_keys). One
    entry per distinct ``strategy_id`` (first-seen order); a row with no stamped
    ``strategy_id`` rolls into the ``unattributed`` bucket.
    """
    out: list[dict[str, object]] = []
    for sid in _strategy_ids_in_order(rows):
        strat_rows = [r for r in rows if _row_strategy_id(r) == sid]
        trade_rows = [r for r in strat_rows if r.event_type == EventType.TRADE]
        passive_rows = [r for r in strat_rows if r.event_origin.value == "passive"]
        positions = materialize_position_ledger(
            trade_rows,
            marks=marks,
            as_of=as_of,
            share_class_of=share_class_of,
            instrument_key_by_row_id=instrument_key_by_row_id,
        )
        passive_pnl = sum((r.delta for r in passive_rows), Decimal(0))
        realized = sum((p.realized_pnl or Decimal(0) for p in positions), Decimal(0)) + passive_pnl
        unrealized: Decimal = sum((p.unrealized_pnl or Decimal(0) for p in positions), Decimal(0))
        out.append(
            {
                "strategy_id": sid,
                "realized_pnl": str(realized),
                "unrealized_pnl": str(unrealized),
                "passive_pnl": str(passive_pnl),
                "total_pnl": str(realized + unrealized),
                "position_count": len(positions),
            }
        )
    return out


# ── Attribution-breakdown (raw PnLAttributionRow dict rollups) ──────────────────


def _attr_amount(row: Mapping[str, object]) -> Decimal:
    """Parse a ``PnLAttributionRow`` ``amount`` (string-or-Decimal) to Decimal; 0 on garbage."""
    try:
        return Decimal(str(row.get("amount", "0")))
    except (ArithmeticError, ValueError):
        return Decimal(0)


def _attr_group(rows: Sequence[Mapping[str, object]], key: str) -> list[dict[str, str]]:
    """GROUP-BY ``key`` sum of attribution ``amount`` (deterministic first-seen order)."""
    buckets: OrderedDict[str, Decimal] = OrderedDict()
    for row in rows:
        bucket_key = str(row.get(key, "")) or "unknown"
        buckets[bucket_key] = buckets.get(bucket_key, Decimal(0)) + _attr_amount(row)
    return [{key: k, "amount": str(v)} for k, v in buckets.items()]


def _attr_group_per_strategy(
    rows: Sequence[Mapping[str, object]],
    key: str,
) -> list[dict[str, object]]:
    """Per-strategy nested GROUP-BY: one entry per ``strategy_id`` carrying its own
    ``key`` rollup + per-strategy total.

    The multi-dimensional waterfall the dashboard needs: for each canonical
    ``strategy_id`` (e.g. ``carry_staked_basis`` vs ``arbitrage_price_dispersion``),
    the per-``key`` (factor / venue / layer) sums of ``amount`` so the operator sees
    "this strategy's P&L split by factor" — NOT one collapsed number across all
    strategies. Deterministic first-seen order for both the strategy level and the
    inner ``key`` level.
    """
    by_strategy: OrderedDict[str, OrderedDict[str, Decimal]] = OrderedDict()
    for row in rows:
        strat = str(row.get("strategy_id", "")) or "unknown"
        inner_key = str(row.get(key, "")) or "unknown"
        inner = by_strategy.setdefault(strat, OrderedDict())
        inner[inner_key] = inner.get(inner_key, Decimal(0)) + _attr_amount(row)
    return [
        {
            "strategy_id": strat,
            "total_amount": str(sum(inner.values(), Decimal(0))),
            f"by_{key}": [{key: k, "amount": str(v)} for k, v in inner.items()],
        }
        for strat, inner in by_strategy.items()
    ]


def attribution_breakdown(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Per-venue / per-instrument / per-factor / per-layer / per-strategy attribution rollups (P2.5.1).

    Folds the raw ``PnLAttributionRow`` records (from
    ``attribution_reader.read_attribution_rows`` — each carries ``strategy_id`` /
    ``venue`` / ``instrument_id`` / ``factor`` / ``layer`` / ``amount``) into the
    GROUP-BY sums, plus the grand total. This is the multi-dimensional attribution
    VIEW off ``PnLAttributionRow`` — the operator's "where did the P&L come from"
    breakdown by venue, instrument, factor (CARRY / BASIS / FUNDING / FEES / …),
    layer (STRATEGY / EXECUTION) AND per ``strategy_id`` — NOT one collapsed number.

    ``by_venue`` ≠ ``by_layer`` once the run has real dims: CARRY/BASIS/FEES book at
    the staking venue (LIDO / JITO), FUNDING at the perp venue (DERIBIT / DRIFT), so
    the venue split and the layer split partition the same amount differently.

    ``by_strategy`` carries each strategy's OWN factor split (nested) so the
    dashboard renders a per-strategy waterfall, plus a flat ``per_strategy_total``
    for the top-line per-strategy number. Empty ``rows`` → all-empty rollups +
    ``"0"`` total (honest, never mock).
    """
    total = sum((_attr_amount(r) for r in rows), Decimal(0))
    return {
        "by_venue": _attr_group(rows, "venue"),
        "by_instrument": _attr_group(rows, "instrument_id"),
        "by_factor": _attr_group(rows, "factor"),
        "by_layer": _attr_group(rows, "layer"),
        "per_strategy_total": _attr_group(rows, "strategy_id"),
        "by_strategy": _attr_group_per_strategy(rows, "factor"),
        "total_amount": str(total),
    }


__all__ = [
    "attribution_breakdown",
    "by_strategy_pnl",
    "filter_rows_by_strategy",
]
