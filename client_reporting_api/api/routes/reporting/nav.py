# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportArgumentType=false, reportReturnType=false, reportGeneralTypeIssues=false
"""GET /nav — total NAV across clients + hourly series + fees + capital flows."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.reporting._shared import (
    _resolve_client_ids,
    state_mgr,
)
from client_reporting_api.core.backfill_store import get_equity_curve
from client_reporting_api.core.entitlement import require_internal
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES, compute_pnl_series
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _vehicle_type_for_client(cfg: dict[str, object]) -> str:
    """Classify a client's investment vehicle for NAV reporting.

    ``client-reporting-api`` has no direct join to UAC's
    ``ClientRegistry.vehicle_type`` (disjoint client_id namespaces — this
    service's ids are the ``credentials-registry.yaml`` tranche keys, e.g.
    "PR"/"IK", not UAC's "acme-fund"/"patrick-elysium"). The equivalent signal
    already on ``ClientConfig`` is ``is_pooled``: a pooled account (multiple
    investors sharing one NAV pool via ``pool_investors``) is the reporting
    analogue of UAC's "fund" vehicle; a direct single-investor managed account
    is the analogue of "sma".
    """
    return "fund" if cfg.get("is_pooled") else "sma"


def _nav_investor_for_client(cid: str, cfg: dict[str, object]) -> tuple[dict[str, object], float, float] | None:
    """Build a NAV investor row plus the (current_eq, peak) it contributes.

    Returns ``None`` for inactive clients or when no equity history is available.
    """
    if not cfg.get("is_active"):
        return None
    ec = get_equity_curve(cid)
    if not ec:
        return None
    current_eq = float(ec[-1].get("equity_usd", 0))
    peak = max(float(p.get("equity_usd", 0)) for p in ec)
    investor = {
        "name": CLIENT_NAMES.get(cid, cid),
        "class": str(cfg.get("currency", "USDT")),
        "vehicleType": _vehicle_type_for_client(cfg),
        "commitment": current_eq,
        "navShare": current_eq,
        "pctOfFund": 0,  # set after total_nav is known
        "inceptionDate": str(ec[0].get("date", "")),
    }
    return investor, current_eq, peak


def _aggregate_nav_investors(
    ids: list[str], clients_cfg: object
) -> tuple[list[dict[str, object]], float, float, dict[str, float]]:
    """Walk ``ids`` and build investors list + total NAV + total HWM + per-vehicle-type NAV."""
    investors: list[dict[str, object]] = []
    total_nav = 0.0
    total_hwm = 0.0
    nav_by_vehicle_type: dict[str, float] = {"fund": 0.0, "sma": 0.0}
    if not isinstance(clients_cfg, dict):
        return investors, total_nav, total_hwm, nav_by_vehicle_type
    for cid in ids:
        cfg = clients_cfg.get(cid, {})
        if not isinstance(cfg, dict):
            continue
        result = _nav_investor_for_client(cid, cast(dict[str, object], cfg))
        if result is None:
            continue
        investor, current_eq, peak = result
        investors.append(investor)
        total_nav += current_eq
        total_hwm = max(total_hwm, peak)
        vehicle_type = str(investor["vehicleType"])
        nav_by_vehicle_type[vehicle_type] = nav_by_vehicle_type.get(vehicle_type, 0.0) + current_eq
    for inv in investors:
        if total_nav > 0:
            inv["pctOfFund"] = round(float(inv["navShare"]) / total_nav * 100, 1)
    return investors, total_nav, total_hwm, nav_by_vehicle_type


def _hourly_nav_series(ids: list[str]) -> list[dict[str, object]]:
    """Build a 30-day NAV-by-day series from combined equity curves."""
    daily_nav: dict[str, float] = {}
    for cid in ids:
        ec = get_equity_curve(cid)
        if not ec:
            continue
        for p in ec[-30:]:
            date = str(p.get("date", ""))
            daily_nav[date] = daily_nav.get(date, 0) + float(p.get("equity_usd", 0))
    prev_nav = 0.0
    out: list[dict[str, object]] = []
    for date in sorted(daily_nav.keys()):
        nav = daily_nav[date]
        change = nav - prev_nav if prev_nav > 0 else 0
        change_pct = (change / prev_nav * 100) if prev_nav > 0 else 0
        out.append(
            {
                "hour": date,
                "nav": round(nav, 2),
                "change": round(change, 2),
                "changePct": round(change_pct, 2),
            }
        )
        prev_nav = nav
    return out


def _nav_fee_summary() -> list[dict[str, object]]:
    """Aggregate the fee summary from real invoice data."""
    odum_fees_ytd = sum(
        float(inv.get("amount", 0))
        for inv in state_mgr.get_invoices()
        if str(inv.get("invoice_id", "")).startswith("INV-")
    )
    return [
        {
            "category": "Performance Fee (Odum)",
            "rate": "30-35%",
            "annualAmount": round(odum_fees_ytd * 4, 2),
            "ytdAccrued": round(odum_fees_ytd, 2),
        },
        {"category": "Trader Fee", "rate": "10-20%", "annualAmount": 0, "ytdAccrued": 0},
        {
            "category": "Server Costs",
            "rate": "$50/underwater",
            "annualAmount": 2400,
            "ytdAccrued": 0,
        },
    ]


def _capital_flows_for_clients(ids: list[str]) -> list[dict[str, object]]:
    """Project per-client transfer events as fund-level capital flows."""
    flows: list[dict[str, object]] = []
    for cid in ids:
        pnl_data = compute_pnl_series(cid)
        if not pnl_data:
            continue
        for t in pnl_data.get("transfers", []):
            if not isinstance(t, dict):
                continue
            amt = float(t.get("amount", 0))
            flows.append(
                {
                    "id": f"{cid}-{t.get('date', '')}",
                    "date": str(t.get("date", "")),
                    "type": "Subscription" if amt > 0 else "Redemption",
                    "investor": CLIENT_NAMES.get(cid, cid),
                    "amount": abs(amt),
                    "status": "Settled",
                }
            )
    return flows


@router.get("/nav")
def get_nav(
    auth: AuthDep,
    client_ids: str = Query(default="", description="Comma-separated client IDs"),
) -> dict[str, object]:
    """Return real NAV data aggregated across clients.

    Cross-client aggregate — internal-only to avoid data leakage.
    """
    require_internal(auth)
    ids = _resolve_client_ids(client_ids)
    registry = load_registry()
    clients_cfg = registry.get("clients", {})

    nav_investors, total_nav, total_hwm, nav_by_vehicle_type = _aggregate_nav_investors(ids, clients_cfg)
    hourly_nav = _hourly_nav_series(ids)

    return {
        "current_nav": round(total_nav, 2),
        "aum": round(total_nav, 2),
        "investor_count": len(nav_investors),
        "high_water_mark": round(total_hwm, 2),
        "daily_change_pct": hourly_nav[-1]["changePct"] if hourly_nav else 0,
        "mtd_return_pct": 0,
        "hourly_nav": hourly_nav,
        "investors": nav_investors,
        "nav_by_vehicle_type": {k: round(v, 2) for k, v in nav_by_vehicle_type.items()},
        "fees": _nav_fee_summary(),
        "capital_flows": _capital_flows_for_clients(ids),
    }
