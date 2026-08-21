"""CSV export endpoints — streaming downloads for hourly/daily/per-trade data."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.api.routes.trades import (
    _backfill_trades,  # pyright: ignore[reportPrivateUsage]
    _ledger_run_trades,  # pyright: ignore[reportPrivateUsage]
)
from client_reporting_api.core.backfill_store import (
    _get_equity,  # pyright: ignore[reportPrivateUsage]
    _get_transfer,  # pyright: ignore[reportPrivateUsage]
    _is_btc_account,  # pyright: ignore[reportPrivateUsage]
    compute_monthly_returns,
    get_equity_curve,
)
from client_reporting_api.core.entitlement import (
    enforce_entitlement,  # pyright: ignore[reportPrivateUsage]
    require_internal,
)
from client_reporting_api.core.live_data_provider import get_collector
from client_reporting_api.core.mock_performance_data import (
    MOCK_COIN_BREAKDOWN,
    MOCK_TRADES,
    get_mock_performance_summary,
)
from client_reporting_api.core.tear_sheet_generator import generate_tear_sheet
from client_reporting_api.core.trade_analytics import compute_coin_breakdown
from client_reporting_api.core.transfer_store import get_transfers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/exports", tags=["exports"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

# Real-mode coin-breakdown columns differ from the mock fixture's shape — the
# real ``compute_coin_breakdown`` engine (core/trade_analytics.py) doesn't
# track entry/current price or cost-basis, so real CSVs get their own honest
# column set rather than padding the fixture's columns with blanks.
_MOCK_COIN_FIELDS = [
    "symbol",
    "quantity",
    "avg_entry_price",
    "current_price",
    "cost_basis_usd",
    "market_value_usd",
    "realized_pnl",
    "unrealized_pnl",
    "total_pnl",
    "allocation_pct",
    "trade_count",
]
_REAL_COIN_FIELDS = [
    "symbol",
    "realized_pnl",
    "trading_fees",
    "funding_pnl",
    "total_pnl",
    "volume_usd",
    "trade_count",
    "buy_count",
    "sell_count",
    "avg_trade_size_usd",
    "avg_holding_hours",
    "round_trips",
]

# compute_monthly_returns() (core/backfill_store.py) only computes {month,
# return_pct} — the mock fixture's pnl_usd/opening_equity/closing_equity/year
# columns aren't produced by any real reader today, so real-mode CSVs use a
# reduced, honest column set instead of padding with blanks.
_MOCK_MONTHLY_FIELDS = ["year", "month", "return_pct", "pnl_usd", "opening_equity", "closing_equity"]
_REAL_MONTHLY_FIELDS = ["month", "return_pct"]


def _csv_stream(rows: list[dict[str, Any]], fieldnames: list[str]) -> io.StringIO:
    """Convert a list of dicts to CSV in a StringIO buffer."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    buf.seek(0)
    return buf


@router.get("/trades")
def export_trades_csv(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download full trade history as CSV.

    Real data path (2026-08-21 CTO handoff P2 fix, extended same day): the
    canonical paper-run ledger fills, falling back to the backfilled
    historical trade tape, falling back to live-collector-only state — the
    same three sources ``trades.py::get_trade_history`` uses, in the same
    order, reusing its ``get_collector().get_client_trades(...)`` call
    rather than reimplementing it. The collector leg covers the rare client
    with neither a ledger run nor backfilled history (live-collector-only
    state). ``MOCK_TRADES`` is mock-mode-only; a real result empty across
    all three sources is an honest "No data" row, never a silent fixture.
    """
    enforce_entitlement(auth, client_id)
    fields = [
        "trade_id",
        "venue",
        "symbol",
        "side",
        "quantity",
        "price",
        "fee",
        "fee_currency",
        "realized_pnl",
        "timestamp",
        "order_id",
        "trade_type",
        "notional_usd",
    ]
    if _cloud_cfg.is_mock_mode():
        rows: list[dict[str, Any]] = list(MOCK_TRADES)
    else:
        _run_id, ledger_rows = _ledger_run_trades(client_id)
        rows = cast(list[dict[str, Any]], ledger_rows) or cast(list[dict[str, Any]], _backfill_trades(client_id))
        if not rows:
            records = get_collector().get_client_trades(client_id)
            rows = [
                {
                    "trade_id": r.trade_id,
                    "venue": r.venue,
                    "symbol": r.symbol,
                    "side": r.side.value if hasattr(r.side, "value") else str(r.side),
                    "quantity": float(r.quantity),
                    "price": float(r.price),
                    "fee": float(r.fee),
                    "fee_currency": r.fee_currency,
                    "realized_pnl": float(r.realized_pnl),
                    "timestamp": r.timestamp.isoformat(),
                    "order_id": r.order_id,
                    "trade_type": r.trade_type.value if hasattr(r.trade_type, "value") else str(r.trade_type),
                    "notional_usd": float(r.notional_usd if r.notional_usd else r.quantity * r.price),
                }
                for r in records
            ]
    if not rows:
        buf: io.StringIO = io.StringIO("No data\n")
    else:
        buf = _csv_stream(rows, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_trades.csv"},
    )


@router.get("/daily-summary")
def export_daily_summary_csv(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download daily P&L summary as CSV.

    Real data path (2026-08-21 CTO handoff P2 fix): monthly returns
    computed from the real equity curve via ``compute_monthly_returns``
    — the same engine ``performance.py``'s real summary path uses. That
    engine only produces {month, return_pct}, not the mock fixture's
    pnl_usd/opening_equity/closing_equity columns, so real-mode CSVs use
    a reduced column set (see ``_REAL_MONTHLY_FIELDS``) rather than a
    fixture masquerading as those unavailable figures.
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        summary = get_mock_performance_summary(client_id)
        monthly = summary.get("monthly_returns", [])
        fields = _MOCK_MONTHLY_FIELDS
    else:
        curve = get_equity_curve(client_id)
        monthly = compute_monthly_returns(curve) if curve else []
        fields = _REAL_MONTHLY_FIELDS

    if not monthly:
        buf: io.StringIO = io.StringIO("No data\n")
    else:
        buf = _csv_stream(cast(list[dict[str, Any]], monthly), fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_monthly_summary.csv"},
    )


@router.get("/hourly-snapshots")
def export_hourly_snapshots_csv(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download hourly equity snapshots as CSV.

    2026-08-21 CTO handoff P2 fix: no real hourly-granularity equity
    store exists today — ``get_equity_curve`` (used by ``/daily-equity``
    above) is daily-only. Rather than relabel daily data as hourly, real
    mode returns an explicit, clearly-labeled empty result; the fixture
    is mock-mode-only.
    """
    enforce_entitlement(auth, client_id)
    fields = ["timestamp", "equity_usd", "hwm_usd", "drawdown_pct"]
    if _cloud_cfg.is_mock_mode():
        summary = get_mock_performance_summary(client_id)
        curve = summary.get("equity_curve", [])
        buf: io.StringIO = (
            io.StringIO("No data\n") if not curve else _csv_stream(cast(list[dict[str, Any]], curve), fields)
        )
    else:
        buf = io.StringIO("No data: hourly equity snapshots are not yet captured for live clients\n")
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_equity_curve.csv"},
    )


@router.get("/coin-breakdown")
def export_coin_breakdown_csv(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download per-coin P&L breakdown as CSV.

    Real data path (2026-08-21 CTO handoff P2 fix): reuses
    ``compute_coin_breakdown`` — the same per-coin engine
    ``performance.py``'s real ``/coin-breakdown`` JSON route uses.
    ``MOCK_COIN_BREAKDOWN`` is now mock-mode-only.
    """
    enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        fields = _MOCK_COIN_FIELDS
        rows: list[dict[str, Any]] = list(MOCK_COIN_BREAKDOWN)
    else:
        analytics = compute_coin_breakdown(client_id)
        fields = _REAL_COIN_FIELDS
        rows = [
            {
                "symbol": c.symbol,
                "realized_pnl": c.realized_pnl,
                "trading_fees": c.trading_fees,
                "funding_pnl": c.funding_pnl,
                "total_pnl": c.net_pnl,
                "volume_usd": c.volume_usd,
                "trade_count": c.trade_count,
                "buy_count": c.buy_count,
                "sell_count": c.sell_count,
                "avg_trade_size_usd": c.avg_trade_size_usd,
                "avg_holding_hours": c.avg_holding_hours,
                "round_trips": c.round_trips,
            }
            for c in analytics.coins
        ]

    if not rows:
        buf: io.StringIO = io.StringIO("No data\n")
    else:
        buf = _csv_stream(rows, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_coin_breakdown.csv"},
    )


@router.get("/daily-equity")
def export_daily_equity_csv(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download daily equity curve with TWR metrics as CSV.

    Includes: date, equity, TWR index, drawdown %, daily return, cumulative transfers.
    Uses transfer-adjusted metrics from canonical transfer store.
    """
    enforce_entitlement(auth, client_id)
    curve = get_equity_curve(client_id)
    if not curve:
        buf = io.StringIO("No data\n")
        return StreamingResponse(
            buf,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={client_id}_daily.csv"},
        )

    is_btc = _is_btc_account(curve)
    first_date = str(curve[0].get("date", ""))
    rnd = 6 if is_btc else 2

    fields = [
        "date",
        "equity",
        "twr_index",
        "drawdown_pct",
        "daily_return_pct",
        "transfer",
        "cumulative_transfers",
    ]
    rows: list[dict[str, object]] = []
    twr = 1.0
    peak = 1.0
    cum_xfer = 0.0

    for i, point in enumerate(curve):
        date = str(point.get("date", ""))
        eq = _get_equity(point, is_btc)
        day_ret = 0.0
        xfer = 0.0

        if i > 0:
            eq_prev = _get_equity(curve[i - 1], is_btc)
            xfer = _get_transfer(point, curve[i - 1], is_btc, first_date)
            cum_xfer += xfer
            if eq_prev > 0 and eq > 0:
                day_ret = (eq - xfer) / eq_prev - 1.0
            twr *= 1.0 + day_ret

        if twr > peak:
            peak = twr
        dd = (peak - twr) / peak * 100 if peak > 0 else 0

        rows.append(
            {
                "date": date,
                "equity": round(eq, rnd),
                "twr_index": round(twr, 6),
                "drawdown_pct": round(-dd, 2),
                "daily_return_pct": round(day_ret * 100, 4),
                "transfer": round(xfer, rnd),
                "cumulative_transfers": round(cum_xfer, rnd),
            }
        )

    buf = _csv_stream(rows, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_daily_equity.csv"},
    )


@router.get("/transfers")
def export_transfers_csv(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download canonical transfer history as CSV."""
    enforce_entitlement(auth, client_id)
    records = get_transfers(client_id)
    fields = [
        "transfer_id",
        "timestamp",
        "direction",
        "currency",
        "amount",
        "usd_amount",
        "venue",
        "status",
        "tx_hash",
        "network",
    ]
    rows = [
        {
            "transfer_id": r.transfer_id,
            "timestamp": r.timestamp.isoformat(),
            "direction": r.direction,
            "currency": r.currency,
            "amount": str(r.amount),
            "usd_amount": str(r.usd_amount),
            "venue": r.venue,
            "status": r.status,
            "tx_hash": r.tx_hash,
            "network": r.network,
        }
        for r in records
    ]
    buf = _csv_stream(rows, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_transfers.csv"},
    )


@router.get("/tear-sheet")
def export_tear_sheet(
    auth: AuthDep,
    client_ids: str = Query(
        ...,
        description="Comma-separated client IDs (e.g. 'PR,ODUM_PROP')",
    ),
    title: str = Query(
        default="Odum Capital — Strategy Performance",
        description="Report title",
    ),
) -> HTMLResponse:
    """Generate and return an institutional tear sheet as HTML.

    Includes: equity curves, monthly returns, Sharpe/Sortino/Calmar,
    drawdown series, rolling metrics, CSV download buttons.

    Cross-client tear sheets accept a list of client IDs and are used
    for internal / IR reporting; require ``is_internal``. External
    callers must use the per-client export endpoints that apply
    ``enforce_entitlement``.
    """
    require_internal(auth)
    ids = [cid.strip() for cid in client_ids.split(",") if cid.strip()]
    path = generate_tear_sheet(ids, title=title)
    if not path:
        return HTMLResponse(content="<h1>No data available</h1>", status_code=404)

    html = Path(path).read_text(encoding="utf-8")
    return HTMLResponse(content=html)
