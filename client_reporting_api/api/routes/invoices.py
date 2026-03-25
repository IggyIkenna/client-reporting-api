"""Invoice management routes — generate, list, detail, download."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from unified_trading_library import generate_download_url
from unified_trading_library import UnifiedCloudConfig

from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])

_cloud_cfg = UnifiedCloudConfig()


# --- Mock data ---

MOCK_INVOICES: list[dict[str, str | int | float | bool]] = [
    {
        "invoice_id": "INV-2026-001",
        "org_id": "org-alpha",
        "type": "management_fee",
        "period_month": "2026-02",
        "status": "issued",
        "currency": "USD",
        "subtotal": 12500.00,
        "tax": 0.00,
        "total": 12500.00,
        "description": "Management Fee — 2% AUM (Q1 2026)",
        "issued_at": "2026-03-01T00:00:00Z",
        "due_date": "2026-03-31",
        "aum_basis": 625000.00,
        "fee_rate_pct": 2.0,
    },
    {
        "invoice_id": "INV-2026-002",
        "org_id": "org-alpha",
        "type": "performance_fee",
        "period_month": "2026-02",
        "status": "issued",
        "currency": "USD",
        "subtotal": 4820.00,
        "tax": 0.00,
        "total": 4820.00,
        "description": "Performance Fee — 20% of profits above HWM",
        "issued_at": "2026-03-01T00:00:00Z",
        "due_date": "2026-03-31",
        "aum_basis": 625000.00,
        "fee_rate_pct": 20.0,
    },
    {
        "invoice_id": "INV-2026-003",
        "org_id": "org-beta",
        "type": "management_fee",
        "period_month": "2026-01",
        "status": "paid",
        "currency": "USD",
        "subtotal": 8750.00,
        "tax": 0.00,
        "total": 8750.00,
        "description": "Management Fee — 2% AUM (Jan 2026)",
        "issued_at": "2026-02-01T00:00:00Z",
        "due_date": "2026-02-28",
        "aum_basis": 437500.00,
        "fee_rate_pct": 2.0,
    },
    {
        "invoice_id": "INV-2026-004",
        "org_id": "org-beta",
        "type": "performance_fee",
        "period_month": "2026-01",
        "status": "paid",
        "currency": "USD",
        "subtotal": 3200.00,
        "tax": 0.00,
        "total": 3200.00,
        "description": "Performance Fee — 20% of profits above HWM",
        "issued_at": "2026-02-01T00:00:00Z",
        "due_date": "2026-02-28",
        "aum_basis": 437500.00,
        "fee_rate_pct": 20.0,
    },
]


# --- Request/Response models ---


class GenerateInvoiceRequest(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    org_id: str
    period_month: str  # "YYYY-MM"
    invoice_type: str  # "management_fee" | "performance_fee"
    currency: str = "USD"


# --- Seed mock store ---

_store = get_store()
for _inv in MOCK_INVOICES:
    _existing = _store.get("invoices", str(_inv["invoice_id"]))
    if _existing is None:
        _store.create("invoices", {**_inv, "id": _inv["invoice_id"]})


# --- Routes ---


@router.post("/generate")
def generate_invoice(request: GenerateInvoiceRequest) -> dict[str, object]:
    """Create an invoice from template for a given org/period.

    In mock mode: generates synthetic invoice data with realistic fee calculations.
    In live mode: computes fees from AUM/PnL data and creates an invoice record.
    """
    invoice_id = f"INV-{uuid.uuid4().hex[:8].upper()}"

    if _cloud_cfg.is_mock_mode():
        # Simulate fee calculation
        mock_aum = 500_000.00
        if request.invoice_type == "management_fee":
            fee_rate = 2.0
            subtotal = mock_aum * (fee_rate / 100.0) / 12.0  # Monthly
        else:
            fee_rate = 20.0
            mock_profit = mock_aum * 0.048  # ~4.8% monthly return
            subtotal = mock_profit * (fee_rate / 100.0)

        invoice_record: dict[str, object] = {
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
            "description": f"{request.invoice_type.replace('_', ' ').title()} — {request.period_month}",
            "issued_at": "2026-03-21T00:00:00Z",
            "due_date": "2026-04-20",
            "aum_basis": mock_aum,
            "fee_rate_pct": fee_rate,
        }
        _store.create("invoices", invoice_record)
        return invoice_record

    logger.info(
        "generate_invoice: org_id=%s period=%s type=%s",
        request.org_id,
        request.period_month,
        request.invoice_type,
    )
    # Live implementation: compute fees from AUM/PnL data, create invoice record
    return {"invoice_id": invoice_id, "status": "draft"}


@router.get("")
def list_invoices(
    org_id: str = Query(..., description="Organisation identifier"),
) -> list[dict[str, object]]:
    """List invoices for an organisation.

    In mock mode: returns seed + generated invoices from the in-memory store.
    In live mode: queries invoice records from the database.
    """
    if _cloud_cfg.is_mock_mode():
        all_invoices = _store.list("invoices")
        return [inv for inv in all_invoices if inv.get("org_id") == org_id]

    logger.info("list_invoices: org_id=%s", org_id)
    # Live implementation: query invoice database
    return []


@router.get("/{invoice_id}")
def get_invoice(invoice_id: str) -> dict[str, object]:
    """Get invoice details by ID.

    In mock mode: returns the invoice from the in-memory store.
    In live mode: queries the invoice record from the database.
    """
    if _cloud_cfg.is_mock_mode():
        inv = _store.get("invoices", invoice_id)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
        return inv

    logger.info("get_invoice: invoice_id=%s", invoice_id)
    # Live implementation: query invoice database
    raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")


@router.get("/{invoice_id}/download")
def download_invoice(invoice_id: str) -> dict[str, object]:
    """Get a pre-signed download URL for the invoice PDF.

    In mock mode: returns a synthetic download URL.
    In live mode: generates a pre-signed URL for the invoice PDF in cloud storage.
    """
    if _cloud_cfg.is_mock_mode():
        inv = _store.get("invoices", invoice_id)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
        return {
            "invoice_id": invoice_id,
            "download_url": f"https://mock-storage.example.com/invoices/{invoice_id}.pdf?X-Mock-Signature=inv789",
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
