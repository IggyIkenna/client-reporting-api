"""Role-based portal views: admin, trader, introducer."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.invoices._shared import decimal_safe, state_mgr
from client_reporting_api.core.entitlement import require_internal

router = APIRouter(prefix="/portal")

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


def _admin_at_hwm_rows(at_hwm: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project at-HWM clients into the admin portal row shape."""
    rows: list[dict[str, object]] = []
    for c in at_hwm:
        equity = c.get("current_equity", Decimal("0"))
        rows.append(
            {
                "client_id": c["client_id"],
                "full_name": c.get("full_name", ""),
                "currency": c.get("currency", "USDT"),
                "equity": decimal_safe(equity),
                "equity_usd": decimal_safe(c.get("current_equity_usd", equity)),
                "effective_hwm": decimal_safe(c.get("effective_hwm", 0)),
                "new_watermark": decimal_safe(equity),
                "deposits_since_hwm": decimal_safe(c.get("deposits_since_hwm", 0)),
                "pnl": decimal_safe(c.get("pnl_above_odum_hwm", 0)),
                "odum_fee": decimal_safe(c.get("odum_fee", 0)),
                "trader_fee": decimal_safe(c.get("trader_fee_gross", 0)),
                "introducer_fee": decimal_safe(c.get("introducer_fee", 0)),
                "status": "AT_HWM",
            }
        )
    return rows


def _admin_underwater_rows(underwater: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project underwater clients into the admin portal row shape."""
    rows: list[dict[str, object]] = []
    for c in underwater:
        equity = c.get("current_equity", Decimal("0"))
        currency = c.get("currency", "USDT")
        recovery = c.get("recovery_required", Decimal("0"))
        rows.append(
            {
                "client_id": c["client_id"],
                "full_name": c.get("full_name", ""),
                "currency": currency,
                "equity": decimal_safe(equity),
                "equity_usd": decimal_safe(c.get("current_equity_usd", equity)),
                "effective_hwm": decimal_safe(c.get("odum_hwm", 0)),
                "recovery_required": decimal_safe(recovery),
                "trader_credits": decimal_safe(c.get("trader_credits", 0)),
                "server_cost": decimal_safe(c.get("server_cost", 0)),
                "status": "UNDERWATER",
            }
        )
    return rows


def _introducer_summary_rows() -> list[dict[str, object]]:
    """Build the per-client introducer summary used by the admin dashboard."""
    out: list[dict[str, object]] = []
    for client_id in ("PR", "ET"):
        intro = state_mgr.get_introducer_state(client_id)
        if intro is None:
            continue
        fees = state_mgr.compute_current_fees(client_id)
        new_odum = float(fees.get("odum_fee", 0)) if fees else 0
        cumulative = float(intro.get("cumulative_odum_invoiced", 0))
        rate = float(intro.get("rate_pct", 0))
        paid = float(intro.get("paid", 0))
        total_owed = (cumulative + new_odum) * rate
        out.append(
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
    return out


@router.get("/admin")
def portal_admin(auth: AuthDep) -> dict[str, object]:
    """Admin view: full visibility — HWM, PnL, fees, recovery state.

    Internal-only.
    """
    require_internal(auth)
    summary = state_mgr.get_dashboard_summary()
    at_hwm = summary.get("at_hwm", [])
    underwater = summary.get("underwater", [])

    clients = _admin_at_hwm_rows(at_hwm) + _admin_underwater_rows(underwater)

    all_invoices = state_mgr.get_invoices()
    paid_invoices = [inv for inv in all_invoices if inv.get("status") == "PAID"]

    return decimal_safe(
        {
            "clients": clients,
            "invoices_paid": paid_invoices,
            "introducers": _introducer_summary_rows(),
        }
    )


def _trader_fee_row(c: dict[str, object]) -> dict[str, object] | None:
    """Return a trader-portal row for a single at-HWM entry, or None if below HWM."""
    pnl = c.get("pnl", c.get("pnl_above_trader_hwm", Decimal("0")))
    if not isinstance(pnl, Decimal) or pnl <= 0:
        return None
    equity = c.get("current_equity", Decimal("0"))
    return {
        "client_id": c["client_id"],
        "currency": c.get("currency", "USDT"),
        "equity": decimal_safe(equity),
        "equity_usd": decimal_safe(c.get("current_equity_usd", equity)),
        "effective_hwm": decimal_safe(c.get("effective_hwm", 0)),
        "new_watermark": decimal_safe(equity),
        "pnl": decimal_safe(pnl),
        "fee_pct": 0.10,
        "gross_fee": decimal_safe(c.get("trader_fee_gross", Decimal("0"))),
        "already_paid": decimal_safe(c.get("trader_already_paid", Decimal("0"))),
        "credit_applied": decimal_safe(c.get("credit_applied", 0)),
        "net_fee": decimal_safe(c.get("trader_fee_net", Decimal("0"))),
    }


@router.get("/trader")
def portal_trader(auth: AuthDep) -> dict[str, object]:
    """Trader view: PnL per client + 10% fee. No Odum fees, no introducer info.

    Internal-only (trader-portal is a support view accessed via admin creds).
    """
    require_internal(auth)
    summary = state_mgr.get_dashboard_summary()
    at_hwm = summary.get("at_hwm", [])
    underwater = summary.get("underwater", [])

    performance_fees: list[dict[str, object]] = []
    total_perf = Decimal("0")
    for c in at_hwm:
        row = _trader_fee_row(c)
        if row is None:
            continue
        performance_fees.append(row)
        net_fee = row.get("net_fee")
        if isinstance(net_fee, Decimal):
            total_perf += net_fee

    server_count = len(underwater)
    return decimal_safe(
        {
            "performance_fees": performance_fees,
            "total_performance_fees": total_perf,
            "server_cost_accounts": server_count,
            "server_cost_per_account": Decimal("50"),
            "total_server_costs": Decimal("50") * server_count,
            "total_due": total_perf + Decimal("50") * server_count,
        }
    )


def _introducer_client_row(client_id: str, introducer_id: str) -> dict[str, object] | None:
    """Return one introducer-portal row for (client, introducer) or None if unlinked."""
    intro = state_mgr.get_introducer_state(client_id)
    if intro is None or str(intro.get("introducer_id", "")) != introducer_id:
        return None
    fees = state_mgr.compute_current_fees(client_id)
    if fees is None:
        return None

    rate = float(intro.get("rate_pct", 0))
    odum_fee = float(fees.get("odum_fee", 0))
    cumulative = float(intro.get("cumulative_odum_invoiced", 0))
    paid = float(intro.get("paid", 0))
    total_owed = (cumulative + odum_fee) * rate
    equity = fees.get("current_equity", Decimal("0"))

    return {
        "client_id": client_id,
        "full_name": fees.get("full_name", ""),
        "currency": fees.get("currency", "USDT"),
        "equity": decimal_safe(equity),
        "equity_usd": decimal_safe(fees.get("current_equity_usd", equity)),
        "effective_hwm": decimal_safe(fees.get("effective_hwm", 0)),
        "new_watermark": decimal_safe(equity),
        "pnl": decimal_safe(fees.get("pnl_above_odum_hwm", 0)),
        "odum_fee": odum_fee,
        "introducer_rate_pct": rate,
        "introducer_fee": round(odum_fee * rate, 2),
        "cumulative_odum_invoiced": cumulative,
        "total_introducer_owed": round(total_owed, 2),
        "already_paid": paid,
        "balance_due": round(total_owed - paid, 2),
    }


@router.get("/introducer/{introducer_id}")
def portal_introducer(introducer_id: str, auth: AuthDep) -> dict[str, object]:
    """Introducer view: clients they introduced + their share of the performance fee.

    Internal-only (introducer-portal is a support view accessed via admin creds).
    """
    require_internal(auth)
    linked_clients: list[dict[str, object]] = []
    for client_id in ("PR", "ET", "NN", "STD"):
        row = _introducer_client_row(client_id, introducer_id)
        if row is not None:
            linked_clients.append(row)

    introducer_name = ""
    if linked_clients:
        first_client = str(linked_clients[0].get("client_id", ""))
        intro_state = state_mgr.get_introducer_state(first_client)
        if intro_state:
            introducer_name = str(intro_state.get("introducer_name", ""))

    total_due = sum(float(c.get("balance_due", 0)) for c in linked_clients)
    return decimal_safe(
        {
            "introducer_id": introducer_id,
            "introducer_name": introducer_name,
            "clients": linked_clients,
            "total_balance_due": round(total_due, 2),
        }
    )
