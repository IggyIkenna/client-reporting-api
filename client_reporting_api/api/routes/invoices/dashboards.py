"""Fee dashboard, HWM, and introducer endpoints.

All endpoints read from InvoiceStateManager (hardcoded seed from mr_report
overlaid with live equity from GCS backfill data). Decimals are converted
to floats via ``decimal_safe`` for JSON serialisation.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.invoices._shared import decimal_safe, state_mgr
from client_reporting_api.core.entitlement import (
    _enforce_entitlement,  # pyright: ignore[reportPrivateUsage]
    require_internal,
)

router = APIRouter(prefix="/dashboard")

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


@router.get("/fees")
def fee_dashboard(auth: AuthDep) -> dict[str, object]:
    """Full fee dashboard: all clients grouped by status with current fees.

    Cross-client aggregate — internal-only.
    """
    require_internal(auth)
    summary = state_mgr.get_dashboard_summary()
    return decimal_safe(summary)


@router.get("/fees/{client_id}")
def client_fee_detail(client_id: str, auth: AuthDep) -> dict[str, object]:
    """Detailed fee breakdown for a single client."""
    _enforce_entitlement(auth, client_id)
    fees = state_mgr.compute_current_fees(client_id.upper())
    if fees is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")

    invoices = state_mgr.get_invoices(client_id.upper())
    payments = state_mgr.get_trader_payments(client_id.upper())
    introducer = state_mgr.get_introducer_state(client_id.upper())

    result: dict[str, object] = {
        "fees": decimal_safe(fees),
        "invoices": decimal_safe(invoices),
        "trader_payments": decimal_safe(payments),
    }
    if introducer:
        result["introducer"] = decimal_safe(introducer)
    return result


def _performance_fee_rows(
    at_hwm: Sequence[dict[str, object]],
) -> tuple[list[dict[str, object]], Decimal]:
    """Project at-HWM entries to trader-fee rows; return (rows, total_fee)."""
    rows: list[dict[str, object]] = []
    total = Decimal("0")
    for c in at_hwm:
        net_fee = c.get("trader_fee_net", Decimal("0"))
        if isinstance(net_fee, Decimal) and net_fee > 0:
            rows.append(
                {
                    "client_id": c["client_id"],
                    "current_equity": decimal_safe(c.get("current_equity", 0)),
                    "trader_hwm": decimal_safe(c.get("trader_hwm", 0)),
                    "pnl_above_hwm": decimal_safe(c.get("pnl_above_trader_hwm", 0)),
                    "gross_fee": decimal_safe(c.get("trader_fee_gross", 0)),
                    "credit_applied": decimal_safe(c.get("credit_applied", 0)),
                    "net_fee": decimal_safe(net_fee),
                }
            )
            total += net_fee
    return rows, total


@router.get("/trader-payment")
def trader_payment_summary(auth: AuthDep) -> dict[str, object]:
    """Generate trader payment summary: at-HWM clients + underwater server costs.

    Cross-client operational aggregate — internal-only.
    """
    require_internal(auth)
    summary = state_mgr.get_dashboard_summary()
    at_hwm = summary.get("at_hwm", [])
    underwater = summary.get("underwater", [])

    performance_fees, total_perf = _performance_fee_rows(cast(Sequence[dict[str, object]], at_hwm))

    server_costs_count = len(underwater)
    server_costs_total = Decimal("50") * server_costs_count

    result = decimal_safe(
        {
            "performance_fees": performance_fees,
            "total_performance_fees": total_perf,
            "underwater_accounts": [{"client_id": c["client_id"], "server_cost": Decimal("50")} for c in underwater],
            "total_server_costs": server_costs_total,
            "total_due": total_perf + server_costs_total,
            "total_trader_credits_outstanding": state_mgr.get_total_trader_credits(),
        }
    )
    return cast(dict[str, object], result)


@router.get("/hwm/{client_id}")
def get_hwm_state(client_id: str, auth: AuthDep) -> dict[str, object]:
    """Get current HWM state for a client."""
    _enforce_entitlement(auth, client_id)
    state = state_mgr.get_hwm_state(client_id.upper())
    if state is None:
        raise HTTPException(status_code=404, detail=f"No HWM state for {client_id}")
    return decimal_safe(state.model_dump())


@router.get("/introducers")
def list_introducers(auth: AuthDep) -> dict[str, object]:
    """List all introducer balances.

    Cross-client operational view — internal-only.
    """
    require_internal(auth)
    result: dict[str, object] = {}
    for client_id in ("PR", "ET"):
        intro = state_mgr.get_introducer_state(client_id)
        if intro:
            result[client_id] = decimal_safe(intro)
    return result
