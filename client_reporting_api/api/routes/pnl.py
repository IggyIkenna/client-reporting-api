"""GET /pnl — PnL data for client-reporting-ui PnL tab."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from client_reporting_api.core.pnl_reader import generate_pnl_report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pnl"])


@router.get("/pnl")
def get_pnl(
    client_id: str = Query(..., description="Client identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """Return PnL attribution data for a client/period from GCS."""
    logger.info("get_pnl: client_id=%s period_month=%s", client_id, period_month)
    try:
        return generate_pnl_report(client_id=client_id, period_month=period_month)
    except Exception as exc:
        logger.exception("Failed to read PnL data: %s", exc)
        raise HTTPException(status_code=500, detail="PnL data retrieval failed") from exc
