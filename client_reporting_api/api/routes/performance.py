"""Performance dashboard API routes — equity curve, monthly returns, stats, positions.

Serves the TradeLink-replacement performance dashboard in unified-trading-system-ui.
In live mode, pulls real data from OKX/Binance via ExchangeDataCollector.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.backfill_store import (
    compute_monthly_returns,
    compute_performance_stats,
    get_backfill_summary,
    get_equity_curve,
)
from client_reporting_api.core.entitlement import _enforce_entitlement
from client_reporting_api.core.live_data_provider import get_collector
from client_reporting_api.core.mock_performance_data import (
    MOCK_BALANCE_BREAKDOWN,
    MOCK_COIN_BREAKDOWN,
    MOCK_POSITIONS,
    get_mock_performance_summary,
)
from client_reporting_api.core.trade_analytics import compute_coin_breakdown

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/performance", tags=["performance"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _decimal_to_float(val: Decimal) -> float:
    """Convert Decimal to float for JSON serialization."""
    return float(val)


@router.get("/summary")
def get_performance_summary(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Return full performance summary with equity curve, monthly returns, and stats."""
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return get_mock_performance_summary(client_id)

    # Live mode: fetch real snapshot from exchange
    collector = get_collector()
    snapshot = collector.get_client_snapshot(client_id)
    if snapshot is None:
        logger.warning("No snapshot for %s, falling back to mock", client_id)
        return get_mock_performance_summary(client_id)

    # Load backfilled equity curve if available
    equity_curve = get_equity_curve(client_id)
    monthly_returns = compute_monthly_returns(equity_curve)
    stats = compute_performance_stats(equity_curve)
    backfill_summary = get_backfill_summary(client_id)

    return {
        "client_id": client_id,
        "current_equity_usd": _decimal_to_float(snapshot.total_equity_usd),
        "free_balance_usd": _decimal_to_float(snapshot.free_balance_usd),
        "locked_balance_usd": _decimal_to_float(snapshot.locked_balance_usd),
        "unrealized_pnl_usd": _decimal_to_float(snapshot.unrealized_pnl_usd),
        "source": "live",
        "timestamp": snapshot.timestamp.isoformat(),
        "asset_count": len(snapshot.asset_balances),
        "position_count": len(snapshot.positions),
        "equity_curve": equity_curve,
        "monthly_returns": monthly_returns,
        "stats": stats,
        "equity_source": backfill_summary.get("equity_source", "unknown")
        if backfill_summary
        else "unknown",
        "trade_count": backfill_summary.get("trade_count", 0) if backfill_summary else 0,
        "ledger_records": backfill_summary.get("ledger_records", 0) if backfill_summary else 0,
    }


@router.get("/positions")
def get_open_positions(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Return current open positions with unrealized P&L."""
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return {"client_id": client_id, "positions": MOCK_POSITIONS}

    collector = get_collector()
    snapshot = collector.get_client_snapshot(client_id)
    if snapshot is None:
        return {"client_id": client_id, "positions": []}

    positions = [
        {
            "symbol": p.symbol,
            "side": p.side,
            "quantity": _decimal_to_float(p.quantity),
            "entry_price": _decimal_to_float(p.entry_price),
            "mark_price": _decimal_to_float(p.mark_price),
            "unrealized_pnl": _decimal_to_float(p.unrealized_pnl),
            "leverage": _decimal_to_float(p.leverage),
            "liquidation_price": _decimal_to_float(p.liquidation_price)
            if p.liquidation_price
            else None,
            "notional_usd": _decimal_to_float(p.notional_usd),
        }
        for p in snapshot.positions
    ]
    return {"client_id": client_id, "positions": positions}


@router.get("/balances")
def get_balance_breakdown(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Return per-asset balance breakdown with USD values."""
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return {"client_id": client_id, **MOCK_BALANCE_BREAKDOWN}

    collector = get_collector()
    snapshot = collector.get_client_snapshot(client_id)
    if snapshot is None:
        return {"client_id": client_id, "total_equity_usd": 0, "balances": []}

    balances = [
        {
            "currency": a.currency,
            "free": str(a.free),
            "locked": str(a.locked),
            "total": str(a.total),
            "usd_value": str(a.usd_value),
        }
        for a in snapshot.asset_balances
    ]
    return {
        "client_id": client_id,
        "total_equity_usd": _decimal_to_float(snapshot.total_equity_usd),
        "balances": balances,
    }


@router.get("/coin-breakdown")
def get_coin_breakdown(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Return per-coin P&L breakdown from real bills ledger + trades.

    Includes: realized PnL, fees, funding, net PnL, volume, trade count,
    buy/sell split, avg holding time, round trips.
    Validated: sum(coin.net_pnl) == total_net_pnl.
    """
    _enforce_entitlement(auth, client_id)
    if _cloud_cfg.is_mock_mode():
        return {"client_id": client_id, "coins": MOCK_COIN_BREAKDOWN}

    ta = compute_coin_breakdown(client_id)
    coins = [
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
            "allocation_pct": round(c.volume_usd / ta.total_volume_usd, 4)
            if ta.total_volume_usd > 0
            else 0,
        }
        for c in ta.coins
    ]
    return {"client_id": client_id, "coins": coins}
