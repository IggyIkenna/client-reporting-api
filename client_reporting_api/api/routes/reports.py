"""POST /api/reports/generate — generate a PnL report for a client."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from unified_trading_library import AuthContext, UnifiedCloudConfig, create_api_auth

from client_reporting_api.core.entitlement import (
    _enforce_entitlement,
    require_internal,
)
from client_reporting_api.core.pnl_reader import generate_pnl_report
from client_reporting_api.mock_data import MOCK_GENERATE_RESPONSE
from client_reporting_api.mock_state import get_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])

_cloud_cfg = UnifiedCloudConfig()
_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]


class GenerateReportRequest(BaseModel):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    client_id: str
    period_month: str  # "YYYY-MM"


@router.get("")
def list_reports(auth: AuthDep) -> list[dict[str, object]]:
    """List available reports.

    Cross-client listing — internal-only. External callers should use the
    per-client endpoints (``/api/v1/pnl``, ``/api/v1/performance/summary``)
    that apply ``_enforce_entitlement``.

    In mock mode returns seed + mutated reports (performance, risk, compliance types).
    In live mode reads from GCS report metadata.
    """
    require_internal(auth)
    if _cloud_cfg.is_mock_mode():
        return get_store().list("reports")
    # GCS_READER stub — full implementation reads report metadata from GCS
    return []


@router.post("/generate")
def generate_report(request: GenerateReportRequest, auth: AuthDep) -> dict[str, object]:
    """Generate a PnL attribution report for a client/period.

    Reads Parquet files from GCS at pnl/{period_month}/{client_id}/ and
    returns a structured report payload.
    """
    _enforce_entitlement(auth, request.client_id)
    if _cloud_cfg.is_mock_mode():
        new_report: dict[str, object] = {
            **MOCK_GENERATE_RESPONSE,
            "client_id": request.client_id,
            "period_month": request.period_month,
            "report_id": f"RPT-{uuid.uuid4().hex[:8].upper()}",
        }
        new_report["id"] = new_report["report_id"]
        get_store().create("reports", new_report)
        return new_report
    logger.info(
        "generate_report: client_id=%s period_month=%s",
        request.client_id,
        request.period_month,
    )
    try:
        return generate_pnl_report(
            client_id=request.client_id,
            period_month=request.period_month,
        )
    except Exception as exc:
        logger.exception("Failed to generate report: %s", exc)
        raise HTTPException(status_code=500, detail="Report generation failed") from exc
