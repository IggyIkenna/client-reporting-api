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
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.attribution_reader import read_attribution_rows
from client_reporting_api.core.entitlement import enforce_entitlement  # pyright: ignore[reportPrivateUsage]
from client_reporting_api.core.ledger_views import (
    attribution_breakdown,
    compute_ledger_views,
    read_ledger_rows,
    realized_pnl_total,
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


def _pnl_from_rows(
    client_id: str,
    rows: list[dict[str, object]],
    realized_pnl_total: Decimal,
) -> dict[str, object]:
    """Aggregate attribution rows into per-date PnL entries.

    ``realized_pnl_total`` is the ledger-derived realised PnL (``Σ`` over the
    client's ``PositionLedgerRow``s) — it replaces the former hardcoded
    ``"0.00"``. It is attributed to the latest period (most recent date) since
    realised PnL is a running cumulative figure, not a per-attribution-row split.
    """
    by_date: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        ts = row.get("timestamp")
        row_date = str(ts)[:10] if ts else "unknown"
        layer = str(row.get("layer", ""))
        amount_str = str(row.get("amount", "0"))
        try:
            amount = Decimal(amount_str)
        except ArithmeticError:
            continue
        if row_date not in by_date:
            by_date[row_date] = {
                "total": Decimal("0"),
                "strategy": Decimal("0"),
                "execution": Decimal("0"),
            }
        by_date[row_date]["total"] += amount
        if layer == "STRATEGY":
            by_date[row_date]["strategy"] += amount
        elif layer == "EXECUTION":
            by_date[row_date]["execution"] += amount

    sorted_dates = sorted(by_date)
    latest_date = sorted_dates[-1] if sorted_dates else None
    entries = [
        {
            "period_tag": d,
            "realized_pnl": str(realized_pnl_total) if d == latest_date else "0",
            "unrealized_pnl": str(totals["total"]),
            "total_pnl": str(totals["total"] + (realized_pnl_total if d == latest_date else Decimal(0))),
            "strategy_alpha_total": str(totals["strategy"]),
            "execution_alpha_total": str(totals["execution"]),
            "timestamp": f"{d}T00:00:00+00:00",
        }
        for d in sorted_dates
        for totals in [by_date[d]]
    ]
    return {
        "client_id": client_id,
        "share_class": "USDT",
        "realized_pnl_total": str(realized_pnl_total),
        "entries": entries,
    }


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

    Reads the client's ``LedgerRow`` tape via the pluggable :func:`read_ledger_rows`
    seam (empty until engine-wiring populates the GCS ledger) and folds it into
    the operator views. Marks / share-class maps are empty for now (the seam
    returns no rows yet), so the result is an honest zero/empty response — NOT
    mock data. When real ledger rows arrive the marks/share-class maps are
    supplied alongside them at the read seam.
    """
    rows = read_ledger_rows(client_id, as_of_date=as_of_date)
    marks: Mapping[str, Decimal] = {}
    share_class_of: Mapping[str, str] = {}
    return compute_ledger_views(
        rows,
        marks=marks,
        as_of=datetime.now(UTC),
        share_class_of=share_class_of,
    )


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
    """Daily PnL series for a client. Returns strategy_alpha + execution_alpha split per day.

    ``realized_pnl`` is ledger-derived (``Σ`` over the client's ``PositionLedgerRow``s),
    NOT the former hardcoded ``"0.00"``. Until the GCS ledger is populated the
    ledger seam returns no rows → the realised total is an honest ``"0"``.
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return _mock_pnl(client_id, date_from, date_to)
    rows = read_attribution_rows(client_id, date_from=date_from, date_to=date_to)
    ledger_rows = read_ledger_rows(client_id, as_of_date=date_to)
    realized_total = realized_pnl_total(
        ledger_rows,
        marks={},
        as_of=datetime.now(UTC),
        share_class_of={},
    )
    return _pnl_from_rows(client_id, rows, realized_total)


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
        breakdown = attribution_breakdown(list(mock_rows) if isinstance(mock_rows, list) else [])
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
    rows = read_ledger_rows(client_id)
    instructions: list[dict[str, object]] = []
    for row in rows:
        if str(row.event_type) != "trade":
            continue
        delta = row.delta
        trade_id = row.trade_id or ""
        instructions.append(
            {
                "instruction_id": trade_id,
                "strategy_id": row.asset_canonical_id,
                "action": "TRADE",
                "venue": row.venue,
                "instrument_key": trade_id.split("|")[0],
                "side": _side_from_delta(delta, str(row.direction) if row.direction else None),
                "target_qty": str(abs(delta)),
                "benchmark_price": str(row.price if row.price is not None else "0"),
                "timestamp": row.timestamp_utc.isoformat(),
                "correlation_id": row.event_id,
                "status": "filled",
            }
        )
    instructions = instructions[-limit:]
    return {"client_id": client_id, "instructions": instructions, "total": len(instructions)}


@router.get("/transfers")
def get_client_transfers(
    client_id: str,
    auth: AuthDep,
) -> dict[str, object]:
    """Wallet transfers / money movements (TRANSFER / DEPOSIT / BRIDGE ledger rows).

    Single-client scope (funds never cross clients). Reads the REAL ledger rows and
    projects the non-trade money-movement events. Honest empty when none.
    """
    enforce_entitlement(auth, client_id)
    rows = read_ledger_rows(client_id)
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
    return {"client_id": client_id, "transfers": transfers, "total": len(transfers)}


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
