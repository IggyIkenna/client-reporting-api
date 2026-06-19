"""High-water mark driven off the MATERIALISED ledger NAV — never ``max(equities)``.

HARD RULE (CLAUDE.md / codex/09-strategy/operational/pnl-attribution.md): the HWM
is NEVER raw equity. The NAV that drives the mark is the **materialised**
realised+unrealised P&L folded out of the ``LedgerRow`` tape by
:func:`client_reporting_api.core.ledger_views.compute_ledger_views` (seed +
cumulative ``totals.total_pnl``), NOT a peak over a raw equity curve.

Three simultaneous HWM methods exist (TWR / Notional / PnL-recovery — see
``core/hwm_seeds.py`` + ``core/backfill_store.py``). This module materialises the
**Notional/ledger-NAV** HWM ledger: a monotonically non-decreasing peak over the
ledger-derived NAV series, emitted as UAC
:class:`~unified_api_contracts.internal.HighWaterMarkLedgerRow` snapshots.

Pure functions (no I/O). The HWM advances ONLY when ``nav > prior_peak_nav``;
``delta = max(0, nav - prior_peak_nav)`` so it never retreats through a drawdown.

SSOT: codex/09-strategy/operational/paper-batch-live-reconciliation.md §1 +
codex/09-strategy/operational/pnl-attribution.md.
Plan: plans/active/citadel_paper_batch_live_reconciliation_2026_06_19.md (P3.5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from unified_api_contracts.internal import HighWaterMarkLedgerRow


class LedgerHwmInvariantError(ValueError):
    """Raised when a materialised HWM ledger violates the HWM invariants.

    Mirrors the checks in
    ``unified_trading_library.post_trade.hwm_invariants.assert_hwm_invariants``
    (which asserts on a ``PerformanceFeeCrystallizedEvent``, not a NAV series, so
    we cannot call it directly here — we assert the same monotonicity + sign +
    ordering invariants on the emitted ``HighWaterMarkLedgerRow`` series).
    """


def ledger_nav_series(
    daily_views: Sequence[tuple[datetime, Mapping[str, object]]],
    *,
    seed: Decimal,
) -> list[tuple[datetime, Decimal]]:
    """Turn daily ``compute_ledger_views`` outputs into a NAV series.

    NAV at each point = ``seed + cumulative total P&L`` (the materialised
    realised+unrealised total). Because ``compute_ledger_views`` returns the
    CUMULATIVE total P&L over the tape it has seen up to ``as_of`` (its ``rows``
    are the running tape, not a per-day delta), the per-point NAV is simply
    ``seed + totals.total_pnl`` for that snapshot — no further accumulation.

    Args:
        daily_views: ordered ``(as_of, views)`` pairs — one per T+1 reporting
            day. ``views`` is a :func:`compute_ledger_views` result dict; its
            ``totals.total_pnl`` is a Decimal-stable string.
        seed: the historical NAV seed in native units (e.g. from
            ``core/hwm_seeds.get_hwm_seed`` — the equity at which our curve
            starts). ``Decimal(0)`` for a from-scratch account.

    Returns:
        ``[(as_of, nav)]`` with ``nav = seed + Decimal(totals.total_pnl)``,
        preserving the input order.
    """

    series: list[tuple[datetime, Decimal]] = []
    for as_of, views in daily_views:
        totals = views.get("totals")
        if not isinstance(totals, Mapping):
            raise LedgerHwmInvariantError(
                f"compute_ledger_views output missing 'totals' mapping at as_of={as_of.isoformat()}"
            )
        total_pnl_raw = totals.get("total_pnl")
        if not isinstance(total_pnl_raw, str):
            raise LedgerHwmInvariantError(
                f"totals.total_pnl must be a Decimal-string at as_of={as_of.isoformat()}, "
                f"got {type(total_pnl_raw).__name__}"
            )
        series.append((as_of, seed + Decimal(total_pnl_raw)))
    return series


def hwm_from_ledger(
    nav_series: Sequence[tuple[datetime, Decimal]],
    *,
    prior_peak_nav: Decimal,
    client_id: str,
    share_class_id: str,
    period_start: datetime,
    period_end: datetime,
) -> list[HighWaterMarkLedgerRow]:
    """Materialise the HWM ledger off the ledger-derived NAV series.

    For each ``(as_of, nav)``: the running ``prior_peak_nav`` carried forward is
    the running max; ``delta = max(0, nav - prior_peak_nav)`` so the HWM advances
    on a new all-time high and is FLAT (delta 0) through any drawdown — it never
    retreats. This drives the HWM off the materialised ledger NAV (the
    realised+unrealised total), NOT ``max(equities)``.

    Args:
        nav_series: ordered ``(as_of, nav)`` — typically :func:`ledger_nav_series`
            output. NAV is the materialised ledger NAV, never raw equity.
        prior_peak_nav: the HWM carried in from before this series starts
            (e.g. the ledger-NAV seed peak, or a recovery seed). The first point
            can only advance the peak if its NAV exceeds this.
        client_id: client identifier (stamped on every emitted row).
        share_class_id: share class identifier (stamped on every emitted row).
        period_start: start of the current crystallization period (UTC, tz-aware).
        period_end: end of the current crystallization period (UTC, exclusive).

    Returns:
        One :class:`HighWaterMarkLedgerRow` per NAV point, with ``prior_peak_nav``
        = the running peak BEFORE this point's possible advance, ``nav`` = the
        materialised NAV, ``delta`` = the (>=0) advance. ``crystallization_due``
        is always ``False`` here — wall-clock crystallization is a separate
        concern (this function only materialises the HWM track).

    Raises:
        LedgerHwmInvariantError: if ``period_end <= period_start``, or if the
            emitted series violates HWM monotonicity / delta-sign invariants.
    """

    if period_end <= period_start:
        raise LedgerHwmInvariantError(
            f"period_end ({period_end.isoformat()}) must be > period_start ({period_start.isoformat()})"
        )

    rows: list[HighWaterMarkLedgerRow] = []
    running_peak = prior_peak_nav
    for as_of, nav in nav_series:
        delta = max(Decimal(0), nav - running_peak)
        rows.append(
            HighWaterMarkLedgerRow(
                client_id=client_id,
                share_class_id=share_class_id,
                as_of=as_of,
                nav=nav,
                prior_peak_nav=running_peak,
                delta=delta,
                crystallization_due=False,
                period_start=period_start,
                period_end=period_end,
            )
        )
        # HWM advances only on a new high; flat through a drawdown.
        running_peak = max(running_peak, nav)

    _assert_hwm_invariants(rows)
    return rows


def _assert_hwm_invariants(rows: Sequence[HighWaterMarkLedgerRow]) -> None:
    """Assert the HWM invariants on a materialised ledger.

    Mirrors ``unified_trading_library.post_trade.hwm_invariants.assert_hwm_invariants``
    for the NAV-series shape:

    1. ``period_end > period_start`` (per row).
    2. ``delta >= 0`` (per row — HWM never retreats).
    3. ``delta == max(0, nav - prior_peak_nav)`` (per row — delta is the advance).
    4. ``hwm_at_end >= hwm_at_start`` — the running peak (``prior_peak_nav + delta``)
       is monotonically non-decreasing across the series.

    Raises:
        LedgerHwmInvariantError: on any violation.
    """

    prev_peak: Decimal | None = None
    for row in rows:
        if row.period_end <= row.period_start:
            raise LedgerHwmInvariantError(f"period_end <= period_start at as_of={row.as_of.isoformat()}")
        if row.delta < Decimal(0):
            raise LedgerHwmInvariantError(f"delta < 0 ({row.delta}) at as_of={row.as_of.isoformat()}")
        expected_delta = max(Decimal(0), row.nav - row.prior_peak_nav)
        if row.delta != expected_delta:
            raise LedgerHwmInvariantError(
                f"delta {row.delta} != max(0, nav - prior_peak_nav) {expected_delta} at as_of={row.as_of.isoformat()}"
            )
        peak = row.prior_peak_nav + row.delta  # the HWM AFTER this point
        if prev_peak is not None and peak < prev_peak:
            raise LedgerHwmInvariantError(f"hwm retreated: {peak} < prior {prev_peak} at as_of={row.as_of.isoformat()}")
        prev_peak = peak
