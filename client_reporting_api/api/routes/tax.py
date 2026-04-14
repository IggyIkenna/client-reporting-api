"""FIFO cost-basis tax reporting endpoints — annual summary and CSV export."""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from unified_trading_library import UnifiedCloudConfig

from client_reporting_api.core.mock_performance_data import MOCK_TRADES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/tax", tags=["tax"])

_cloud_cfg = UnifiedCloudConfig()


class TaxLot(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema
    """A single FIFO-matched tax lot (buy matched to sell)."""

    symbol: str
    quantity: float
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    realized_pnl: float
    holding_period_days: int
    is_long_term: bool


class AnnualTaxSummary(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema
    """Aggregated tax summary for a given client and tax year."""

    client_id: str
    tax_year: int
    total_realized_pnl: float
    total_fees: float
    net_taxable: float
    currency: str
    lots: list[TaxLot]


def _build_fifo_lots(
    trades: list[dict[str, str | float]],
    tax_year: int,
) -> list[TaxLot]:
    """Match BUY/SELL trades per symbol using FIFO ordering.

    Returns closed lots where the sell occurred in the given tax year.
    """
    buys_by_symbol: dict[str, list[dict[str, str | float]]] = defaultdict(list)
    lots: list[TaxLot] = []

    sorted_trades = sorted(trades, key=lambda t: str(t.get("timestamp", "")))

    for trade in sorted_trades:
        symbol = str(trade.get("symbol", ""))
        side = str(trade.get("side", ""))
        if side == "BUY":
            buys_by_symbol[symbol].append(dict(trade))
        elif side == "SELL":
            _match_sell_to_buys(trade, buys_by_symbol[symbol], lots, tax_year)

    return lots


def _match_sell_to_buys(
    sell: dict[str, str | float],
    buy_queue: list[dict[str, str | float]],
    lots: list[TaxLot],
    tax_year: int,
) -> None:
    """Match a single SELL against the FIFO buy queue."""
    sell_ts = str(sell.get("timestamp", ""))
    sell_dt = _parse_timestamp(sell_ts)
    if sell_dt.year != tax_year:
        return

    sell_qty = float(sell.get("quantity", 0))
    sell_price = float(sell.get("price", 0))
    remaining = sell_qty

    while remaining > 0 and buy_queue:
        buy = buy_queue[0]
        buy_qty = float(buy.get("quantity", 0))
        buy_price = float(buy.get("price", 0))
        buy_ts = str(buy.get("timestamp", ""))
        buy_dt = _parse_timestamp(buy_ts)

        matched_qty = min(remaining, buy_qty)
        pnl = matched_qty * (sell_price - buy_price)
        holding_days = (sell_dt - buy_dt).days

        lots.append(
            TaxLot(
                symbol=str(sell.get("symbol", "")),
                quantity=matched_qty,
                buy_date=buy_ts[:10],
                buy_price=buy_price,
                sell_date=sell_ts[:10],
                sell_price=sell_price,
                realized_pnl=round(pnl, 2),
                holding_period_days=holding_days,
                is_long_term=holding_days > 365,
            )
        )

        remaining -= matched_qty
        leftover = buy_qty - matched_qty
        if leftover > 0:
            buy["quantity"] = leftover
        else:
            buy_queue.pop(0)


def _parse_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp to datetime."""
    return datetime.fromisoformat(ts)


def _compute_total_fees(
    trades: list[dict[str, str | float]],
    tax_year: int,
) -> float:
    """Sum fees for trades in the given tax year."""
    total = 0.0
    for trade in trades:
        ts = str(trade.get("timestamp", ""))
        dt = _parse_timestamp(ts)
        if dt.year == tax_year:
            total += float(trade.get("fee", 0))
    return round(total, 2)


def _build_annual_summary(
    client_id: str,
    tax_year: int,
    trades: list[dict[str, str | float]],
) -> AnnualTaxSummary:
    """Build a complete annual tax summary from trades."""
    lots = _build_fifo_lots(trades, tax_year)
    total_pnl = round(sum(lot.realized_pnl for lot in lots), 2)
    total_fees = _compute_total_fees(trades, tax_year)
    net_taxable = round(total_pnl - total_fees, 2)

    return AnnualTaxSummary(
        client_id=client_id,
        tax_year=tax_year,
        total_realized_pnl=total_pnl,
        total_fees=total_fees,
        net_taxable=net_taxable,
        currency="USD",
        lots=lots,
    )


@router.get("/annual-summary")
def get_annual_tax_summary(
    client_id: str = Query(..., description="Client identifier"),
    tax_year: int = Query(2025, description="Tax year to report on"),
) -> AnnualTaxSummary:
    """Return FIFO cost-basis tax lots and summary for a tax year."""
    if _cloud_cfg.is_mock_mode():
        return _build_annual_summary(client_id, tax_year, MOCK_TRADES)

    logger.info(
        "get_annual_tax_summary: client_id=%s year=%d",
        client_id,
        tax_year,
    )
    return AnnualTaxSummary(
        client_id=client_id,
        tax_year=tax_year,
        total_realized_pnl=0.0,
        total_fees=0.0,
        net_taxable=0.0,
        currency="USD",
        lots=[],
    )


@router.get("/annual-summary/csv")
def export_annual_tax_csv(
    client_id: str = Query(..., description="Client identifier"),
    tax_year: int = Query(2025, description="Tax year to report on"),
) -> StreamingResponse:
    """Download FIFO tax lots as CSV for a given tax year."""
    summary = _build_annual_summary(client_id, tax_year, MOCK_TRADES)

    fields = [
        "symbol",
        "quantity",
        "buy_date",
        "buy_price",
        "sell_date",
        "sell_price",
        "realized_pnl",
        "holding_period_days",
        "is_long_term",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for lot in summary.lots:
        writer.writerow(lot.model_dump())
    buf.seek(0)

    filename = f"{client_id}_tax_{tax_year}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
