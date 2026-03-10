"""POST /api/reports/generate — generate a PnL report for a client."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from client_reporting_api.core.pnl_reader import generate_pnl_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateReportRequest(
    BaseModel
):  # CORRECT-LOCAL: FastAPI route schema — not a shared domain contract
    client_id: str
    period_month: str  # "YYYY-MM"


@router.post("/generate")
def generate_report(request: GenerateReportRequest) -> dict[str, object]:
    """Generate a PnL attribution report for a client/period.

    Reads Parquet files from GCS at pnl/{period_month}/{client_id}/ and
    returns a structured report payload.
    """
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
