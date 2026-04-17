"""GET /invoices — all invoices projected into the UI Invoice type shape."""

from __future__ import annotations

from fastapi import APIRouter, Query

from client_reporting_api.api.routes.reporting._shared import state_mgr
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES
from client_reporting_api.core.tranche_router import load_registry

router = APIRouter()


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
    org_id: str = Query(default="", description="Filter by org ID"),
) -> list[dict[str, object]]:
    """Return all invoices matching the UI Invoice type shape."""
    all_invs = state_mgr.get_invoices()
    registry = load_registry()
    clients_cfg = registry.get("clients", {})

    result: list[dict[str, object]] = []
    for inv in all_invs:
        cid = str(inv.get("client_id", ""))
        cfg = clients_cfg.get(cid, {})
        client_org = str(cfg.get("organisation_id", "")) if isinstance(cfg, dict) else ""
        if org_id and client_org != org_id:
            continue
        result.append(_invoice_row(inv, client_org, cid))
    return result
