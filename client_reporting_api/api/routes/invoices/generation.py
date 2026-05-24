"""Invoice generation and basic CRUD endpoints."""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from unified_trading_library import (
    AuthContext,
    create_api_auth,
    generate_download_url,
)

from client_reporting_api.api.routes.invoices._shared import (
    GenerateInvoiceRequest,
    cloud_cfg,
    state_mgr,
    store,
)
from client_reporting_api.core.entitlement import (
    enforce_entitlement,  # pyright: ignore[reportPrivateUsage]
    require_internal,
)
from client_reporting_api.core.tranche_router import load_registry

logger = logging.getLogger(__name__)

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _build_mock_invoice(request: GenerateInvoiceRequest, invoice_id: str) -> dict[str, object]:
    """Build a synthetic invoice record for mock mode (no live state touched)."""
    mock_aum = 500_000.00
    if request.invoice_type == "management_fee":
        fee_rate = 2.0
        subtotal = mock_aum * (fee_rate / 100.0) / 12.0  # Monthly
    else:
        fee_rate = 20.0
        mock_profit = mock_aum * 0.048  # ~4.8% monthly return
        subtotal = mock_profit * (fee_rate / 100.0)

    return {
        "id": invoice_id,
        "invoice_id": invoice_id,
        "org_id": request.org_id,
        "type": request.invoice_type,
        "period_month": request.period_month,
        "status": "draft",
        "currency": request.currency,
        "subtotal": round(subtotal, 2),
        "tax": 0.00,
        "total": round(subtotal, 2),
        "description": (f"{request.invoice_type.replace('_', ' ').title()} \u2014 {request.period_month}"),
        "issued_at": "2026-03-21T00:00:00Z",
        "due_date": "2026-04-20",
        "aum_basis": mock_aum,
        "fee_rate_pct": fee_rate,
    }


def _clients_for_org(org_id: str) -> list[dict[str, object]]:
    """Return the billable clients that belong to ``org_id`` in live mode."""
    summary = state_mgr.get_dashboard_summary()
    all_clients = summary["at_hwm"] + summary["underwater"] + summary["prop"]
    registry = load_registry()
    clients_cfg = registry.get("clients", {})
    return cast(
        list[dict[str, object]],
        [c for c in all_clients if clients_cfg.get(str(c.get("client_id", "")), {}).get("organisation_id") == org_id],
    )


def _build_live_invoice_line_items(
    clients: list[dict[str, object]],
) -> tuple[list[dict[str, object]], Decimal]:
    """Sum odum_fee + server_cost into invoice line items; return (items, total)."""
    line_items: list[dict[str, object]] = []
    total = Decimal("0")
    for c in clients:
        odum_fee = c.get("odum_fee", Decimal("0"))
        server_cost = c.get("server_cost", Decimal("0"))
        if isinstance(odum_fee, Decimal) and odum_fee > 0:
            line_items.append(
                {
                    "client_id": str(c["client_id"]),
                    "fee_type": "odum_fee",
                    "amount": float(odum_fee),
                }
            )
            total += odum_fee
        if isinstance(server_cost, Decimal) and server_cost > 0:
            line_items.append(
                {
                    "client_id": str(c["client_id"]),
                    "fee_type": "server_cost",
                    "amount": float(server_cost),
                }
            )
            total += server_cost
    return line_items, total


@router.post("/generate")
def generate_invoice(request: GenerateInvoiceRequest, auth: AuthDep) -> dict[str, object]:
    """Create an invoice from template for a given org/period.

    Invoice generation is a billing-ops action — internal-only.
    """
    require_internal(auth)
    invoice_id = f"INV-{uuid.uuid4().hex[:8].upper()}"

    if cloud_cfg.is_mock_mode():
        invoice_record = _build_mock_invoice(request, invoice_id)
        store.create("invoices", invoice_record)
        return invoice_record

    logger.info(
        "generate_invoice: org_id=%s period=%s type=%s",
        request.org_id,
        request.period_month,
        request.invoice_type,
    )
    org_clients = _clients_for_org(request.org_id)
    if not org_clients:
        return {"invoice_id": invoice_id, "status": "draft", "note": "No billable clients for org"}

    line_items, total = _build_live_invoice_line_items(org_clients)
    return {
        "invoice_id": invoice_id,
        "org_id": request.org_id,
        "period_month": request.period_month,
        "status": "draft",
        "currency": request.currency,
        "line_items": line_items,
        "total": float(total),
    }


@router.get("/")
def list_invoices(
    auth: AuthDep,
    org_id: str = Query(..., description="Organisation identifier"),
) -> list[dict[str, object]]:
    """List invoices for an organisation.

    External callers MUST pass their own ``org_id`` (matched via
    :func:`enforce_entitlement`); internal callers may pass any
    ``org_id`` to inspect another org's invoice history.
    """
    enforce_entitlement(auth, org_id)
    if cloud_cfg.is_mock_mode():
        all_invoices = store.list("invoices")
        return [inv for inv in all_invoices if inv.get("org_id") == org_id]

    registry = load_registry()
    org_client_ids = {cid for cid, cfg in registry.get("clients", {}).items() if cfg.get("organisation_id") == org_id}
    all_invoices = state_mgr.get_invoices()
    return [
        {k: (float(v) if isinstance(v, Decimal) else v) for k, v in inv.items()}
        for inv in all_invoices
        if inv.get("client_id") in org_client_ids
    ]


@router.get("/{invoice_id}")
def get_invoice(invoice_id: str, auth: AuthDep) -> dict[str, object]:
    """Get invoice details by ID.

    Invoices are looked up by opaque ``invoice_id`` so an external caller
    cannot derive a valid ID for someone else's invoice; even so, the
    payload exposes ``org_id`` and ``client_id`` so we restrict to
    internal callers until per-org invoice scoping is added.
    """
    require_internal(auth)
    if cloud_cfg.is_mock_mode():
        inv = store.get("invoices", invoice_id)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
        return inv

    all_invoices = state_mgr.get_invoices()
    for inv in all_invoices:
        if inv.get("invoice_id") == invoice_id:
            return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in inv.items()}
    raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")


@router.get("/{invoice_id}/download")
def download_invoice(invoice_id: str, auth: AuthDep) -> dict[str, object]:
    """Get a pre-signed download URL for the invoice PDF.

    Internal-only — see :func:`get_invoice`.
    """
    require_internal(auth)
    if cloud_cfg.is_mock_mode():
        inv = store.get("invoices", invoice_id)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
        return {
            "invoice_id": invoice_id,
            "download_url": (f"https://mock-storage.example.com/invoices/{invoice_id}.pdf?X-Mock-Signature=inv789"),
            "expires_in_minutes": 60,
            "filename": f"{invoice_id}.pdf",
        }

    logger.info("download_invoice: invoice_id=%s", invoice_id)
    download_url = generate_download_url(
        bucket="client-reporting-invoices",
        object_path=f"invoices/{invoice_id}/{invoice_id}.pdf",
        expiry_minutes=60,
    )
    return {
        "invoice_id": invoice_id,
        "download_url": download_url,
        "expires_in_minutes": 60,
        "filename": f"{invoice_id}.pdf",
    }
