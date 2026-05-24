"""Shared state, schemas, and helpers for the invoices route package."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel
from unified_trading_library import UnifiedCloudConfig

from client_reporting_api.core.invoice_state import InvoiceStateManager
from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)

cloud_cfg = UnifiedCloudConfig()
state_mgr = InvoiceStateManager()
store = get_store()


class GenerateInvoiceRequest(BaseModel):  # CORRECT-LOCAL: FastAPI route schema
    """Request body for POST /api/v1/invoices/generate."""

    org_id: str
    period_month: str  # "YYYY-MM"
    invoice_type: str  # "management_fee" | "performance_fee"
    currency: str = "USD"


class InvoiceTransitionRequest(BaseModel):  # CORRECT-LOCAL: FastAPI route schema
    """Request body for PUT /api/v1/invoices/{id}/transition."""

    action: str  # "issue" | "accept" | "dispute" | "pay" | "void"
    note: str = ""
    payment_txid: str | None = None


# Valid invoice state-machine transitions.
# Any → VOIDED is treated as an admin override.
VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"issued", "voided"},
    "issued": {"accepted", "disputed", "voided"},
    "accepted": {"paid", "voided"},
    "disputed": {"issued", "voided"},
}


def decimal_safe(obj: object) -> object:
    """Convert Decimals to floats for JSON serialisation (recursive)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        dict_obj = cast(dict[Any, Any], obj)
        return {k: decimal_safe(v) for k, v in dict_obj.items()}
    if isinstance(obj, list):
        list_obj = cast(list[Any], obj)
        return [decimal_safe(item) for item in list_obj]
    return obj
