"""GET /reports — overview page with portfolio + monthly HTML + invoices + balances."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.reporting._shared import (
    _load_json,
    _resolve_client_ids,
    state_mgr,
)
from client_reporting_api.core.backfill_store import (
    compute_performance_stats,
    get_equity_curve,
)
from client_reporting_api.core.entitlement import require_internal
from client_reporting_api.core.hwm_seeds import get_hwm_seed, get_pnl_recovery_seed
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES, compute_pnl_series
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "data" / "reports"


def _portfolio_entry(cid: str) -> dict[str, object] | None:
    """Build a single portfolio summary entry, or None when data is missing."""
    ec = get_equity_curve(cid)
    if not ec:
        return None
    stats = compute_performance_stats(
        ec,
        hwm_seed=get_hwm_seed(cid),
        pnl_recovery=get_pnl_recovery_seed(cid),
    )
    pnl_data = compute_pnl_series(cid)
    if not pnl_data:
        return None
    positions = _load_json(cid, "positions.json") or []
    return {
        "name": CLIENT_NAMES.get(cid, cid),
        "client": CLIENT_NAMES.get(cid, cid),
        "clientId": cid,
        "aum": pnl_data.get("current_equity", 0),
        "pnl": pnl_data.get("trading_pnl", 0),
        "pnlPct": float(stats.get("simple_return_pct", 0)),
        "mtdReturn": 0,
        "ytdReturn": float(stats.get("total_return_pct", 0)),
        "sharpe": float(stats.get("sharpe_ratio", 0)),
        "positions": len(positions),
    }


def _list_generated_reports() -> list[dict[str, object]]:
    """List the most recent generated monthly report HTML files."""
    if not _REPORTS_DIR.exists():
        return []
    out: list[dict[str, object]] = []
    for f in sorted(_REPORTS_DIR.glob("*_report.html"), reverse=True)[:20]:
        parts = f.stem.split("_")
        if len(parts) < 2:
            continue
        cid, month = parts[0], parts[1]
        out.append(
            {
                "id": f.stem,
                "name": f"{CLIENT_NAMES.get(cid, cid)} - {month}",
                "type": "pnl",
                "status": "ready",
                "date": f"{month}-01",
                "client": CLIENT_NAMES.get(cid, cid),
                "clientId": cid,
                "format": "HTML",
            }
        )
    return out


def _account_balance_entry(cid: str) -> dict[str, object] | None:
    """Build an account-balance entry from the latest equity-curve point."""
    ec = get_equity_curve(cid)
    if not ec:
        return None
    last_eq = float(ec[-1].get("equity_usd", 0))
    registry = load_registry()
    client_cfg = registry.get("clients", {}).get(cid, {})
    venue = str(client_cfg.get("venue", "")) if isinstance(client_cfg, dict) else ""
    currency = str(client_cfg.get("currency", "")) if isinstance(client_cfg, dict) else ""
    return {
        "venue": venue,
        "currency": currency,
        "free": last_eq,
        "locked": 0,
        "total": last_eq,
    }


def _client_transfers(cid: str) -> list[dict[str, object]]:
    """Project transfers for a single client to the UI shape."""
    pnl_data = compute_pnl_series(cid)
    if not pnl_data:
        return []
    out: list[dict[str, object]] = []
    for t in pnl_data.get("transfers", []):
        if not isinstance(t, dict):
            continue
        amt = float(t.get("amount", 0))
        out.append(
            {
                "time": str(t.get("date", "")),
                "from": "External" if amt > 0 else CLIENT_NAMES.get(cid, cid),
                "to": CLIENT_NAMES.get(cid, cid) if amt > 0 else "External",
                "amount": f"${abs(amt):,.0f}",
                "status": "settled",
            }
        )
    return out


@router.get("/reports")
def get_reports_overview(
    auth: AuthDep,
    client_ids: str = Query(default="", description="Comma-separated client IDs"),
) -> dict[str, object]:
    """Aggregate portfolio / monthly reports / invoices / balances / transfers for the UI.

    Cross-client aggregate — internal-only.
    """
    require_internal(auth)
    ids = _resolve_client_ids(client_ids)
    portfolio = [entry for cid in ids if (entry := _portfolio_entry(cid))]
    account_balances = [entry for cid in ids if (entry := _account_balance_entry(cid))]
    recent_transfers: list[dict[str, object]] = []
    for cid in ids:
        recent_transfers.extend(_client_transfers(cid))

    invoice_list = [
        {
            "id": inv.get("invoice_id", ""),
            "client": inv.get("client_id", ""),
            "amount": float(inv.get("amount", 0)),
            "status": "paid" if inv.get("paid") else "unpaid",
            "date": str(inv.get("date", "")),
        }
        for inv in state_mgr.get_invoices()
    ]

    return {
        "data": _list_generated_reports(),
        "portfolioSummary": portfolio,
        "invoices": invoice_list,
        "accountBalances": account_balances,
        "recentTransfers": recent_transfers,
    }
