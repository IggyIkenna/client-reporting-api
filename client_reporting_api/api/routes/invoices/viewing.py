"""HTML view endpoints: invoices, PnL charts, dashboards, monthly reports."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.dashboard_generator import (
    generate_all_dashboards,
    generate_dashboard,
)
from client_reporting_api.core.entitlement import (
    enforce_entitlement,  # pyright: ignore[reportPrivateUsage]
    require_internal,
)
from client_reporting_api.core.invoice_generator import (
    generate_all_invoices,
    get_all_2026_invoices,
    get_invoice_html_path,
)
from client_reporting_api.core.monthly_report_generator import generate_monthly_report
from client_reporting_api.core.pnl_chart_generator import (
    generate_all_charts,
    generate_pnl_chart,
)
from client_reporting_api.core.trade_analytics import CLIENT_IDS

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_DATA_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "data"
_CHARTS_DIR = _DATA_ROOT / "charts"
_DASH_DIR = _DATA_ROOT / "dashboards"
_REPORTS_DIR = _DATA_ROOT / "reports"
_CHART_CLIENT_IDS = ("PR", "NN", "ET", "STD", "GP", "SL", "SL2", "ANU", "IK", "ODUM_PROP")


@router.get("/view/{invoice_id}", response_class=HTMLResponse)
def view_invoice_html(invoice_id: str, auth: AuthDep) -> HTMLResponse:
    """Serve a generated invoice as HTML (viewable in browser, printable to PDF).

    Internal-only — invoice rendering exposes per-client billing data.
    """
    require_internal(auth)
    path = get_invoice_html_path(invoice_id)
    if path is None:
        generate_all_invoices()
        path = get_invoice_html_path(invoice_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


def _invoice_type_for(invoice_id: str) -> str:
    """Classify an invoice by its ID prefix (odum/trader/introducer)."""
    if invoice_id.startswith("TRD-"):
        return "trader"
    if invoice_id.startswith("INT-"):
        return "introducer"
    return "odum"


@router.get("/all")
def list_all_invoices(
    auth: AuthDep,
    client_id: str | None = Query(None, description="Filter by client"),
    invoice_type: str | None = Query(None, description="Filter: odum, trader, introducer"),
    status: str | None = Query(None, description="Filter: ISSUED, PAID, VOIDED"),
) -> list[dict[str, object]]:
    """List all 2026 invoices with optional filters (returns metadata only).

    External callers MUST scope by their own ``client_id`` (matched via
    :func:`enforce_entitlement`). Internal callers may omit / vary it.
    """
    if not auth.is_internal:
        # External: client_id is mandatory and must match caller.
        if client_id is None:
            require_internal(auth)  # raises 403 with the right message
        enforce_entitlement(auth, client_id or "")
    all_invoices = get_all_2026_invoices()

    results: list[dict[str, object]] = []
    for inv in all_invoices:
        inv_type = _invoice_type_for(inv.invoice_id)
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
def regenerate_all_invoices(auth: AuthDep) -> dict[str, object]:
    """Regenerate all invoice HTML files from current data.

    Bulk regeneration is an ops action — internal-only.
    """
    require_internal(auth)
    paths = generate_all_invoices()
    return {
        "generated": len(paths),
        "invoices": [p.split("/")[-1] for p in paths],
    }


@router.get("/charts/{client_id}", response_class=HTMLResponse)
def view_pnl_chart(client_id: str, auth: AuthDep) -> HTMLResponse:
    """Serve interactive PnL chart for a client."""
    enforce_entitlement(auth, client_id)
    chart_path = _CHARTS_DIR / f"{client_id.upper()}_pnl.html"
    if not chart_path.exists():
        result = generate_pnl_chart(client_id.upper())
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {client_id}")
        chart_path = Path(result)
    return HTMLResponse(content=chart_path.read_text(encoding="utf-8"))


@router.get("/charts")
def list_charts(auth: AuthDep) -> list[dict[str, str]]:
    """List available PnL charts for all clients.

    Cross-client listing — internal-only.
    """
    require_internal(auth)
    if not _CHARTS_DIR.exists():
        generate_all_charts()
    results: list[dict[str, str]] = []
    for client_id in _CHART_CLIENT_IDS:
        chart_path = _CHARTS_DIR / f"{client_id}_pnl.html"
        if chart_path.exists():
            results.append({"client_id": client_id, "view_url": f"/api/v1/invoices/charts/{client_id}"})
    return results


@router.get("/dashboards/{client_id}", response_class=HTMLResponse)
def view_dashboard(client_id: str, auth: AuthDep) -> HTMLResponse:
    """Serve the comprehensive performance dashboard for a client."""
    enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    dash_path = _DASH_DIR / f"{cid}_dashboard.html"
    if not dash_path.exists():
        result = generate_dashboard(cid)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {cid}")
        dash_path = Path(result)
    return HTMLResponse(content=dash_path.read_text(encoding="utf-8"))


@router.get("/dashboards")
def list_dashboards(auth: AuthDep) -> list[dict[str, str]]:
    """List available performance dashboards.

    Cross-client listing — internal-only.
    """
    require_internal(auth)
    results: list[dict[str, str]] = []
    for cid in CLIENT_IDS:
        dash_path = _DASH_DIR / f"{cid}_dashboard.html"
        if dash_path.exists():
            results.append({"client_id": cid, "dashboard_url": f"/api/v1/invoices/dashboards/{cid}"})
    return results


@router.post("/dashboards/generate-all")
def regenerate_all_dashboards(auth: AuthDep) -> dict[str, object]:
    """Regenerate all dashboards from latest data.

    Bulk regeneration is an ops action — internal-only.
    """
    require_internal(auth)
    paths = generate_all_dashboards()
    return {"generated": len(paths), "dashboards": [str(p) for p in paths]}


@router.get("/reports/{client_id}/{year}/{month}", response_class=HTMLResponse)
def view_monthly_report(client_id: str, year: int, month: int, auth: AuthDep) -> HTMLResponse:
    """Serve monthly PnL report as printable HTML (PDF-ready via browser print)."""
    enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    month_str = f"{year}-{month:02d}"
    report_path = _REPORTS_DIR / f"{cid}_{month_str}_report.html"
    if not report_path.exists():
        result = generate_monthly_report(cid, year, month)
        if result is None:
            raise HTTPException(status_code=404, detail=f"No data for {cid} {month_str}")
        report_path = Path(result)
    return HTMLResponse(content=report_path.read_text(encoding="utf-8"))


@router.get("/reports/{client_id}")
def list_monthly_reports(client_id: str, auth: AuthDep) -> list[dict[str, str]]:
    """List available monthly reports for a client."""
    enforce_entitlement(auth, client_id)
    cid = client_id.upper()
    results: list[dict[str, str]] = []
    if _REPORTS_DIR.exists():
        for f in sorted(_REPORTS_DIR.glob(f"{cid}_*_report.html")):
            # Filename shape: PR_2025-07_report.html
            parts = f.stem.split("_")
            if len(parts) >= 2:
                month_str = parts[1]
                year_part, month_part = month_str.split("-")
                results.append(
                    {
                        "month": month_str,
                        "report_url": (f"/api/v1/invoices/reports/{cid}/{year_part}/{int(month_part)}"),
                    }
                )
    return results
