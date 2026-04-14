"""Trade history API routes — full order history with fills, fees, slippage.

In live mode, pulls real trades from OKX/Binance via ExchangeDataCollector.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Query
from unified_trading_library import UnifiedCloudConfig

from client_reporting_api.core.backfill_store import get_backfill_trades
from client_reporting_api.core.live_data_provider import get_collector
from client_reporting_api.core.mock_performance_data import MOCK_TRADES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

_cloud_cfg = UnifiedCloudConfig()


def _decimal_to_float(val: Decimal) -> float:
    """Convert Decimal to float for JSON serialization."""
    return float(val)


@router.get("")
def get_trade_history(
    client_id: str = Query(..., description="Client identifier"),
    symbol: str | None = Query(None, description="Filter by symbol"),
    side: str | None = Query(None, description="Filter by side (BUY or SELL)"),
    limit: int = Query(50, ge=1, le=500, description="Number of trades"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, object]:
    """Return paginated trade history with fills, fees, and P&L."""
    if _cloud_cfg.is_mock_mode():
        return _mock_trades(client_id, symbol, side, limit, offset)

    collector = get_collector()
    records = collector.get_client_trades(client_id, limit=limit + offset)

    trades = [
        {
            "trade_id": r.trade_id,
            "venue": r.venue,
            "symbol": r.symbol,
            "side": r.side.value if hasattr(r.side, "value") else str(r.side),
            "quantity": _decimal_to_float(r.quantity),
            "price": _decimal_to_float(r.price),
            "fee": _decimal_to_float(r.fee),
            "fee_currency": r.fee_currency,
            "realized_pnl": _decimal_to_float(r.realized_pnl),
            "timestamp": r.timestamp.isoformat(),
            "order_id": r.order_id,
            "trade_type": r.trade_type.value
            if hasattr(r.trade_type, "value")
            else str(r.trade_type),
            "notional_usd": _decimal_to_float(
                r.notional_usd if r.notional_usd else r.quantity * r.price
            ),
        }
        for r in records
    ]

    # Fall back to backfilled trades if live returns empty
    if not trades:
        trades = _backfill_trades(client_id)

    if symbol:
        trades = [t for t in trades if t["symbol"] == symbol]
    if side:
        trades = [t for t in trades if t["side"] == side]

    total = len(trades)
    trades = trades[offset : offset + limit]

    total_volume = sum(float(t["notional_usd"]) for t in trades)
    total_fees = sum(float(t["fee"]) for t in trades)
    net_pnl = sum(float(t["realized_pnl"]) for t in trades)

    return {
        "client_id": client_id,
        "trades": trades,
        "total": total,
        "offset": offset,
        "limit": limit,
        "source": "live",
        "aggregates": {
            "total_trades": total,
            "total_volume_usd": round(total_volume, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_realized_pnl": round(net_pnl, 2),
        },
    }


def _backfill_trades(client_id: str) -> list[dict[str, str | float | None]]:
    """Load backfilled trades and normalize to API format."""
    raw = get_backfill_trades(client_id)
    trades: list[dict[str, str | float | None]] = []
    for t in raw:
        fee_info = t.get("fee")
        fee_cost = 0.0
        fee_currency = ""
        if isinstance(fee_info, dict):
            fee_cost = float(fee_info.get("cost", 0) or 0)
            fee_currency = str(fee_info.get("currency", ""))
        qty = float(t.get("amount", 0) or 0)
        price = float(t.get("price", 0) or 0)
        # CCXT `cost` = amount * contractSize * price for derivatives.
        # `amount` alone is in contracts for OKX SWAP, so qty * price is wrong.
        cost_val = float(t.get("cost", 0) or 0)
        notional = cost_val if cost_val else round(qty * price, 2)
        ts_val = t.get("timestamp")
        ts_ms = int(ts_val) if ts_val is not None else 0
        from datetime import UTC, datetime

        ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat() if ts_ms else ""
        trades.append(
            {
                "trade_id": str(t.get("id", "")),
                "venue": str(t.get("exchange", "")),
                "symbol": str(t.get("symbol", "")),
                "side": str(t.get("side", "")).upper(),
                "quantity": qty,
                "price": price,
                "fee": fee_cost,
                "fee_currency": fee_currency,
                "realized_pnl": 0.0,
                "timestamp": ts_str,
                "order_id": str(t.get("order", "")),
                "trade_type": "MARKET",
                "notional_usd": round(notional, 2),
            }
        )
    # Sort newest first
    trades.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return trades


def _mock_trades(
    client_id: str,
    symbol: str | None,
    side: str | None,
    limit: int,
    offset: int,
) -> dict[str, object]:
    """Return mock trade data."""
    trades = list(MOCK_TRADES)
    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol]
    if side:
        trades = [t for t in trades if t.get("side") == side]

    total = len(trades)
    trades = trades[offset : offset + limit]

    total_volume = sum(float(t.get("notional_usd", 0)) for t in MOCK_TRADES)
    total_fees = sum(float(t.get("fee", 0)) for t in MOCK_TRADES)
    net_pnl = sum(float(t.get("realized_pnl", 0)) for t in MOCK_TRADES)

    return {
        "client_id": client_id,
        "trades": trades,
        "total": total,
        "offset": offset,
        "limit": limit,
        "aggregates": {
            "total_trades": len(MOCK_TRADES),
            "total_volume_usd": round(total_volume, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_realized_pnl": round(net_pnl, 2),
        },
    }
