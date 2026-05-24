"""GET /trades — paginated trade history from backfill data."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.reporting._shared import _load_json
from client_reporting_api.core.entitlement import _enforce_entitlement

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _apply_filters(trades: list[dict[str, object]], symbol: str, side: str) -> list[dict[str, object]]:
    """Filter the trade list by symbol substring / side — both case-insensitive."""
    result = trades
    if symbol:
        sym_upper = symbol.upper()
        result = [t for t in result if sym_upper in str(t.get("symbol", "")).upper()]
    if side:
        result = [t for t in result if str(t.get("side", "")).lower() == side.lower()]
    return result


def _project_trade(t: dict[str, object]) -> tuple[dict[str, object], float, float, float]:
    """Project one raw trade into UI shape and return (row, cost, fee, rpnl)."""
    fee_info_raw = t.get("fee", {})
    fee_info: dict[str, Any] = cast(dict[str, Any], fee_info_raw) if isinstance(fee_info_raw, dict) else {}
    fee_cost = float(fee_info.get("cost", 0) or 0)
    info_raw = t.get("info", {})
    info: dict[str, Any] = cast(dict[str, Any], info_raw) if isinstance(info_raw, dict) else {}
    rpnl = float(info.get("fillPnl", 0) or 0)
    cost = float(t.get("cost", 0) or 0)
    record = {
        "trade_id": str(t.get("id", "")),
        "venue": "okx",
        "symbol": str(t.get("symbol", "")),
        "side": str(t.get("side", "")).upper(),
        "quantity": float(t.get("amount", 0) or 0),
        "price": float(t.get("price", 0) or 0),
        "fee": abs(fee_cost),
        "fee_currency": str(fee_info.get("currency", "USDT")) if isinstance(fee_info, dict) else "USDT",
        "realized_pnl": rpnl,
        "timestamp": str(t.get("datetime", "")),
        "order_id": str(t.get("order", "")),
        "trade_type": "perp",
        "notional_usd": cost,
    }
    return record, cost, abs(fee_cost), rpnl


@router.get("/trades")
def get_trades(
    auth: AuthDep,
    client_id: str = Query(..., description="Client ID"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    symbol: str = Query(default=""),
    side: str = Query(default=""),
) -> dict[str, object]:
    """Return paginated trade history from backfill data."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    trades = _load_json(cid, "trades.json") or []
    trades.sort(key=lambda t: t.get("timestamp", 0) or 0, reverse=True)
    trades = _apply_filters(trades, symbol, side)

    total = len(trades)
    page = trades[offset : offset + limit]

    trade_records: list[dict[str, object]] = []
    total_volume = 0.0
    total_fees = 0.0
    total_rpnl = 0.0
    for t in page:
        record, cost, fee, rpnl = _project_trade(t)
        trade_records.append(record)
        total_volume += cost
        total_fees += fee
        total_rpnl += rpnl

    return {
        "client_id": cid,
        "trades": trade_records,
        "total": total,
        "offset": offset,
        "limit": limit,
        "aggregates": {
            "total_trades": total,
            "total_volume_usd": round(total_volume, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_realized_pnl": round(total_rpnl, 2),
        },
    }
