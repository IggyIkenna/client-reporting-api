"""Invoice state-machine transition endpoint."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.api.routes.invoices._shared import (
    VALID_TRANSITIONS,
    InvoiceTransitionRequest,
    store,
)
from client_reporting_api.core.entitlement import require_internal

logger = logging.getLogger(__name__)

router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_ACTION_TO_STATUS = {
    "issue": "issued",
    "accept": "accepted",
    "dispute": "disputed",
    "pay": "paid",
    "void": "voided",
}


@router.put("/{invoice_id}/transition")
def transition_invoice(
    invoice_id: str,
    request: InvoiceTransitionRequest,
    auth: AuthDep,
) -> dict[str, object]:
    """Transition an invoice through the lifecycle state machine.

    Invoice lifecycle is a billing-ops action — internal-only.
    """
    require_internal(auth)
    inv = store.get("invoices", invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")

    current = str(inv.get("status", "draft")).lower()
    target = request.action.lower()
    new_status = _ACTION_TO_STATUS.get(target)
    if new_status is None:
        raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")

    allowed = VALID_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot transition from {current} to {new_status}. Allowed: {sorted(allowed)}"
            ),
        )

    updated = {**inv, "status": new_status}
    if request.note:
        updated["notes"] = request.note
    if request.payment_txid and new_status == "paid":
        updated["payment_txid"] = request.payment_txid

    store.update("invoices", invoice_id, updated)
    logger.info("Invoice %s transitioned: %s -> %s", invoice_id, current, new_status)
    return updated
