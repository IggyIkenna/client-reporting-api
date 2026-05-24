# pyright: reportArgumentType=false, reportUnknownArgumentType=false
"""GET /invoices — all invoices projected into the UI Invoice type shape."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.reporting._shared import state_mgr
from client_reporting_api.core.entitlement import enforce_entitlement
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _invoice_row(inv: dict[str, object], client_org: str, cid: str) -> dict[str, object]:
    """Project one raw invoice into the UI's Invoice type shape."""
    return {
        "invoice_id": inv.get("invoice_id", ""),
        "org_id": client_org,
        "type": "performance_fee",
        "period_month": str(inv.get("date", ""))[:7],
        "status": "paid" if inv.get("paid") else "issued",
        "currency": "USD",
        "subtotal": float(inv.get("amount", 0)),
        "tax": 0,
        "total": float(inv.get("amount", 0)),
        "description": f"Performance fee for {CLIENT_NAMES.get(cid, cid)}",
        "issued_at": str(inv.get("date", "")),
        "due_date": str(inv.get("date", "")),
        "is_underwater": False,
        "server_cost": 0,
        "payment_txid": None,
        "notes": "",
    }


@router.get("/invoices")
def get_invoices(
    auth: AuthDep,
    org_id: str = Query(default="", description="Filter by org ID"),
) -> list[dict[str, object]]:
    """Return all invoices matching the UI Invoice type shape.

    External callers MUST supply ``org_id`` matching their own
    ``AuthContext.org_id`` — enforced via :func:`enforce_entitlement`.
    Internal callers may omit ``org_id`` to list all invoices.
    """
    if not auth.is_internal:
        # External caller: org_id must match their own. The reuse of
        # ``enforce_entitlement`` treats the org_id filter as the
        # tenant scope for invoices (invoices hang off org_id, not
        # client_id).
        enforce_entitlement(auth, org_id)
    all_invs = state_mgr.get_invoices()
    registry = load_registry()
    clients_cfg = registry.get("clients", {})

    result: list[dict[str, object]] = []
    for inv in all_invs:
        cid = str(inv.get("client_id", ""))
        cfg = clients_cfg.get(cid, {})
        client_org = str(cast(dict[str, object], cfg).get("organisation_id", "")) if cfg else ""
        if org_id and client_org != org_id:
            continue
        result.append(_invoice_row(inv, client_org, cid))
    return result
