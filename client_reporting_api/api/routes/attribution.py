"""Per-client attribution routes — NAV / PnL series / positions / attribution waterfall.

Routes (all under /api/v1/clients/{client_id}/):
  GET /nav         — NAV time-series (date_from / date_to query params)
  GET /pnl         — Daily PnL series
  GET /positions   — Current open positions
  GET /attribution — PnL attribution waterfall by factor x layer

SSOT: plans/active/client_reporting_pnl_attribution_mvp_2026_05_10.md Phase 4.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.attribution_reader import read_attribution_rows
from client_reporting_api.core.entitlement import enforce_entitlement  # pyright: ignore[reportPrivateUsage]
from client_reporting_api.core.ledger_views import (
    attribution_breakdown,
    compute_ledger_views,
    compute_pnl_entries,
    read_batch_total_pnl,
    read_canonical_positions,
    read_canonical_run_fills,
    read_ledger_rows,
    read_marks,
    read_run_window,
    resolve_canonical_run,
)
from client_reporting_api.core.portfolio_metrics import (
    backtest_surface,
    net_views,
    per_strategy_breakdown,
)
from client_reporting_api.core.recon_view import latest_recon_verdict

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/clients/{client_id}", tags=["attribution"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


# ---------------------------------------------------------------------------
# Mock helpers (CLOUD_MOCK_MODE=true / CI mode)
# ---------------------------------------------------------------------------


def _mock_nav(client_id: str, date_from: date | None, date_to: date | None) -> dict[str, object]:
    today = date.today()
    dates = [str(today)]
    return {
        "client_id": client_id,
        "share_class": "USDT",
        "mode": "DEMO",
        "snapshots": [
            {
                "date": d,
                "nav_usd": "100000.00",
                "nav_in_share_class": "100000.00",
                "nav_delta_usd": "500.00",
                "timestamp": datetime.now(UTC).isoformat(),
            }
            for d in dates
        ],
    }


def _mock_pnl(client_id: str, date_from: date | None, date_to: date | None) -> dict[str, object]:
    today = date.today()
    return {
        "client_id": client_id,
        "share_class": "USDT",
        "entries": [
            {
                "period_tag": str(today),
                "realized_pnl": "200.00",
                "unrealized_pnl": "300.00",
                "total_pnl": "500.00",
                "strategy_alpha_total": "450.00",
                "execution_alpha_total": "50.00",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    }


def _mock_attribution(client_id: str, date_from: date | None, date_to: date | None) -> dict[str, object]:
    today = date.today()
    return {
        "client_id": client_id,
        "rows": [
            {
                "strategy_id": "carry_staked_basis",
                "instrument_id": "BTC-PERP",
                "date": str(today),
                "factor": "CARRY",
                "layer": "STRATEGY",
                "amount": "300.00",
            },
            {
                "strategy_id": "carry_staked_basis",
                "instrument_id": "BTC-PERP",
                "date": str(today),
                "factor": "SLIPPAGE",
                "layer": "EXECUTION",
                "amount": "50.00",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Live helpers (reads from attribution parquet via bucket naming SSOT)
# ---------------------------------------------------------------------------


def _nav_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
    date_from: date | None,
    date_to: date | None,
) -> dict[str, object]:
    """Aggregate attribution rows into NAV snapshots per date."""
    by_date: dict[str, Decimal] = {}
    for row in rows:
        ts = row.get("timestamp")
        row_date = str(ts)[:10] if ts else "unknown"
        amount_str = str(row.get("amount", "0"))
        with contextlib.suppress(Exception):
            by_date[row_date] = by_date.get(row_date, Decimal("0")) + Decimal(amount_str)

    snapshots = [
        {
            "date": d,
            "nav_usd": str(total),
            "nav_in_share_class": str(total),
            "nav_delta_usd": str(total),
            "timestamp": f"{d}T00:00:00+00:00",
        }
        for d, total in sorted(by_date.items())
    ]
    return {"client_id": client_id, "share_class": "USDT", "mode": "DEMO", "snapshots": snapshots}


def _attribution_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    """Project raw attribution parquet rows to API response."""
    out: list[dict[str, object]] = []
    for row in rows:
        ts = row.get("timestamp")
        out.append(
            {
                "strategy_id": row.get("strategy_id"),
                "instrument_id": row.get("instrument_id"),
                "date": str(ts)[:10] if ts else None,
                "factor": row.get("factor"),
                "layer": row.get("layer"),
                "amount": row.get("amount"),
                "archetype_id": row.get("archetype_id"),
                "fill_id": row.get("fill_id"),
                "venue": row.get("venue"),
                "benchmark_price": row.get("benchmark_price"),
            }
        )
    return {"client_id": client_id, "rows": out}


def _ledger_views(client_id: str, as_of_date: date | None) -> dict[str, object]:
    """Compute the ledger-derived positions / balances / PnL totals for a client.

    Resolves THE canonical paper run, reads its InstructionLedger ``LedgerRow``
    tape (:func:`read_ledger_rows`) AND its PricingLedger marks
    (:func:`read_marks` → ``{asset_canonical_id -> mark}``), and folds both into
    the operator views — so positions carry mark-to-market unrealized P&L from
    the run's own mark snapshots. When the run has no PricingLedger objects yet
    ``marks`` is ``{}`` → unrealized is an honest 0 per position (no marks to
    apply), distinguished by the ``marks_status`` field. ``share_class_of`` is
    derived per-position by the ledger writer (carried on each row), so no
    separate map is threaded here.
    """
    run_id = resolve_canonical_run(client_id, as_of_date=as_of_date)
    rows, instrument_key_by_row_id = read_ledger_rows(client_id, as_of_date=as_of_date)
    marks: Mapping[str, Decimal] = read_marks(client_id, run_id) if run_id else {}
    share_class_of: Mapping[str, str] = {}
    views = compute_ledger_views(
        rows,
        marks=marks,
        as_of=datetime.now(UTC),
        share_class_of=share_class_of,
        instrument_key_by_row_id=instrument_key_by_row_id,
    )
    views["run_id"] = run_id
    views["marks_status"] = "marked" if marks else "no_marks"
    return views


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/nav")
def get_client_nav(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),  # noqa: B008
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """NAV time-series for a client. Reads attribution parquet, sums amounts per date."""
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_nav(client_id, date_from, date_to)
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    return _nav_from_rows(client_id, rows, date_from, date_to)


@router.get("/pnl")
def get_client_pnl(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),  # noqa: B008
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """Realised + unrealised P&L for a client, DERIVED FROM THE LEDGER (fix 2026-06-20).

    Folds the canonical run's InstructionLedger TRADE tape into the
    ``PositionLedger`` (avg-cost) and emits a per-position ``entries`` list plus the
    realised/unrealised roll-ups — coherent with the ``/positions`` view (same
    ledger, same canonical run). The former implementation keyed ``entries`` off an
    attribution parquet that is empty for a fresh paper run → it returned
    ``entries:[]`` even though the run has 21 fills. P&L now comes from the ledger
    itself, so it is non-empty whenever the run has positions. The per-factor
    STRATEGY/EXECUTION alpha attribution (when an attribution parquet exists) rides
    the dedicated ``/attribution`` + ``/attribution/breakdown`` endpoints.

    All-opening runs legitimately show ``realized_pnl=0`` per leg (correct avg-cost
    accounting — nothing has closed); ``unrealized`` carries the mark-to-market
    DERIVED FROM THE PricingLedger marks (fix 2026-06-20): the canonical run's
    ``ledger_type=pricing`` mark snapshots are read into a ``{asset_canonical_id ->
    mark}`` map and folded into the position ledger, so
    ``unrealized_pnl = Sum (mark - avg_cost) * net_qty`` populates per position + the
    total. When the run has NO PricingLedger objects yet the response is an HONEST
    "no marks" state (``marks_status="no_marks"`` + ``unrealized_pnl_total=null``),
    NOT a fabricated 0 that reads as a real mark-to-market. Honest zero/empty when
    the client has no run.
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_pnl(client_id, date_from, date_to)
    run_id = resolve_canonical_run(client_id, as_of_date=date_to)
    ledger_rows, instrument_key_by_row_id = read_ledger_rows(client_id, as_of_date=date_to)
    marks = read_marks(client_id, run_id) if run_id else {}
    pnl = compute_pnl_entries(
        ledger_rows,
        marks=marks,
        as_of=datetime.now(UTC),
        share_class_of={},
        instrument_key_by_row_id=instrument_key_by_row_id,
    )
    pnl["client_id"] = client_id
    pnl["share_class"] = "USDT"
    pnl["run_id"] = run_id
    pnl["marks_status"] = "marked" if marks else "no_marks"
    if not marks:
        # HONEST absence: with no PricingLedger marks the position ledger marks
        # everything at avg_cost → unrealized is structurally 0. Surface that as a
        # typed "no marks yet" null on the run-level totals + per entry, so the UI
        # never renders a fabricated 0 as a real mark-to-market. Realised P&L +
        # net_qty + avg_cost are unaffected (mark-independent).
        pnl["unrealized_pnl_total"] = None
        pnl["total_pnl"] = pnl.get("realized_pnl_total")  # total = realised only while unmarked
        entries = pnl.get("entries")
        if isinstance(entries, list):
            for entry in cast("list[dict[str, object]]", entries):
                entry["unrealized_pnl"] = None
                entry["total_pnl"] = entry.get("realized_pnl")
    return pnl


@router.get("/positions")
def get_client_positions(
    client_id: str,
    auth: AuthDep,
    as_of: date | None = Query(None, description="Snapshot date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """Current open positions for a client — REAL, ledger-derived (P3.4 + P5.1).

    Returns the client's ``PositionLedgerRow`` list (``Σ delta`` with average-cost
    realised/unrealised PnL) plus per-venue / per-instrument / per-share_class
    balance rollups and the realised+unrealised PnL totals. The ledger source is
    a pluggable seam (:func:`read_ledger_rows`) returning ``[]`` until the
    engine-wiring phase populates the GCS ledger — so today this is an HONEST
    empty/zero response (positions ``[]``, totals ``"0"``), never mock data.
    """
    enforce_entitlement(auth, client_id)
    views = _ledger_views(client_id, as_of)
    views["client_id"] = client_id
    return views


@router.get("/attribution")
def get_client_attribution(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),  # noqa: B008
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """PnL attribution waterfall by factor x layer for a client.

    Returns raw PnLAttributionRow records grouped by (strategy_id, instrument, factor, layer).
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_attribution(client_id, date_from, date_to)
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    return _attribution_from_rows(client_id, rows)


@router.get("/attribution/breakdown")
def get_client_attribution_breakdown(
    client_id: str,
    auth: AuthDep,
    date_from: date | None = Query(None, description="Start date (inclusive) YYYY-MM-DD"),  # noqa: B008
    date_to: date | None = Query(None, description="End date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """Per-venue / per-instrument / per-factor / per-layer attribution rollups (P2.5.1).

    Folds the client's ``PnLAttributionRow`` records into GROUP-BY sums by venue,
    instrument, factor and layer (plus the grand total) — the operator's
    "where did the P&L come from" breakdown. Empty (no attribution shards yet) →
    honest all-empty rollups + ``"0"`` total, never mock.
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        mock_rows = _mock_attribution(client_id, date_from, date_to)["rows"]
        rows_typed = cast("list[dict[str, object]]", mock_rows) if isinstance(mock_rows, list) else []
        breakdown = attribution_breakdown(rows_typed)
        breakdown["client_id"] = client_id
        return breakdown
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    breakdown = attribution_breakdown(rows)
    breakdown["client_id"] = client_id
    return breakdown


# ---------------------------------------------------------------------------
# Paper-trading dashboard ledger panels (P2.5.2): instructions / transfers /
# reconciliation. Read the SAME GCS InstructionLedger the engine writes (via
# read_ledger_rows) so the operator dashboard renders a REAL paper run. Honest
# empty/PENDING when no run exists — never mock.
# ---------------------------------------------------------------------------

_TRANSFER_EVENT_TYPES = frozenset({"transfer", "deposit", "withdrawal", "bridge"})


def _side_from_delta(delta: Decimal, direction: object) -> str:
    """UI side ('long'/'short'/'flat') from the row direction or the signed delta."""
    if isinstance(direction, str) and direction:
        upper = direction.upper()
        if upper in ("LONG", "BUY", "SUPPLY"):
            return "long"
        if upper in ("SHORT", "SELL", "WITHDRAW"):
            return "short"
    if delta > 0:
        return "long"
    if delta < 0:
        return "short"
    return "flat"


@router.get("/instructions")
def get_client_instructions(
    client_id: str,
    auth: AuthDep,
    limit: int = Query(100, description="Max instructions to return (most-recent first)"),
) -> dict[str, object]:
    """Strategy-instruction tape (the InstructionLedger) for the paper-trading dashboard.

    Reads the client's REAL InstructionLedger rows from GCS (``read_ledger_rows``)
    and projects the TRADE rows to the dashboard's ``LedgerInstruction`` shape:
    what the strategy decided each tick (action / venue / instrument / side /
    target qty / benchmark price). Honest empty when the client has no run yet.
    """
    enforce_entitlement(auth, client_id)
    run_id = resolve_canonical_run(client_id)
    rows, _instrument_keys = read_ledger_rows(client_id)
    instructions: list[dict[str, object]] = []
    for row in rows:
        if str(row.event_type) != "trade":
            continue
        delta = row.delta
        trade_id = row.trade_id or ""
        qty_str = str(abs(delta))
        instructions.append(
            {
                "instruction_id": trade_id,
                "strategy_id": row.asset_canonical_id,
                "action": "TRADE",
                "venue": row.venue,
                "instrument_key": trade_id.split("|")[0],
                "side": _side_from_delta(delta, str(row.direction) if row.direction else None),
                "target_qty": qty_str,
                # `size`/`quantity` aliases so any UI panel field name surfaces the
                # qty (the LedgerInstruction renders `target_qty`; aliases are belt+braces).
                "size": qty_str,
                "quantity": qty_str,
                "benchmark_price": str(row.price if row.price is not None else "0"),
                "timestamp": row.timestamp_utc.isoformat(),
                "correlation_id": row.event_id,
                "status": "filled",
            }
        )
    instructions = instructions[-limit:]
    return {"client_id": client_id, "run_id": run_id, "instructions": instructions, "total": len(instructions)}


@router.get("/trades")
def get_client_trades(
    client_id: str,
    auth: AuthDep,
    limit: int = Query(500, description="Max trades to return (most-recent first)"),
) -> dict[str, object]:
    """Trade fills derived from the canonical paper run's InstructionLedger.

    The dashboard's executed-fills tape: resolves the SAME canonical run as the
    positions/pnl/instructions/reconciliation views and projects each
    :class:`TradeFillRecord` (timestamp / venue / side / instrument_key / qty /
    fill_price / notional) to the UI's ``LedgerTrade`` shape. These are the exact
    fills the reconciliation matched, so the tape is coherent across panels.

    Mirrors the legacy ``/api/v1/trades?client_id=`` route's ledger source under
    the per-client ``/api/v1/clients/{client_id}/trades`` path. Honest empty when
    the client has no run yet.
    """
    enforce_entitlement(auth, client_id)
    run_id, fills = read_canonical_run_fills(client_id)
    trades: list[dict[str, object]] = []
    for fill in fills:
        qty = abs(fill.qty)
        price = fill.fill_price
        side_upper = fill.side.strip().upper()
        ui_side = "buy" if side_upper in ("BUY", "LONG", "SUPPLY", "YES", "BACK") else "sell"
        trades.append(
            {
                "trade_id": fill.trade_key,
                "venue": fill.venue,
                "symbol": fill.instrument_key,
                "instrument_key": fill.instrument_key,
                "side": ui_side,
                "quantity": str(qty),
                "fill_price": str(price),
                "price": str(price),
                "fee": str(fill.fees_in_quote),
                "fee_currency": "USDC",
                "realized_pnl": "0",
                "timestamp": fill.tick_timestamp.isoformat(),
                "order_id": fill.strategy_instruction_id,
                "trade_type": "paper",
                "notional_usd": str(qty * price),
            }
        )
    trades.sort(key=lambda t: str(t.get("timestamp", "")), reverse=True)
    trades = trades[:limit]
    return {"client_id": client_id, "run_id": run_id, "trades": trades, "total": len(trades)}


@router.get("/transfers")
def get_client_transfers(
    client_id: str,
    auth: AuthDep,
) -> dict[str, object]:
    """Wallet transfers / money movements (TRANSFER / DEPOSIT / BRIDGE ledger rows).

    Single-client scope (funds never cross clients). Reads the canonical run's
    ledger rows and projects the non-trade money-movement events (TRANSFER /
    DEPOSIT / WITHDRAWAL / BRIDGE). When the run carries NO money-movement rows
    (the carry_staked_basis paper run is all TRADE legs — the capital moves are
    modelled as trades into the staking/perp legs, not separate TRANSFER rows) the
    response is a TYPED honest-empty (``status="NO_TRANSFER_ROWS"`` + a ``note``),
    distinguishing "the run genuinely has no transfers" from "this panel is broken"
    — never a silent bare ``0`` that reads as a bug.
    """
    enforce_entitlement(auth, client_id)
    run_id = resolve_canonical_run(client_id)
    rows, _instrument_keys = read_ledger_rows(client_id)
    transfers: list[dict[str, object]] = []
    for row in rows:
        evt = str(row.event_type).lower()
        if evt not in _TRANSFER_EVENT_TYPES:
            continue
        delta = row.delta
        venue = row.venue
        price = row.price if row.price is not None else Decimal("0")
        transfers.append(
            {
                "transfer_id": row.event_id,
                "event_type": evt.upper(),
                "from_venue": "" if delta >= 0 else venue,
                "to_venue": venue if delta >= 0 else "",
                "asset_symbol": row.asset_symbol,
                "share_class": row.asset_canonical_id,
                "amount": str(delta),
                "notional_usd": str(abs(delta) * price),
                "timestamp": row.timestamp_utc.isoformat(),
                "client_id": client_id,
            }
        )
    if run_id is None:
        status = "NO_RUN"
        note = "No paper run exists for this client yet."
    elif not transfers:
        status = "NO_TRANSFER_ROWS"
        note = (
            "This run's ledger carries no money-movement (TRANSFER/DEPOSIT/WITHDRAWAL/BRIDGE) rows — "
            "the carry_staked_basis run models capital movement as TRADE legs into the staking/perp "
            "positions, not as separate transfer events. See /positions and /trades for the real flow."
        )
    else:
        status = "OK"
        note = ""
    return {
        "client_id": client_id,
        "run_id": run_id,
        "transfers": transfers,
        "total": len(transfers),
        "status": status,
        "note": note,
    }


@router.get("/reconciliation/latest")
def get_client_reconciliation_latest(
    client_id: str,
    auth: AuthDep,
) -> dict[str, object]:
    """Latest daily T+1 reconcile_day determinism verdict (paper ≡ batch, ε=0).

    The headline "is paper deterministic?" badge for the dashboard. Reads the
    client's paper InstructionLedger + its batch-rerun ledger (written under the
    paper run's ``__batch__/`` prefix by the batch-rerun stage) and keys the fills
    by ``trade_id`` (the deterministic UAC trade_key) — DETERMINISTIC iff every
    paper fill matches a batch fill on (price, qty-sign) with no unmatched. This is
    the SAME keyed comparison ``reconcile_day`` performs; computed inline here to
    keep client-reporting-api service-dep-clean (no BLRS import). Honest verdicts:
    NO_DATA (no run) / PENDING (paper present, batch rerun not yet) / DETERMINISTIC
    / DRIFT (a real bug, with the per-trade deviations).
    """
    enforce_entitlement(auth, client_id)
    return latest_recon_verdict(client_id)


# ---------------------------------------------------------------------------
# Phase-10 portfolio metrics: net views / per-strategy / bps / ROE / backtest.
# All run-scoped (THE canonical run), derived from the SAME ledger surface the
# positions/pnl views fold. Honest typed-empty where a source is absent.
# ---------------------------------------------------------------------------

_DEFAULT_WINDOW_DAYS = Decimal("7")  # honest fallback when the manifest has no window


def _window_days(run_id: str | None, client_id: str) -> tuple[Decimal, tuple[str, ...]]:
    """``(window_days, strategy_ids)`` from the run manifest (honest fallback to 7d)."""
    if run_id is None:
        return _DEFAULT_WINDOW_DAYS, ()
    window_start, window_end, strategy_ids, _fill_model = read_run_window(client_id, run_id)
    if window_start is None or window_end is None:
        return _DEFAULT_WINDOW_DAYS, strategy_ids
    days = Decimal((window_end - window_start).total_seconds()) / Decimal("86400")
    return (days if days > 0 else _DEFAULT_WINDOW_DAYS), strategy_ids


@router.get("/net-views")
def get_client_net_views(
    client_id: str,
    auth: AuthDep,
    as_of: date | None = Query(None, description="Snapshot date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """net-in-dollars / net-in-coin / delta-per-coin for the canonical run (P10.3).

    Folds THE canonical run's ``PositionLedger`` (marks joined) into: portfolio
    net+gross USD value at marks, net qty per coin, and the signed USD delta
    exposure per coin (≈0 per coin for the delta-neutral books). Honest zero/empty
    when the client has no run yet.
    """
    enforce_entitlement(auth, client_id)
    run_id, positions = read_canonical_positions(client_id, as_of_date=as_of)
    views = net_views(positions)
    views["client_id"] = client_id
    views["run_id"] = run_id
    return views


@router.get("/per-strategy")
def get_client_per_strategy(
    client_id: str,
    auth: AuthDep,
    as_of: date | None = Query(None, description="Snapshot date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """Per-strategy trades / positions / P&L / turnover / bps / ROE + overall (P10.4/8/9).

    Groups by the canonical ``@``-qualified ``strategy_id`` (mapped from each row's
    venue via the run manifest's ``strategy_ids``). Each strategy carries
    ``bps_pnl_on_turnover`` (P10.8) and ``roe_annualised_pct`` (P10.9) alongside the
    P&L + turnover; an ``overall`` roll-up covers all strategies. Honest typed-empty
    (``None`` bps/ROE) when a denominator is 0.
    """
    enforce_entitlement(auth, client_id)
    run_id, positions = read_canonical_positions(client_id, as_of_date=as_of)
    _resolved_run, fills = read_canonical_run_fills(client_id, as_of_date=as_of)
    window_days, strategy_ids = _window_days(run_id, client_id)
    breakdown = per_strategy_breakdown(positions, fills, strategy_ids, window_days=window_days)
    breakdown["client_id"] = client_id
    breakdown["run_id"] = run_id
    return breakdown


@router.get("/bps-pnl")
def get_client_bps_pnl(
    client_id: str,
    auth: AuthDep,
    as_of: date | None = Query(None, description="Snapshot date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """bps PnL on turnover (``total_pnl / Σ|notional| * 1e4``) per strategy + overall (P10.8).

    A focused projection of :func:`per_strategy_breakdown` — the per-strategy +
    overall ``bps_pnl_on_turnover`` with the turnover + total_pnl it derives from.
    Honest ``None`` bps when a strategy has zero turnover.
    """
    enforce_entitlement(auth, client_id)
    run_id, positions = read_canonical_positions(client_id, as_of_date=as_of)
    _resolved_run, fills = read_canonical_run_fills(client_id, as_of_date=as_of)
    window_days, strategy_ids = _window_days(run_id, client_id)
    breakdown = per_strategy_breakdown(positions, fills, strategy_ids, window_days=window_days)
    strategies = breakdown["strategies"]
    overall = breakdown["overall"]
    per = [
        {
            "strategy_id": s["strategy_id"],
            "total_pnl": s["total_pnl"],
            "turnover_usd": s["turnover_usd"],
            "bps_pnl_on_turnover": s["bps_pnl_on_turnover"],
        }
        for s in cast("list[dict[str, object]]", strategies)
    ]
    overall_d = cast("dict[str, object]", overall)
    return {
        "client_id": client_id,
        "run_id": run_id,
        "by_strategy": per,
        "overall": {
            "total_pnl": overall_d["total_pnl"],
            "turnover_usd": overall_d["turnover_usd"],
            "bps_pnl_on_turnover": overall_d["bps_pnl_on_turnover"],
        },
    }


@router.get("/roe")
def get_client_roe(
    client_id: str,
    auth: AuthDep,
    as_of: date | None = Query(None, description="Snapshot date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """% ROE annualised over the run window per strategy + overall (P10.9).

    A focused projection of :func:`per_strategy_breakdown` — the per-strategy +
    overall ``roe_annualised_pct`` with the equity (``gross_usd``) + window it
    derives from. Honest ``None`` ROE when equity is 0 / the window is undefined.
    """
    enforce_entitlement(auth, client_id)
    run_id, positions = read_canonical_positions(client_id, as_of_date=as_of)
    _resolved_run, fills = read_canonical_run_fills(client_id, as_of_date=as_of)
    window_days, strategy_ids = _window_days(run_id, client_id)
    breakdown = per_strategy_breakdown(positions, fills, strategy_ids, window_days=window_days)
    strategies = breakdown["strategies"]
    overall = breakdown["overall"]
    per = [
        {
            "strategy_id": s["strategy_id"],
            "total_pnl": s["total_pnl"],
            "gross_usd": s["gross_usd"],
            "roe_annualised_pct": s["roe_annualised_pct"],
        }
        for s in cast("list[dict[str, object]]", strategies)
    ]
    overall_d = cast("dict[str, object]", overall)
    return {
        "client_id": client_id,
        "run_id": run_id,
        "window_days": breakdown["window_days"],
        "by_strategy": per,
        "overall": {
            "total_pnl": overall_d["total_pnl"],
            "gross_usd": overall_d["gross_usd"],
            "roe_annualised_pct": overall_d["roe_annualised_pct"],
        },
    }


@router.get("/backtest")
def get_client_backtest(
    client_id: str,
    auth: AuthDep,
    as_of: date | None = Query(None, description="Snapshot date (inclusive) YYYY-MM-DD"),  # noqa: B008
) -> dict[str, object]:
    """Backtest surface: historical PnL + execution cost + execution assumptions (P10.7).

    Reads the canonical run's ``__batch__`` rerun (historical PnL), surfaces the
    execution cost (= execution alpha = smart - benchmark; structurally 0 in the
    benchmark-only paper/batch, stated honestly) and the execution assumptions (the
    ``RunManifest.fill_model`` + the fill-model fidelity tier), plus a paper-vs-batch
    comparison payload for the UI's unified view. Honest PENDING when the canonical
    run has no batch rerun yet.
    """
    enforce_entitlement(auth, client_id)
    run_id = resolve_canonical_run(client_id, as_of_date=as_of)
    if run_id is None:
        return {
            "client_id": client_id,
            "run_id": None,
            "status": "NO_RUN",
            "note": "No paper run exists for this client yet.",
        }
    # Paper total PnL for the canonical run (realised + unrealised at marks).
    ledger_rows, instrument_key_by_row_id = read_ledger_rows(client_id, as_of_date=as_of)
    marks = read_marks(client_id, run_id)
    paper_pnl = compute_pnl_entries(
        ledger_rows,
        marks=marks,
        as_of=datetime.now(UTC),
        share_class_of={},
        instrument_key_by_row_id=instrument_key_by_row_id,
    )
    paper_total = Decimal(str(paper_pnl["total_pnl"]))
    window_start, window_end, _strategy_ids, fill_model = read_run_window(client_id, run_id)
    batch_run_id, batch_total = read_batch_total_pnl(client_id, run_id)
    recon = latest_recon_verdict(client_id)
    matched = recon.get("matched_trades") if recon.get("paper_run_id") == run_id else None
    surface = backtest_surface(
        fill_model=fill_model or "BENCHMARK",
        window_start=window_start or datetime.now(UTC),
        window_end=window_end or datetime.now(UTC),
        paper_total_pnl=paper_total,
        batch_total_pnl=batch_total,
        batch_run_id=batch_run_id,
        matched_trades=matched if isinstance(matched, int) else None,
    )
    surface["client_id"] = client_id
    surface["run_id"] = run_id
    return surface
