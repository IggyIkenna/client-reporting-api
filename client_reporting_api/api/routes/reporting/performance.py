"""GET /performance/* — summary, coin breakdown, positions, balances."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.reporting._shared import _load_json
from client_reporting_api.core.backfill_store import (
    compute_monthly_returns,
    compute_performance_stats,
    get_backfill_summary,
    get_equity_curve,
)
from client_reporting_api.core.entitlement import _enforce_entitlement
from client_reporting_api.core.hwm_seeds import get_hwm_seed, get_pnl_recovery_seed
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES, compute_pnl_series
from client_reporting_api.core.trade_analytics import compute_coin_breakdown

router = APIRouter(prefix="/performance")

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _build_equity_curve_ui(ec: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project the raw equity curve into the UI shape (with running HWM + drawdown)."""
    out: list[dict[str, object]] = []
    hwm = 0.0
    for p in ec:
        eq = float(p.get("equity_usd", 0))
        if eq > hwm:
            hwm = eq
        dd = (hwm - eq) / hwm * 100 if hwm > 0 else 0
        out.append(
            {
                "timestamp": str(p.get("date", "")),
                "equity_usd": eq,
                "hwm_usd": hwm,
                "drawdown_pct": round(dd, 2),
            }
        )
    return out


def _build_monthly_returns_ui(monthly: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project monthly returns into the UI shape."""
    out: list[dict[str, object]] = []
    for m in monthly:
        month_str = str(m.get("month", ""))
        if not month_str:
            continue
        out.append(
            {
                "year": int(month_str[:4]),
                "month": int(month_str[5:7]),
                "return_pct": float(m.get("return_pct", 0)),
                "pnl_usd": 0,
                "opening_equity": 0,
                "closing_equity": 0,
            }
        )
    return out


@router.get("/summary")
def get_performance_summary(
    auth: AuthDep,
    client_id: str = Query(..., description="Client ID"),
) -> dict[str, object]:
    """Return full performance summary with real equity curve, monthly returns, stats."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    ec = get_equity_curve(cid)
    if not ec:
        raise HTTPException(status_code=404, detail=f"No data for {cid}")

    stats = compute_performance_stats(
        ec,
        hwm_seed=get_hwm_seed(cid),
        pnl_recovery=get_pnl_recovery_seed(cid),
    )
    pnl_data = compute_pnl_series(cid)
    ta = compute_coin_breakdown(cid)
    summary = get_backfill_summary(cid)

    return {
        "client_id": cid,
        "client_name": CLIENT_NAMES.get(cid, cid),
        "current_equity_usd": pnl_data.get("current_equity", 0) if pnl_data else 0,
        "hwm_twr_index": float(stats.get("high_water_mark_twr", 0)),
        "twr_recovery_pct": float(stats.get("twr_recovery_pct", 0)),
        "twr_recovery_amount": float(stats.get("twr_recovery_amount", 0)),
        "notional_hwm": float(stats.get("notional_hwm", 0)),
        "notional_recovery": float(stats.get("notional_recovery", 0)),
        "notional_recovery_pct": float(stats.get("notional_recovery_pct", 0)),
        "source": "backfill",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "position_count": len(_load_json(cid, "positions.json") or []),
        "equity_curve": _build_equity_curve_ui(ec),
        "monthly_returns": _build_monthly_returns_ui(compute_monthly_returns(ec)),
        "stats": stats,
        "trade_count": ta.total_trade_count,
        "equity_source": summary.get("equity_source", "unknown") if summary else "unknown",
    }


@router.get("/coin-breakdown")
def get_coin_breakdown(
    auth: AuthDep,
    client_id: str = Query(..., description="Client ID"),
) -> dict[str, object]:
    """Return real coin-level PnL breakdown from bills ledger + trades."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    ta = compute_coin_breakdown(cid)
    coins = [
        {
            "symbol": c.symbol,
            "realized_pnl": c.realized_pnl,
            "trading_fees": c.trading_fees,
            "funding_pnl": c.funding_pnl,
            "total_pnl": c.net_pnl,
            "volume_usd": c.volume_usd,
            "trade_count": c.trade_count,
            "allocation_pct": round(c.volume_usd / ta.total_volume_usd, 4) if ta.total_volume_usd > 0 else 0,
        }
        for c in ta.coins
    ]
    return {"client_id": cid, "coins": coins}


def _project_position(p: dict[str, object]) -> dict[str, object]:
    """Project one position row from backfill into the UI shape."""
    info_raw = p.get("info", {})
    info: dict[str, Any] = cast(dict[str, Any], info_raw) if isinstance(info_raw, dict) else {}
    return {
        "symbol": p.get("symbol", info.get("instId", "")),
        "side": p.get("side", info.get("posSide", "")),
        "quantity": float(info.get("pos", p.get("contracts", 0)) or 0),
        "entry_price": float(info.get("avgPx", p.get("entryPrice", 0)) or 0),
        "mark_price": float(info.get("markPx", p.get("markPrice", 0)) or 0),
        "unrealized_pnl": float(info.get("upl", p.get("unrealizedPnl", 0)) or 0),
        "leverage": float(info.get("lever", p.get("leverage", 1)) or 1),
        "liquidation_price": float(info.get("liqPx", 0) or 0),
        "notional_usd": float(info.get("notionalUsd", 0) or 0),
    }


@router.get("/positions")
def get_positions(
    auth: AuthDep,
    client_id: str = Query(..., description="Client ID"),
) -> dict[str, object]:
    """Return current open positions from backfill data."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    positions = _load_json(cid, "positions.json") or []
    return {"client_id": cid, "positions": [_project_position(p) for p in positions]}


def _balance_row(ccy: str, info: dict[str, object]) -> tuple[dict[str, str], float]:
    """Build one balance row and return it alongside its USD contribution."""
    total = float(info.get("total", 0) or 0)
    usd = float(info.get("usd_value", total) or total)
    row = {
        "currency": ccy,
        "free": str(round(float(info.get("free", 0) or 0), 6)),
        "locked": str(round(float(info.get("locked", 0) or 0), 6)),
        "total": str(round(total, 6)),
        "usd_value": str(round(usd, 2)),
    }
    return row, usd


@router.get("/balances")
def get_balances(
    auth: AuthDep,
    client_id: str = Query(..., description="Client ID"),
) -> dict[str, object]:
    """Return balance breakdown from backfill data."""
    _enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    balance_data = _load_json(cid, "balance.json")
    if not balance_data:
        return {"client_id": cid, "total_equity_usd": 0, "balances": []}

    balances: list[dict[str, str]] = []
    total_equity = 0.0
    if isinstance(balance_data, dict):
        for ccy, info in balance_data.items():
            if not isinstance(info, dict):
                continue
            row, usd = _balance_row(ccy, info)
            balances.append(row)
            total_equity += usd
    return {"client_id": cid, "total_equity_usd": total_equity, "balances": balances}
