"""Ledger-derived operator-facing views — positions / balances / PnL totals.

Replaces the mock positions feed and the hardcoded ``realized_pnl="0.00"`` in
``api/routes/attribution.py`` with REAL, ledger-derived state. Given a client's
``LedgerRow`` tape (the as-if-filled fills + passive accruals) the pure
:func:`compute_ledger_views` folds it into:

- **positions** — the ``PositionLedgerRow`` list (``Σ delta`` with average-cost
  realised/unrealised PnL, via UTL :func:`materialize_position_ledger`).
- **balances** — GROUP-BY rollups of ``net_qty`` + ``unrealized_pnl`` over the
  position rows, sliced **by venue / by instrument / by share_class** (P5.1).
- **PnL totals** — ``Σ realized_pnl`` + ``Σ unrealized_pnl`` over the position
  rows — this is what replaces the hardcoded ``"0.00"``.

The ledger SOURCE is a pluggable seam (:func:`read_ledger_rows`) that returns
``[]`` until the engine-wiring phase populates the GCS ledger. With an empty
ledger the views are an HONEST zero/empty response (not mock data).

SSOT: codex/09-strategy/operational/paper-batch-live-reconciliation.md §1+§4.3
Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P3.4 + P5.1).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

from unified_api_contracts import EventType, LedgerRow, PositionLedgerRow
from unified_trading_library.ledger.materialize import (  # noqa: qg-deep-import
    materialize_position_ledger,  # not re-exported at UTL top level
)


def read_ledger_rows(
    client_id: str,
    as_of_date: date | None = None,
) -> list[LedgerRow]:
    """Read a client's ``LedgerRow`` tape (pluggable seam).

    Returns ``[]`` until the engine-wiring phase populates the GCS ledger
    (``gs://…/runs/{run_id}/ledger/{instruction,passive,…}/``). This is the
    honest empty-but-correct path: callers materialise zero positions / zero PnL
    rather than returning mock data. A real GCS reader replaces this body in the
    ledger-materialisation phase — the computation in :func:`compute_ledger_views`
    is the deliverable here and works unchanged once rows arrive.

    Args:
        client_id: the client whose ledger to read (single-client scope — funds
            never cross clients).
        as_of_date: optional snapshot date; reserved for the GCS reader.
    """

    _ = (client_id, as_of_date)  # seam — no GCS reader yet (engine-wiring phase)
    return []


def _decimal_str(value: Decimal | None) -> str | None:
    """Serialise a Decimal to a string (JSON-stable, no float rounding)."""

    return None if value is None else str(value)


def _rollup(
    rows: Sequence[PositionLedgerRow],
    key: str,
) -> list[dict[str, object]]:
    """GROUP-BY ``key`` rollup of ``net_qty`` + ``unrealized_pnl`` + ``realized_pnl``.

    ``key`` is the ``PositionLedgerRow`` attribute name to group on
    (``"venue"`` / ``"instrument_key"`` / ``"share_class"``). Sums are Decimal;
    the result preserves first-seen group order (deterministic).
    """

    buckets: OrderedDict[str, dict[str, Decimal]] = OrderedDict()
    for row in rows:
        bucket_key = str(getattr(row, key))
        agg = buckets.setdefault(
            bucket_key,
            {
                "net_qty": Decimal(0),
                "unrealized_pnl": Decimal(0),
                "realized_pnl": Decimal(0),
            },
        )
        agg["net_qty"] += row.net_qty
        agg["unrealized_pnl"] += row.unrealized_pnl or Decimal(0)
        agg["realized_pnl"] += row.realized_pnl or Decimal(0)

    return [
        {
            key: bucket_key,
            "net_qty": str(agg["net_qty"]),
            "unrealized_pnl": str(agg["unrealized_pnl"]),
            "realized_pnl": str(agg["realized_pnl"]),
        }
        for bucket_key, agg in buckets.items()
    ]


def realized_pnl_total(
    rows: Sequence[LedgerRow],
    *,
    marks: Mapping[str, Decimal],
    as_of: datetime,
    share_class_of: Mapping[str, str],
) -> Decimal:
    """Return the ledger-derived realised PnL total (typed Decimal).

    Σ over the ``PositionLedgerRow`` realised PnL (avg-cost trade closes, net of
    fees) PLUS the PASSIVE accrual cash flows. This is the value that replaces the
    former hardcoded ``realized_pnl="0.00"`` in the pnl route. ``Decimal(0)`` for
    an empty ledger (honest zero, not a placeholder).
    """

    trade_rows = [r for r in rows if r.event_type == EventType.TRADE]
    passive_rows = [r for r in rows if r.event_origin.value == "passive"]
    positions = materialize_position_ledger(
        trade_rows,
        marks=marks,
        as_of=as_of,
        share_class_of=share_class_of,
    )
    passive_pnl = sum((r.delta for r in passive_rows), Decimal(0))
    return sum((p.realized_pnl or Decimal(0) for p in positions), Decimal(0)) + passive_pnl


def _position_view(row: PositionLedgerRow) -> dict[str, object]:
    """Project a ``PositionLedgerRow`` to the operator-facing position dict."""

    return {
        "account_id": row.account_id,
        "client_id": row.client_id,
        "venue": row.venue,
        "instrument_key": row.instrument_key,
        "asset_canonical_id": row.asset_canonical_id,
        "asset_symbol": row.asset_symbol,
        "asset_class": row.asset_class.value,
        "share_class": row.share_class,
        "net_qty": str(row.net_qty),
        "avg_cost": _decimal_str(row.avg_cost),
        "mark_price": _decimal_str(row.mark_price),
        "quote_currency": row.quote_currency,
        "realized_pnl": _decimal_str(row.realized_pnl),
        "unrealized_pnl": _decimal_str(row.unrealized_pnl),
        "as_of": row.as_of.isoformat(),
    }


def compute_ledger_views(
    rows: Sequence[LedgerRow],
    *,
    marks: Mapping[str, Decimal],
    as_of: datetime,
    share_class_of: Mapping[str, str],
) -> dict[str, object]:
    """Fold a client's ledger tape into the operator-facing views.

    Pure function (no I/O). Materialises the ``PositionLedger`` via UTL
    :func:`materialize_position_ledger` then derives the balance rollups and the
    realised/unrealised PnL totals.

    Args:
        rows: the client's ``LedgerRow`` tape (TRADE fills + PASSIVE accruals).
        marks: ``asset_canonical_id -> mark price`` (joins on the per-group key).
        as_of: UTC instant the snapshot is valid at (must be tz-aware UTC).
        share_class_of: ``asset_canonical_id -> share_class`` (falls back to the
            canonical id when absent).

    Returns:
        A dict with ``positions`` (list of position dicts), ``balances`` (the
        ``by_venue`` / ``by_instrument`` / ``by_share_class`` rollups), and
        ``totals`` (``realized_pnl`` / ``unrealized_pnl`` / ``total_pnl`` strings).
        For an empty ``rows`` the positions + rollups are empty and totals are
        ``"0"`` — an honest zero response, never mock data.

    Only ``TRADE`` rows drive the ``PositionLedger`` (``Σ delta`` of the BASE
    asset). ``PASSIVE`` accrual rows (funding / staking / lending) carry a QUOTE
    cash flow in ``delta``, NOT a base-asset quantity, so feeding them to the
    position fold would corrupt ``net_qty``; they are summed into realised PnL
    separately (the carry IS the P&L for funding/basis strategies).
    """

    trade_rows = [r for r in rows if r.event_type == EventType.TRADE]
    passive_rows = [r for r in rows if r.event_origin.value == "passive"]

    positions = materialize_position_ledger(
        trade_rows,
        marks=marks,
        as_of=as_of,
        share_class_of=share_class_of,
    )

    passive_pnl = sum((r.delta for r in passive_rows), Decimal(0))
    realized_total = sum((p.realized_pnl or Decimal(0) for p in positions), Decimal(0)) + passive_pnl
    unrealized_total: Decimal = sum((p.unrealized_pnl or Decimal(0) for p in positions), Decimal(0))

    return {
        "positions": [_position_view(p) for p in positions],
        "balances": {
            "by_venue": _rollup(positions, "venue"),
            "by_instrument": _rollup(positions, "instrument_key"),
            "by_share_class": _rollup(positions, "share_class"),
        },
        "totals": {
            "realized_pnl": str(realized_total),
            "unrealized_pnl": str(unrealized_total),
            "total_pnl": str(realized_total + unrealized_total),
        },
    }
