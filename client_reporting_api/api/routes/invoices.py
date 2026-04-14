"""Invoice management routes — generate, list, detail, download.

Live mode uses InvoiceStateManager (hardcoded seed from mr_report,
overlaid with live equity from GCS). Mock mode uses in-memory store.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from unified_trading_library import UnifiedCloudConfig, generate_download_url

from client_reporting_api.core.backfill_store import (
    compute_performance_stats,
    get_equity_curve,
)
from client_reporting_api.core.dashboard_generator import (
    generate_all_dashboards,
    generate_dashboard,
)
from client_reporting_api.core.invoice_generator import (
    generate_all_invoices,
    get_all_2026_invoices,
    get_invoice_html_path,
)
from client_reporting_api.core.invoice_state import InvoiceStateManager
from client_reporting_api.core.monthly_report_generator import (
    generate_monthly_report,
)
from client_reporting_api.core.pnl_chart_generator import (
    generate_all_charts,
    generate_pnl_chart,
)
from client_reporting_api.core.trade_analytics import (
    CLIENT_IDS,
    aggregate_clients,
    compute_coin_breakdown,
)
from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])

_cloud_cfg = UnifiedCloudConfig()
_state_mgr = InvoiceStateManager()


# --- Mock data ---

MOCK_INVOICES: list[dict[str, str | int | float | bool]] = [
    {
        "invoice_id": "INV-2026-001",
        "client_id": "PR",
        "org_id": "org-alpha",
        "type": "performance_fee",
        "period_month": "2026-03",
        "status": "issued",
        "currency": "USD",
        "subtotal": 4820.00,
        "tax": 0.00,
        "total": 4820.00,
        "description": "Performance Fee — Prism Capital (Mar 2026)",
        "issued_at": "2026-04-01T00:00:00Z",
        "due_date": "2026-04-30",
        "opening_aum": 300000.00,
        "closing_aum": 324100.00,
        "pnl": 24100.00,
        "trader_hwm_before": 300000.00,
        "odum_hwm_before": 300000.00,
        "trader_fee": 4820.00,
        "odum_fee": 1205.00,
        "is_underwater": False,
    },
    {
        "invoice_id": "INV-2026-002",
        "client_id": "ET",
        "org_id": "org-alpha",
        "type": "performance_fee",
        "period_month": "2026-03",
        "status": "paid",
        "currency": "USD",
        "subtotal": 3440.00,
        "tax": 0.00,
        "total": 3440.00,
        "description": "Performance Fee — Eqvilent (Mar 2026)",
        "issued_at": "2026-04-01T00:00:00Z",
        "due_date": "2026-04-30",
        "opening_aum": 2020000.00,
        "closing_aum": 2037200.00,
        "pnl": 17200.00,
        "trader_hwm_before": 2020000.00,
        "odum_hwm_before": 2020000.00,
        "trader_fee": 3440.00,
        "odum_fee": 860.00,
        "is_underwater": False,
    },
    {
        "invoice_id": "INV-2026-003",
        "client_id": "GP",
        "org_id": "org-beta",
        "type": "performance_fee",
        "period_month": "2026-03",
        "status": "issued",
        "currency": "USD",
        "subtotal": 50.00,
        "tax": 0.00,
        "total": 50.00,
        "description": "Server Cost — GPD Capital (underwater)",
        "issued_at": "2026-04-01T00:00:00Z",
        "due_date": "2026-04-30",
        "opening_aum": 409000.00,
        "closing_aum": 406955.00,
        "pnl": -2045.00,
        "trader_hwm_before": 420000.00,
        "odum_hwm_before": 415000.00,
        "trader_fee": 0.00,
        "odum_fee": 0.00,
        "server_cost": 50.00,
        "is_underwater": True,
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
            "description": (
                f"{request.invoice_type.replace('_', ' ').title()} \u2014 {request.period_month}"
            ),
            "issued_at": "2026-03-21T00:00:00Z",
            "due_date": "2026-04-20",
            "aum_basis": mock_aum,
            "fee_rate_pct": fee_rate,
        }
        _store.create("invoices", invoice_record)
        return invoice_record

    # Live: compute fees from HWM state + live equity
    logger.info(
        "generate_invoice: org_id=%s period=%s type=%s",
        request.org_id,
        request.period_month,
        request.invoice_type,
    )
    summary = _state_mgr.get_dashboard_summary()
    all_clients = summary["at_hwm"] + summary["underwater"] + summary["prop"]
    # Find clients belonging to this org
    from client_reporting_api.core.tranche_router import load_registry

    registry = load_registry()
    org_clients = [
        c
        for c in all_clients
        if registry.get("clients", {}).get(str(c.get("client_id", "")), {}).get("organisation_id")
        == request.org_id
    ]
    if not org_clients:
        return {"invoice_id": invoice_id, "status": "draft", "note": "No billable clients for org"}

    line_items = []
    total = Decimal("0")
    for c in org_clients:
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

    return {
        "invoice_id": invoice_id,
        "org_id": request.org_id,
        "period_month": request.period_month,
        "status": "draft",
        "currency": request.currency,
        "line_items": line_items,
        "total": float(total),
    }


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

    # Live: return historical invoices from seed data
    from client_reporting_api.core.tranche_router import load_registry

    registry = load_registry()
    # Find client IDs belonging to this org
    org_client_ids = {
        cid
        for cid, cfg in registry.get("clients", {}).items()
        if cfg.get("organisation_id") == org_id
    }
    all_invoices = _state_mgr.get_invoices()
    return [
        {k: (float(v) if isinstance(v, Decimal) else v) for k, v in inv.items()}
        for inv in all_invoices
        if inv.get("client_id") in org_client_ids
    ]


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

    # Live: search seed invoices by ID
    all_invoices = _state_mgr.get_invoices()
    for inv in all_invoices:
        if inv.get("invoice_id") == invoice_id:
            return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in inv.items()}
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
            "download_url": (
                f"https://mock-storage.example.com/invoices/"
                f"{invoice_id}.pdf?X-Mock-Signature=inv789"
            ),
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


# ── State machine transitions ───────────────────────────────────────────────

# Valid transitions: DRAFT→ISSUED, ISSUED→ACCEPTED, ISSUED→DISPUTED,
# ACCEPTED→PAID, DISPUTED→VOIDED, DISPUTED→ISSUED (re-issue),
# any→VOIDED (admin override)
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"issued", "voided"},
    "issued": {"accepted", "disputed", "voided"},
    "accepted": {"paid", "voided"},
    "disputed": {"issued", "voided"},
}


class InvoiceTransitionRequest(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    action: str  # "issue" | "accept" | "dispute" | "pay" | "void"
    note: str = ""
    payment_txid: str | None = None


@router.put("/{invoice_id}/transition")
def transition_invoice(
    invoice_id: str,
    request: InvoiceTransitionRequest,
) -> dict[str, object]:
    """Transition an invoice through the lifecycle state machine."""
    inv = _store.get("invoices", invoice_id)
    if inv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Invoice {invoice_id} not found",
        )

    current = str(inv.get("status", "draft")).lower()
    target = request.action.lower()

    # Map action verbs to status values
    action_to_status = {
        "issue": "issued",
        "accept": "accepted",
        "dispute": "disputed",
        "pay": "paid",
        "void": "voided",
    }
    new_status = action_to_status.get(target)
    if new_status is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action: {request.action}",
        )

    allowed = _VALID_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot transition from {current} to {new_status}. Allowed: {sorted(allowed)}"
            ),
        )

    # Apply transition
    updated = {**inv, "status": new_status}
    if request.note:
        updated["notes"] = request.note
    if request.payment_txid and new_status == "paid":
        updated["payment_txid"] = request.payment_txid

    _store.update("invoices", invoice_id, updated)
    logger.info(
        "Invoice %s transitioned: %s -> %s",
        invoice_id,
        current,
        new_status,
    )
    return updated


# ── Fee dashboard & trader payments (live data from mr_report seed + GCS) ──


def _decimal_safe(obj: object) -> object:
    """Convert Decimals to floats for JSON serialisation."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _decimal_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_safe(item) for item in obj]
    return obj


@router.get("/dashboard/fees")
def fee_dashboard() -> dict[str, object]:
    """Full fee dashboard: all clients grouped by status with current fees.

    Uses hardcoded HWM seed from mr_report, overlaid with live equity
    from GCS backfill data (the trickle).
    """
    summary = _state_mgr.get_dashboard_summary()
    return _decimal_safe(summary)


@router.get("/dashboard/fees/{client_id}")
def client_fee_detail(client_id: str) -> dict[str, object]:
    """Detailed fee breakdown for a single client."""
    fees = _state_mgr.compute_current_fees(client_id.upper())
    if fees is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    # Add invoice history and trader payments
    invoices = _state_mgr.get_invoices(client_id.upper())
    payments = _state_mgr.get_trader_payments(client_id.upper())
    introducer = _state_mgr.get_introducer_state(client_id.upper())

    result: dict[str, object] = {
        "fees": _decimal_safe(fees),
        "invoices": _decimal_safe(invoices),
        "trader_payments": _decimal_safe(payments),
    }
    if introducer:
        result["introducer"] = _decimal_safe(introducer)
    return result


@router.get("/dashboard/trader-payment")
def trader_payment_summary() -> dict[str, object]:
    """Generate trader payment summary (like TRADER_PAYMENT_MESSAGE_FEB_2026.md).

    Shows all at-HWM clients with fees due + underwater server costs.
    """
    summary = _state_mgr.get_dashboard_summary()
    at_hwm = summary.get("at_hwm", [])
    underwater = summary.get("underwater", [])

    performance_fees: list[dict[str, object]] = []
    total_perf = Decimal("0")
    for c in at_hwm:
        net_fee = c.get("trader_fee_net", Decimal("0"))
        if isinstance(net_fee, Decimal) and net_fee > 0:
            performance_fees.append(
                {
                    "client_id": c["client_id"],
                    "current_equity": _decimal_safe(c.get("current_equity", 0)),
                    "trader_hwm": _decimal_safe(c.get("trader_hwm", 0)),
                    "pnl_above_hwm": _decimal_safe(c.get("pnl_above_trader_hwm", 0)),
                    "gross_fee": _decimal_safe(c.get("trader_fee_gross", 0)),
                    "credit_applied": _decimal_safe(c.get("credit_applied", 0)),
                    "net_fee": _decimal_safe(net_fee),
                }
            )
            total_perf += net_fee

    server_costs_count = len(underwater)
    server_costs_total = Decimal("50") * server_costs_count

    return _decimal_safe(
        {
            "performance_fees": performance_fees,
            "total_performance_fees": total_perf,
            "underwater_accounts": [
                {"client_id": c["client_id"], "server_cost": Decimal("50")} for c in underwater
            ],
            "total_server_costs": server_costs_total,
            "total_due": total_perf + server_costs_total,
            "total_trader_credits_outstanding": _state_mgr.get_total_trader_credits(),
        }
    )


@router.get("/dashboard/hwm/{client_id}")
def get_hwm_state(client_id: str) -> dict[str, object]:
    """Get current HWM state for a client."""
    state = _state_mgr.get_hwm_state(client_id.upper())
    if state is None:
        raise HTTPException(status_code=404, detail=f"No HWM state for {client_id}")
    return _decimal_safe(state.model_dump())


@router.get("/dashboard/introducers")
def list_introducers() -> dict[str, object]:
    """List all introducer balances."""
    result: dict[str, object] = {}
    for client_id in ("PR", "ET"):
        intro = _state_mgr.get_introducer_state(client_id)
        if intro:
            result[client_id] = _decimal_safe(intro)
    return result


# ── Portal views — role-based dashboards ──


@router.get("/portal/admin")
def portal_admin() -> dict[str, object]:
    """Admin view — full visibility: all clients, HWM, PnL, fees, recovery.

    Shows effective HWM (base HWM + deposits), new watermark (the level
    the next invoice will be based on), and all fee breakdowns.
    """
    summary = _state_mgr.get_dashboard_summary()
    at_hwm = summary.get("at_hwm", [])
    underwater = summary.get("underwater", [])

    clients: list[dict[str, object]] = []
    for c in at_hwm:
        equity = c.get("current_equity", Decimal("0"))
        clients.append(
            {
                "client_id": c["client_id"],
                "full_name": c.get("full_name", ""),
                "currency": c.get("currency", "USDT"),
                "equity": _decimal_safe(equity),
                "equity_usd": _decimal_safe(c.get("current_equity_usd", equity)),
                "effective_hwm": _decimal_safe(c.get("effective_hwm", 0)),
                "new_watermark": _decimal_safe(equity),
                "deposits_since_hwm": _decimal_safe(c.get("deposits_since_hwm", 0)),
                "pnl": _decimal_safe(c.get("pnl_above_odum_hwm", 0)),
                "odum_fee": _decimal_safe(c.get("odum_fee", 0)),
                "trader_fee": _decimal_safe(c.get("trader_fee_gross", 0)),
                "introducer_fee": _decimal_safe(c.get("introducer_fee", 0)),
                "status": "AT_HWM",
            }
        )

    for c in underwater:
        equity = c.get("current_equity", Decimal("0"))
        currency = c.get("currency", "USDT")
        recovery = c.get("recovery_required", Decimal("0"))
        clients.append(
            {
                "client_id": c["client_id"],
                "full_name": c.get("full_name", ""),
                "currency": currency,
                "equity": _decimal_safe(equity),
                "equity_usd": _decimal_safe(c.get("current_equity_usd", equity)),
                "effective_hwm": _decimal_safe(c.get("odum_hwm", 0)),
                "recovery_required": _decimal_safe(recovery),
                "trader_credits": _decimal_safe(c.get("trader_credits", 0)),
                "server_cost": _decimal_safe(c.get("server_cost", 0)),
                "status": "UNDERWATER",
            }
        )

    # Invoices paid
    all_invoices = _state_mgr.get_invoices()
    paid_invoices = [inv for inv in all_invoices if inv.get("status") == "PAID"]

    # Introducer summary
    intro_summary: list[dict[str, object]] = []
    for client_id in ("PR", "ET"):
        intro = _state_mgr.get_introducer_state(client_id)
        if intro:
            fees = _state_mgr.compute_current_fees(client_id)
            new_odum = float(fees.get("odum_fee", 0)) if fees else 0
            cumulative = float(intro.get("cumulative_odum_invoiced", 0))
            rate = float(intro.get("rate_pct", 0))
            paid = float(intro.get("paid", 0))
            total_owed = (cumulative + new_odum) * rate
            intro_summary.append(
                {
                    "client_id": client_id,
                    "introducer_name": intro.get("introducer_name", ""),
                    "rate_pct": rate,
                    "cumulative_odum_invoiced": cumulative,
                    "new_odum_fee": new_odum,
                    "total_owed": round(total_owed, 2),
                    "paid": paid,
                    "balance_due": round(total_owed - paid, 2),
                }
            )

    return _decimal_safe(
        {
            "clients": clients,
            "invoices_paid": paid_invoices,
            "introducers": intro_summary,
        }
    )


@router.get("/portal/trader")
def portal_trader() -> dict[str, object]:
    """Trader view — sees PnL per client and their fee (10%, becoming 20%).

    Only shows clients above HWM where there's a fee due.
    No underwater details, no Odum fees, no introducer info.
    """
    summary = _state_mgr.get_dashboard_summary()
    at_hwm = summary.get("at_hwm", [])
    underwater = summary.get("underwater", [])

    performance_fees: list[dict[str, object]] = []
    total_perf = Decimal("0")
    for c in at_hwm:
        pnl = c.get("pnl", c.get("pnl_above_trader_hwm", Decimal("0")))
        gross_fee = c.get("trader_fee_gross", Decimal("0"))
        already_paid = c.get("trader_already_paid", Decimal("0"))
        net_fee = c.get("trader_fee_net", Decimal("0"))
        equity = c.get("current_equity", Decimal("0"))
        if isinstance(pnl, Decimal) and pnl > 0:
            performance_fees.append(
                {
                    "client_id": c["client_id"],
                    "currency": c.get("currency", "USDT"),
                    "equity": _decimal_safe(equity),
                    "equity_usd": _decimal_safe(c.get("current_equity_usd", equity)),
                    "effective_hwm": _decimal_safe(c.get("effective_hwm", 0)),
                    "new_watermark": _decimal_safe(equity),
                    "pnl": _decimal_safe(pnl),
                    "fee_pct": 0.10,
                    "gross_fee": _decimal_safe(gross_fee),
                    "already_paid": _decimal_safe(already_paid),
                    "credit_applied": _decimal_safe(c.get("credit_applied", 0)),
                    "net_fee": _decimal_safe(net_fee),
                }
            )
            if isinstance(net_fee, Decimal):
                total_perf += net_fee

    server_count = len(underwater)

    return _decimal_safe(
        {
            "performance_fees": performance_fees,
            "total_performance_fees": total_perf,
            "server_cost_accounts": server_count,
            "server_cost_per_account": Decimal("50"),
            "total_server_costs": Decimal("50") * server_count,
            "total_due": total_perf + Decimal("50") * server_count,
        }
    )


@router.get("/portal/introducer/{introducer_id}")
def portal_introducer(introducer_id: str) -> dict[str, object]:
    """Introducer view — sees client PnL and their share of the performance fee.

    Only shows clients they introduced. No trader fees, no underwater, no Odum internals.
    """
    # Find which clients this introducer is linked to
    linked_clients: list[dict[str, object]] = []

    for client_id in ("PR", "ET", "NN", "STD"):
        intro = _state_mgr.get_introducer_state(client_id)
        if intro is None or str(intro.get("introducer_id", "")) != introducer_id:
            continue

        fees = _state_mgr.compute_current_fees(client_id)
        if fees is None:
            continue

        rate = float(intro.get("rate_pct", 0))
        odum_fee = float(fees.get("odum_fee", 0))
        cumulative = float(intro.get("cumulative_odum_invoiced", 0))
        paid = float(intro.get("paid", 0))
        total_owed = (cumulative + odum_fee) * rate
        equity = fees.get("current_equity", Decimal("0"))

        linked_clients.append(
            {
                "client_id": client_id,
                "full_name": fees.get("full_name", ""),
                "currency": fees.get("currency", "USDT"),
                "equity": _decimal_safe(equity),
                "equity_usd": _decimal_safe(fees.get("current_equity_usd", equity)),
                "effective_hwm": _decimal_safe(fees.get("effective_hwm", 0)),
                "new_watermark": _decimal_safe(equity),
                "pnl": _decimal_safe(fees.get("pnl_above_odum_hwm", 0)),
                "odum_fee": odum_fee,
                "introducer_rate_pct": rate,
                "introducer_fee": round(odum_fee * rate, 2),
                "cumulative_odum_invoiced": cumulative,
                "total_introducer_owed": round(total_owed, 2),
                "already_paid": paid,
                "balance_due": round(total_owed - paid, 2),
            }
        )

    introducer_name = ""
    if linked_clients:
        # Get name from first linked client's intro state
        first_client = str(linked_clients[0].get("client_id", ""))
        intro_state = _state_mgr.get_introducer_state(first_client)
        if intro_state:
            introducer_name = str(intro_state.get("introducer_name", ""))

    total_due = sum(float(c.get("balance_due", 0)) for c in linked_clients)

    return _decimal_safe(
        {
            "introducer_id": introducer_id,
            "introducer_name": introducer_name,
            "clients": linked_clients,
            "total_balance_due": round(total_due, 2),
        }
    )


# ── Invoice HTML viewing & management ──


@router.get("/view/{invoice_id}", response_class=HTMLResponse)
def view_invoice_html(invoice_id: str) -> HTMLResponse:
    """Serve a generated invoice as HTML (viewable in browser, printable to PDF).

    Generates all invoices on first access, then serves from disk cache.
    """
    path = get_invoice_html_path(invoice_id)
    if path is None:
        # Generate all invoices and try again
        generate_all_invoices()
        path = get_invoice_html_path(invoice_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@router.get("/all")
def list_all_invoices(
    client_id: str | None = Query(None, description="Filter by client"),
    invoice_type: str | None = Query(None, description="Filter: odum, trader, introducer"),
    status: str | None = Query(None, description="Filter: ISSUED, PAID, VOIDED"),
) -> list[dict[str, object]]:
    """List all 2026 invoices with optional filters.

    Returns metadata for each invoice (no HTML content).
    Filterable by client_id, type (odum/trader/introducer), status.
    """
    all_invoices = get_all_2026_invoices()

    results: list[dict[str, object]] = []
    for inv in all_invoices:
        # Determine type from invoice_id prefix
        inv_type = "odum"
        if inv.invoice_id.startswith("TRD-"):
            inv_type = "trader"
        elif inv.invoice_id.startswith("INT-"):
            inv_type = "introducer"

        if client_id and inv.client_id != client_id.upper():
            continue
        if invoice_type and inv_type != invoice_type:
            continue
        if status and inv.status != status.upper():
            continue

        results.append(
            {
                "invoice_id": inv.invoice_id,
                "client_id": inv.client_id,
                "client_name": inv.client_name,
                "type": inv_type,
                "invoice_date": inv.invoice_date,
                "due_date": inv.due_date,
                "status": inv.status,
                "total_due": float(inv.total_due),
                "currency": inv.currency,
                "profit": float(inv.profit),
                "fee_rate_pct": float(inv.fee_rate_pct),
                "previous_hwm": float(inv.previous_hwm),
                "current_balance": float(inv.current_balance),
                "view_url": f"/api/v1/invoices/view/{inv.invoice_id}",
            }
        )

    return results


@router.post("/generate-all")
def regenerate_all_invoices() -> dict[str, object]:
    """Regenerate all invoice HTML files from current data.

    Call this after updating invoice status or adding new invoices.
    """
    paths = generate_all_invoices()
    return {
        "generated": len(paths),
        "invoices": [p.split("/")[-1] for p in paths],
    }


# ── PnL Charts ──


@router.get("/charts/{client_id}", response_class=HTMLResponse)
def view_pnl_chart(client_id: str) -> HTMLResponse:
    """Serve interactive PnL chart for a client.

    Shows trading PnL (equity minus transfers), equity curve,
    and transfer log. Chart.js powered — interactive tooltips.
    """
    from pathlib import Path

    chart_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data"
        / "charts"
        / f"{client_id.upper()}_pnl.html"
    )
    if not chart_path.exists():
        result = generate_pnl_chart(client_id.upper())
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {client_id}")
        chart_path = Path(result)
    return HTMLResponse(content=chart_path.read_text(encoding="utf-8"))


@router.get("/charts")
def list_charts() -> list[dict[str, str]]:
    """List available PnL charts for all clients."""
    from pathlib import Path

    charts_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "charts"
    if not charts_dir.exists():
        generate_all_charts()
    results: list[dict[str, str]] = []
    for client_id in ("PR", "NN", "ET", "STD", "GP", "SL", "SL2", "ANU", "IK", "ODUM_PROP"):
        chart_path = charts_dir / f"{client_id}_pnl.html"
        if chart_path.exists():
            results.append(
                {
                    "client_id": client_id,
                    "view_url": f"/api/v1/invoices/charts/{client_id}",
                }
            )
    return results


# ── Dashboard endpoints ─────────────────────────────────────────────────


@router.get("/dashboards/{client_id}", response_class=HTMLResponse)
def view_dashboard(client_id: str) -> HTMLResponse:
    """Serve comprehensive performance dashboard for a client.

    Includes: zoomable equity/PnL charts, daily/monthly bars, coin breakdown,
    order browser, volume chart, all stats (Sharpe, Calmar, Sortino, DD, etc.).
    """
    from pathlib import Path

    cid = client_id.upper()
    dash_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data"
        / "dashboards"
        / f"{cid}_dashboard.html"
    )
    if not dash_path.exists():
        result = generate_dashboard(cid)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {cid}")
        dash_path = Path(result)
    return HTMLResponse(content=dash_path.read_text(encoding="utf-8"))


@router.get("/dashboards")
def list_dashboards() -> list[dict[str, str]]:
    """List available performance dashboards."""
    from pathlib import Path

    dash_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "dashboards"
    results: list[dict[str, str]] = []
    for cid in CLIENT_IDS:
        dash_path = dash_dir / f"{cid}_dashboard.html"
        if dash_path.exists():
            results.append(
                {
                    "client_id": cid,
                    "dashboard_url": f"/api/v1/invoices/dashboards/{cid}",
                }
            )
    return results


@router.post("/dashboards/generate-all")
def regenerate_all_dashboards() -> dict[str, object]:
    """Regenerate all dashboards from latest data."""
    paths = generate_all_dashboards()
    return {"generated": len(paths), "dashboards": [str(p) for p in paths]}


# ── Trade analytics endpoints ───────────────────────────────────────────


@router.get("/analytics/{client_id}")
def get_trade_analytics(client_id: str) -> dict[str, object]:
    """Get coin-level PnL, volume, holding time breakdown for a client.

    All numbers from real exchange data (bills ledger + trades).
    Validated: sum(coin.net_pnl) == total_net_pnl.
    """
    from dataclasses import asdict

    cid = client_id.upper()
    ta = compute_coin_breakdown(cid)
    return asdict(ta)


@router.get("/analytics")
def get_aggregated_analytics(
    client_ids: str = Query(default="", description="Comma-separated client IDs (empty = all)"),
) -> dict[str, object]:
    """Get aggregated trade analytics across clients."""
    from dataclasses import asdict

    ids = (
        [c.strip().upper() for c in client_ids.split(",") if c.strip()]
        if client_ids
        else CLIENT_IDS
    )
    ta = aggregate_clients(ids)
    return asdict(ta)


@router.get("/performance/{client_id}")
def get_performance_stats(client_id: str) -> dict[str, object]:
    """Get full performance stats for a client.

    Returns: simple/compounded/annualized return, Sharpe, Sortino, Calmar,
    max drawdown (% and duration), win rate, and equity curve metadata.
    """
    cid = client_id.upper()
    ec = get_equity_curve(cid)
    if not ec:
        raise HTTPException(status_code=404, detail=f"No equity data for {cid}")
    return compute_performance_stats(ec)


@router.get("/orders/{client_id}")
def get_orders(
    client_id: str,
    symbol: str = Query(default="", description="Filter by symbol (partial match)"),
    side: str = Query(default="", description="Filter by side (buy/sell)"),
    status: str = Query(default="", description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    """Browse orders for a client with pagination and filters."""
    from pathlib import Path

    cid = client_id.upper()
    orders_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data"
        / "backfill"
        / cid
        / "orders.json"
    )
    if not orders_path.exists():
        return {"orders": [], "total": 0, "limit": limit, "offset": offset}

    with open(orders_path) as f:
        all_orders = json.load(f)

    # Sort newest first
    all_orders.sort(key=lambda o: o.get("timestamp", 0) or 0, reverse=True)

    # Apply filters
    if symbol:
        sym_upper = symbol.upper()
        all_orders = [o for o in all_orders if sym_upper in (o.get("symbol", "") or "").upper()]
    if side:
        all_orders = [o for o in all_orders if o.get("side", "") == side.lower()]
    if status:
        all_orders = [o for o in all_orders if o.get("status", "") == status.lower()]

    total = len(all_orders)
    page = all_orders[offset : offset + limit]
    return {"orders": page, "total": total, "limit": limit, "offset": offset}


# ── Monthly report endpoints ────────────────────────────────────────────


@router.get("/reports/{client_id}/{year}/{month}", response_class=HTMLResponse)
def view_monthly_report(client_id: str, year: int, month: int) -> HTMLResponse:
    """Serve monthly PnL report as printable HTML (PDF-ready via browser print).

    Includes: equity curve, daily PnL bars, coin breakdown, transfers.
    """
    from pathlib import Path

    cid = client_id.upper()
    month_str = f"{year}-{month:02d}"
    report_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "data"
        / "reports"
        / f"{cid}_{month_str}_report.html"
    )
    if not report_path.exists():
        result = generate_monthly_report(cid, year, month)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {cid} {month_str}")
        report_path = Path(result)
    return HTMLResponse(content=report_path.read_text(encoding="utf-8"))


@router.get("/reports/{client_id}")
def list_monthly_reports(client_id: str) -> list[dict[str, str]]:
    """List available monthly reports for a client."""
    from pathlib import Path

    cid = client_id.upper()
    reports_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "reports"
    results: list[dict[str, str]] = []
    if reports_dir.exists():
        for f in sorted(reports_dir.glob(f"{cid}_*_report.html")):
            # Extract month from filename: PR_2025-07_report.html
            parts = f.stem.split("_")
            if len(parts) >= 2:
                month_str = parts[1]
                results.append(
                    {
                        "month": month_str,
                        "report_url": f"/api/v1/invoices/reports/{cid}/{month_str.split('-')[0]}/{int(month_str.split('-')[1])}",
                    }
                )
    return results
