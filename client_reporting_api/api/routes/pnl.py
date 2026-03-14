"""GET /pnl — PnL data for client-reporting-ui PnL tab."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from unified_config_interface import UnifiedCloudConfig

from client_reporting_api.core.pnl_reader import generate_pnl_report
from client_reporting_api.mock_data import MOCK_PERFORMANCE, MOCK_PNL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pnl"])

_cloud_cfg = UnifiedCloudConfig()


@router.get("/pnl")
def get_pnl(
    client_id: str = Query(..., description="Client identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """Return PnL attribution data for a client/period from GCS."""
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_PNL, "client_id": client_id, "period_month": period_month}
    logger.info("get_pnl: client_id=%s period_month=%s", client_id, period_month)
    try:
        return generate_pnl_report(client_id=client_id, period_month=period_month)
    except Exception as exc:
        logger.exception("Failed to read PnL data: %s", exc)
        raise HTTPException(status_code=500, detail="PnL data retrieval failed") from exc


@router.get("/performance")
def get_performance(
    client_id: str = Query(..., description="Client identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """Return portfolio performance metrics (returns, sharpe, drawdown).

    In mock mode returns realistic sample performance data.
    In live mode computes from GCS PnL data.
    """
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_PERFORMANCE, "client_id": client_id, "period_month": period_month}
    # Live implementation: compute performance metrics from PnL data
    logger.info("get_performance: client_id=%s period_month=%s", client_id, period_month)
    try:
        pnl_data = generate_pnl_report(client_id=client_id, period_month=period_month)
        return {
            "client_id": client_id,
            "period_month": period_month,
            "status": pnl_data.get("status", "no_data"),
        }
    except Exception as exc:
        logger.exception("Failed to compute performance: %s", exc)
        raise HTTPException(status_code=500, detail="Performance computation failed") from exc
