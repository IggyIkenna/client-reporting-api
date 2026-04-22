"""Trade analytics + performance stats + order browser endpoints."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.backfill_store import (
    compute_performance_stats,
    get_equity_curve,
)
from client_reporting_api.core.entitlement import (
    _enforce_entitlement,
    require_internal,
)
from client_reporting_api.core.trade_analytics import (
    CLIENT_IDS,
    aggregate_clients,
    compute_coin_breakdown,
)

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_BACKFILL_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "backfill"


@router.get("/analytics/{client_id}")
def get_trade_analytics(client_id: str, auth: AuthDep) -> dict[str, object]:
    """Coin-level PnL, volume, holding time breakdown for a client."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    ta = compute_coin_breakdown(cid)
    return asdict(ta)


@router.get("/analytics")
def get_aggregated_analytics(
    auth: AuthDep,
    client_ids: str = Query(default="", description="Comma-separated client IDs (empty = all)"),
) -> dict[str, object]:
    """Get aggregated trade analytics across clients.

    Cross-client aggregate — internal-only.
    """
    require_internal(auth)
    ids = (
        [c.strip().upper() for c in client_ids.split(",") if c.strip()]
        if client_ids
        else CLIENT_IDS
    )
    ta = aggregate_clients(ids)
    return asdict(ta)


@router.get("/performance/{client_id}")
def get_performance_stats(client_id: str, auth: AuthDep) -> dict[str, object]:
    """Full performance stats: Sharpe, Sortino, Calmar, max drawdown, win rate, etc."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    ec = get_equity_curve(cid)
    if not ec:
        raise HTTPException(status_code=404, detail=f"No equity data for {cid}")
    return compute_performance_stats(ec)


def _filter_orders_in_place(
    orders: list[dict[str, object]], symbol: str, side: str, status: str
) -> list[dict[str, object]]:
    """Apply the three supported filters to a list of orders."""
    result = orders
    if symbol:
        sym_upper = symbol.upper()
        result = [o for o in result if sym_upper in (str(o.get("symbol", "")) or "").upper()]
    if side:
        result = [o for o in result if o.get("side", "") == side.lower()]
    if status:
        result = [o for o in result if o.get("status", "") == status.lower()]
    return result


@router.get("/orders/{client_id}")
def get_orders(
    client_id: str,
    auth: AuthDep,
    symbol: str = Query(default="", description="Filter by symbol (partial match)"),
    side: str = Query(default="", description="Filter by side (buy/sell)"),
    status: str = Query(default="", description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    """Browse orders for a client with pagination and filters."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    orders_path = _BACKFILL_ROOT / cid / "orders.json"
    if not orders_path.exists():
        return {"orders": [], "total": 0, "limit": limit, "offset": offset}

    with open(orders_path) as f:
        all_orders = json.load(f)

    all_orders.sort(key=lambda o: o.get("timestamp", 0) or 0, reverse=True)
    all_orders = _filter_orders_in_place(all_orders, symbol, side, status)

    total = len(all_orders)
    page = all_orders[offset : offset + limit]
    return {"orders": page, "total": total, "limit": limit, "offset": offset}
