# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportArgumentType=false
"""GET /fund-operations — investor register, capital accounts, distribution waterfall."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.reporting._shared import _resolve_client_ids, _vehicle_type_for_client
from client_reporting_api.core.backfill_store import get_equity_curve
from client_reporting_api.core.entitlement import require_internal
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES, compute_pnl_series
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _investor_row(cid: str, cfg: dict[str, object], net_deposits: float) -> dict[str, object]:
    """Build one investor-register row from a client config + net-deposits."""
    tranche = str(cfg.get("tranche", "managed"))
    investor_type = "GP" if tranche == "managed" else "LP"
    return {
        "name": CLIENT_NAMES.get(cid, str(cfg.get("full_name", cid))),
        "type": investor_type,
        "vehicleType": _vehicle_type_for_client(cfg),
        "commitment": net_deposits,
        "drawn": net_deposits,
        "remaining": 0,
        "status": "Active" if cfg.get("is_active") else "Suspended",
    }


def _capital_account_rows(
    starting: float, net_deposits: float, trading_pnl: float, current_eq: float
) -> list[dict[str, object]]:
    """Build the ledger-style capital-account rows for a single investor."""
    return [
        {"label": "Opening Balance", "value": starting},
        {"label": "Net Deposits", "value": net_deposits},
        {"label": "Trading PnL", "value": trading_pnl},
        {"label": "Current Equity", "value": current_eq},
    ]


def _distribution_waterfall(total_pnl: float) -> list[dict[str, object]]:
    """Return the 5-tier distribution waterfall given the aggregate PnL."""
    odum_share = total_pnl * 0.30  # avg odum fee ~30%
    trader_share = total_pnl * 0.10
    investor_share = total_pnl - odum_share - trader_share
    return [
        {
            "tier": "Return of Capital",
            "description": "Net deposits returned first",
            "amount": 0,
            "cumulative": 0,
        },
        {
            "tier": "Preferred Return",
            "description": "8% hurdle (not applicable)",
            "amount": 0,
            "cumulative": 0,
        },
        {
            "tier": "Odum Fee (30%)",
            "description": "Performance fee to Odum",
            "amount": round(odum_share, 2),
            "cumulative": round(odum_share, 2),
        },
        {
            "tier": "Trader Fee (10%)",
            "description": "Performance fee to trader",
            "amount": round(trader_share, 2),
            "cumulative": round(odum_share + trader_share, 2),
        },
        {
            "tier": "Investor Return",
            "description": "Net to investor",
            "amount": round(investor_share, 2),
            "cumulative": round(total_pnl, 2),
        },
    ]


def _walk_clients_for_fund_ops(
    ids: list[str], clients_cfg: object
) -> tuple[list[dict[str, object]], dict[str, list[dict[str, object]]], float, float, dict[str, float]]:
    """Accumulate per-investor rows + capital accounts + aggregate AUM/PnL + per-vehicle-type AUM."""
    investors: list[dict[str, object]] = []
    capital_accounts: dict[str, list[dict[str, object]]] = {}
    total_aum = 0.0
    total_pnl = 0.0
    aum_by_vehicle_type: dict[str, float] = {"fund": 0.0, "sma": 0.0}
    if not isinstance(clients_cfg, dict):
        return investors, capital_accounts, total_aum, total_pnl, aum_by_vehicle_type

    for cid in ids:
        cfg = clients_cfg.get(cid, {})
        if not isinstance(cfg, dict) or not cfg.get("is_active"):
            continue
        ec = get_equity_curve(cid)
        if not ec:
            continue
        pnl_data = compute_pnl_series(cid)
        if not pnl_data:
            continue

        starting = float(pnl_data.get("starting_equity", 0))
        current_eq = float(pnl_data.get("current_equity", 0))
        net_deposits_alone = float(pnl_data.get("net_deposits", 0))
        net_deposits = net_deposits_alone + starting
        trading_pnl = float(pnl_data.get("trading_pnl", 0))

        total_aum += current_eq
        total_pnl += trading_pnl
        vehicle_type = _vehicle_type_for_client(cast(dict[str, object], cfg))
        aum_by_vehicle_type[vehicle_type] = aum_by_vehicle_type.get(vehicle_type, 0.0) + current_eq

        investors.append(_investor_row(cid, cast(dict[str, object], cfg), net_deposits))
        capital_accounts[CLIENT_NAMES.get(cid, cid)] = _capital_account_rows(
            starting, net_deposits_alone, trading_pnl, current_eq
        )
    return investors, capital_accounts, total_aum, total_pnl, aum_by_vehicle_type


@router.get("/fund-operations")
def get_fund_operations(
    auth: AuthDep,
    client_ids: str = Query(default="", description="Comma-separated client IDs"),
) -> dict[str, object]:
    """Return real investor register + capital accounts + distribution waterfall.

    Cross-client aggregate — internal-only.
    """
    require_internal(auth)
    ids = _resolve_client_ids(client_ids)
    registry = load_registry()
    clients_cfg = registry.get("clients", {})

    investors, capital_accounts, total_aum, total_pnl, aum_by_vehicle_type = _walk_clients_for_fund_ops(
        ids, clients_cfg
    )

    return {
        "investors": investors,
        "capital_accounts": capital_accounts,
        "distribution_waterfall": _distribution_waterfall(total_pnl),
        "total_aum": round(total_aum, 2),
        "total_pnl": round(total_pnl, 2),
        "investor_count": len(investors),
        "aum_by_vehicle_type": {k: round(v, 2) for k, v in aum_by_vehicle_type.items()},
    }
