"""Per-venue trading P&L fetchers (OKX bills, Binance income).

Used by the incremental-update path to detect transfers via
``equity_change - trading_pnl``. Kept venue-agnostic at the top
(``_fetch_pnl_since``) with venue-specific walkers below.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import ccxt

from client_reporting_api.cli.shared import _get_usd_price

logger = logging.getLogger(__name__)


def _okx_bill_pnl(bill: dict[str, object], exchange: ccxt.Exchange) -> float:
    """Project a single OKX bill row to its USD-equivalent PnL contribution."""
    if str(bill.get("subType", "")) in ("11", "12"):
        return 0.0
    ccy = str(bill.get("ccy", ""))
    change = float(bill.get("balChg", 0) or 0)
    if ccy == "USDT":
        return change
    if ccy == "BTC":
        return change * _get_usd_price(exchange, "BTC")
    return 0.0


def _fetch_okx_pnl(exchange: ccxt.Exchange, since_ms: int, until_ms: int) -> float:
    """Walk OKX account bills page-by-page and sum trading PnL across the window."""
    total = 0.0
    cursor_ms = since_ms
    try:
        while cursor_ms < until_ms:
            end_ms = min(cursor_ms + 7 * 86400 * 1000, until_ms)
            resp = exchange.private_get_account_bills({"begin": str(cursor_ms), "end": str(end_ms), "limit": "100"})
            bills = resp.get("data", [])
            for bill in bills:
                total += _okx_bill_pnl(bill, exchange)
            if len(bills) < 100:
                cursor_ms = end_ms
            else:
                last_ts = max(int(b.get("ts", 0) or 0) for b in bills)
                cursor_ms = last_ts + 1 if last_ts > cursor_ms else end_ms
            time.sleep(0.2)
    except ccxt.BaseError as exc:
        logger.warning("Failed to fetch OKX bills: %s", str(exc))
    return total


def _fetch_binance_pnl(exchange: ccxt.Exchange, since_ms: int, until_ms: int) -> float:
    """Walk Binance income endpoint page-by-page and sum trading PnL."""
    total = 0.0
    cursor_ms = since_ms
    try:
        while cursor_ms < until_ms:
            income = exchange.fapiprivate_get_income(
                {
                    "startTime": str(cursor_ms),
                    "endTime": str(min(cursor_ms + 7 * 86400 * 1000, until_ms)),
                    "limit": "1000",
                }
            )
            for r in income:
                if str(r.get("incomeType", "")) == "TRANSFER":
                    continue
                total += float(r.get("income", 0) or 0)
            if len(income) < 1000:
                cursor_ms += 7 * 86400 * 1000
            else:
                last_ts = max(int(r.get("time", 0) or 0) for r in income)
                cursor_ms = last_ts + 1
            time.sleep(0.2)
    except ccxt.BaseError as exc:
        logger.warning("Failed to fetch Binance income: %s", str(exc))
    return total


def _fetch_pnl_since(exchange: ccxt.Exchange, venue: str, since_date: str, until_date: str) -> float:
    """Fetch trading PnL between two dates from the exchange ledger."""
    since_ms = int(datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms = int(datetime.strptime(until_date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms += 86400 * 1000
    if venue == "okx":
        return _fetch_okx_pnl(exchange, since_ms, until_ms)
    if venue == "binance":
        return _fetch_binance_pnl(exchange, since_ms, until_ms)
    return 0.0
