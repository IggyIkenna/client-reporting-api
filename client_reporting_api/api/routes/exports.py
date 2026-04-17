"""CSV export endpoints — streaming downloads for hourly/daily/per-trade data."""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from unified_trading_library import UnifiedCloudConfig

from client_reporting_api.core.backfill_store import (
    _get_equity,
    _get_transfer,
    _is_btc_account,
    get_equity_curve,
)
from client_reporting_api.core.mock_performance_data import (
    MOCK_COIN_BREAKDOWN,
    MOCK_TRADES,
    get_mock_performance_summary,
)
from client_reporting_api.core.tear_sheet_generator import generate_tear_sheet
from client_reporting_api.core.transfer_store import get_transfers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/exports", tags=["exports"])

_cloud_cfg = UnifiedCloudConfig()


def _csv_stream(rows: list[dict[str, object]], fieldnames: list[str]) -> io.StringIO:
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
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download full trade history as CSV."""
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
    buf = _csv_stream(MOCK_TRADES, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_trades.csv"},
    )


@router.get("/daily-summary")
def export_daily_summary_csv(
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download daily P&L summary as CSV."""
    summary = get_mock_performance_summary(client_id)
    monthly = summary.get("monthly_returns", [])

    fields = ["year", "month", "return_pct", "pnl_usd", "opening_equity", "closing_equity"]
    buf = _csv_stream(monthly, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_monthly_summary.csv"},
    )


@router.get("/hourly-snapshots")
def export_hourly_snapshots_csv(
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download hourly equity snapshots as CSV."""
    summary = get_mock_performance_summary(client_id)
    curve = summary.get("equity_curve", [])

    fields = ["timestamp", "equity_usd", "hwm_usd", "drawdown_pct"]
    buf = _csv_stream(curve, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_equity_curve.csv"},
    )


@router.get("/coin-breakdown")
def export_coin_breakdown_csv(
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download per-coin P&L breakdown as CSV."""
    fields = [
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
    buf = _csv_stream(MOCK_COIN_BREAKDOWN, fields)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={client_id}_coin_breakdown.csv"},
    )


@router.get("/daily-equity")
def export_daily_equity_csv(
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download daily equity curve with TWR metrics as CSV.

    Includes: date, equity, TWR index, drawdown %, daily return, cumulative transfers.
    Uses transfer-adjusted metrics from canonical transfer store.
    """
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
    client_id: str = Query(..., description="Client identifier"),
) -> StreamingResponse:
    """Download canonical transfer history as CSV."""
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
    client_ids: str = Query(
        ...,
        description="Comma-separated client IDs (e.g. 'PR,ODUM_PROP')",
    ),
    title: str = Query(
        default="Odum Capital \u2014 Strategy Performance",
        description="Report title",
    ),
) -> HTMLResponse:
    """Generate and return an institutional tear sheet as HTML.

    Includes: equity curves, monthly returns, Sharpe/Sortino/Calmar,
    drawdown series, rolling metrics, CSV download buttons.
    """
    ids = [cid.strip() for cid in client_ids.split(",") if cid.strip()]
    path = generate_tear_sheet(ids, title=title)
    if not path:
        return HTMLResponse(content="<h1>No data available</h1>", status_code=404)

    html = Path(path).read_text(encoding="utf-8")
    return HTMLResponse(content=html)
