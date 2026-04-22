"""Manual data entry endpoints for fund-of-fund clients.

Fund-of-fund clients (e.g. YOAV, GUY_ASRAF) have no exchange API keys,
so their performance data must be entered manually. This module provides
CRUD endpoints for manual equity snapshots and returns.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.entitlement import (
    _enforce_entitlement,
    require_internal,
)
from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/manual-entry", tags=["manual-entry"])

_cloud_cfg = UnifiedCloudConfig()
_store = get_store()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


class ManualSnapshotRequest(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema
    client_id: str
    date: str  # "YYYY-MM-DD"
    equity_btc: float
    equity_usd: float
    notes: str = ""


class ManualReturnRequest(
    BaseModel,
):  # CORRECT-LOCAL: FastAPI route schema
    client_id: str
    year: int
    month: int
    return_pct: float
    pnl_usd: float
    opening_equity: float
    closing_equity: float
    notes: str = ""


@router.post("/snapshot")
def create_manual_snapshot(
    request: ManualSnapshotRequest,
    auth: AuthDep,
) -> dict[str, object]:
    """Record a manual equity snapshot for a fund-of-fund client.

    Manual injection is an ops action (runs on behalf of fund-of-fund
    clients who have no exchange API keys) — internal-only.
    """
    require_internal(auth)
    snapshot_id = f"snap-{uuid.uuid4().hex[:8]}"
    record: dict[str, object] = {
        "id": snapshot_id,
        "client_id": request.client_id,
        "date": request.date,
        "equity_btc": request.equity_btc,
        "equity_usd": request.equity_usd,
        "notes": request.notes,
        "source": "manual",
    }
    _store.create("manual_snapshots", record)
    return record


@router.get("/snapshots")
def list_manual_snapshots(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> list[dict[str, object]]:
    """List manual equity snapshots for a client."""
    _enforce_entitlement(auth, client_id)
    all_snaps = _store.list("manual_snapshots")
    return [s for s in all_snaps if s.get("client_id") == client_id]


@router.post("/return")
def create_manual_return(
    request: ManualReturnRequest,
    auth: AuthDep,
) -> dict[str, object]:
    """Record a manual monthly return for a fund-of-fund client.

    Manual injection is an ops action — internal-only.
    """
    require_internal(auth)
    return_id = f"ret-{uuid.uuid4().hex[:8]}"
    record: dict[str, object] = {
        "id": return_id,
        "client_id": request.client_id,
        "year": request.year,
        "month": request.month,
        "return_pct": request.return_pct,
        "pnl_usd": request.pnl_usd,
        "opening_equity": request.opening_equity,
        "closing_equity": request.closing_equity,
        "notes": request.notes,
        "source": "manual",
    }
    _store.create("manual_returns", record)
    return record


@router.get("/returns")
def list_manual_returns(
    auth: AuthDep,
    client_id: str = Query(..., description="Client identifier"),
) -> list[dict[str, object]]:
    """List manual monthly returns for a client."""
    _enforce_entitlement(auth, client_id)
    all_returns = _store.list("manual_returns")
    return [r for r in all_returns if r.get("client_id") == client_id]
