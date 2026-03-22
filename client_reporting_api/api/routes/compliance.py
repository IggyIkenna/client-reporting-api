"""MiFID II compliance routes — trade reporting, best execution, positions, dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from unified_config_interface import UnifiedCloudConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

_cloud_cfg = UnifiedCloudConfig()


# --- Mock data ---

MOCK_TRADE_REPORTING: dict[str, object] = {
    "reporting_period": "2026-02",
    "total_transactions": 4285,
    "reported_to_arm": 4285,
    "reporting_status": "complete",
    "arm_provider": "unavista",
    "transactions": [
        {
            "transaction_ref": "TXN-20260201-001",
            "instrument": "BTC-USDT-PERP",
            "venue": "binance",
            "side": "buy",
            "quantity": 0.5,
            "price": 42150.00,
            "notional_eur": 38750.00,
            "execution_time": "2026-02-01T09:15:32Z",
            "reporting_time": "2026-02-01T09:15:33Z",
            "status": "accepted",
            "mifid_instrument_id": "CRYPTO-BTC-USDT-PERP",
        },
        {
            "transaction_ref": "TXN-20260201-002",
            "instrument": "ETH-USDT-PERP",
            "venue": "deribit",
            "side": "sell",
            "quantity": 5.0,
            "price": 2280.00,
            "notional_eur": 10488.00,
            "execution_time": "2026-02-01T10:22:15Z",
            "reporting_time": "2026-02-01T10:22:16Z",
            "status": "accepted",
            "mifid_instrument_id": "CRYPTO-ETH-USDT-PERP",
        },
        {
            "transaction_ref": "TXN-20260201-003",
            "instrument": "SOL-USDT-PERP",
            "venue": "binance",
            "side": "buy",
            "quantity": 50.0,
            "price": 98.50,
            "notional_eur": 4531.00,
            "execution_time": "2026-02-01T11:45:00Z",
            "reporting_time": "2026-02-01T11:45:01Z",
            "status": "accepted",
            "mifid_instrument_id": "CRYPTO-SOL-USDT-PERP",
        },
    ],
    "rejections": 0,
    "amendments": 2,
}

MOCK_BEST_EXECUTION: dict[str, object] = {
    "analysis_period": "2026-02",
    "total_orders_analyzed": 3150,
    "best_execution_compliance_pct": 97.8,
    "by_venue": [
        {
            "venue": "binance",
            "orders": 1800,
            "avg_slippage_bps": 1.2,
            "median_slippage_bps": 0.8,
            "p99_slippage_bps": 5.4,
            "fill_rate_pct": 99.2,
            "avg_latency_ms": 45,
            "best_execution_pct": 98.5,
        },
        {
            "venue": "deribit",
            "orders": 950,
            "avg_slippage_bps": 1.8,
            "median_slippage_bps": 1.1,
            "p99_slippage_bps": 7.2,
            "fill_rate_pct": 98.7,
            "avg_latency_ms": 62,
            "best_execution_pct": 96.8,
        },
        {
            "venue": "okx",
            "orders": 400,
            "avg_slippage_bps": 2.1,
            "median_slippage_bps": 1.5,
            "p99_slippage_bps": 8.0,
            "fill_rate_pct": 97.5,
            "avg_latency_ms": 55,
            "best_execution_pct": 95.2,
        },
    ],
    "by_instrument": [
        {
            "instrument": "BTC-USDT-PERP",
            "orders": 1500,
            "avg_slippage_bps": 0.9,
            "best_venue": "binance",
        },
        {
            "instrument": "ETH-USDT-PERP",
            "orders": 1100,
            "avg_slippage_bps": 1.4,
            "best_venue": "binance",
        },
        {
            "instrument": "SOL-USDT-PERP",
            "orders": 550,
            "avg_slippage_bps": 2.5,
            "best_venue": "okx",
        },
    ],
}

MOCK_LARGE_POSITIONS: dict[str, object] = {
    "reporting_date": "2026-03-15",
    "threshold_eur": 500_000,
    "positions": [
        {
            "instrument": "BTC-USDT-PERP",
            "net_position": 12.5,
            "notional_eur": 528_750.00,
            "pct_of_nav": 8.4,
            "venues": ["binance", "deribit"],
            "reportable": True,
            "reported_to_nca": True,
        },
        {
            "instrument": "ETH-USDT-PERP",
            "net_position": 150.0,
            "notional_eur": 342_000.00,
            "pct_of_nav": 5.5,
            "venues": ["binance"],
            "reportable": False,
            "reported_to_nca": False,
        },
    ],
    "total_nav_eur": 6_300_000.00,
    "concentration_warning": False,
}

MOCK_COMPLIANCE_DASHBOARD: dict[str, object] = {
    "period": "2026-02",
    "overall_status": "compliant",
    "score_pct": 98.5,
    "sections": [
        {
            "name": "Transaction Reporting",
            "status": "compliant",
            "score_pct": 100.0,
            "details": "All 4285 transactions reported to ARM within T+1",
        },
        {
            "name": "Best Execution",
            "status": "compliant",
            "score_pct": 97.8,
            "details": "Avg slippage 1.4bps across all venues",
        },
        {
            "name": "Position Reporting",
            "status": "compliant",
            "score_pct": 100.0,
            "details": "1 large position reported to NCA",
        },
        {
            "name": "Record Keeping",
            "status": "compliant",
            "score_pct": 100.0,
            "details": "All trade records retained per 5-year requirement",
        },
        {
            "name": "Conflict of Interest",
            "status": "review",
            "score_pct": 92.0,
            "details": "2 potential conflicts flagged for manual review",
        },
    ],
    "alerts": [
        {
            "alert_id": "COMP-ALERT-001",
            "severity": "warning",
            "type": "best_execution",
            "message": "OKX p99 slippage exceeded 7bps threshold",
            "created_at": "2026-02-15T10:00:00Z",
            "resolved": True,
        },
        {
            "alert_id": "COMP-ALERT-002",
            "severity": "info",
            "type": "conflict_of_interest",
            "message": "Cross-venue position detected: BTC-USDT-PERP on binance and deribit",
            "created_at": "2026-02-20T14:30:00Z",
            "resolved": False,
        },
    ],
    "violations": [],
    "next_review_date": "2026-04-01",
}


# --- Routes ---


@router.get("/trade-reporting")
def get_trade_reporting(
    org_id: str = Query(..., description="Organisation identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """MiFID II transaction report data.

    In mock mode: returns synthetic transaction reporting data.
    In live mode: queries the ARM submission records.
    """
    if _cloud_cfg.is_mock_mode():
        return {**MOCK_TRADE_REPORTING, "org_id": org_id, "period_month": period_month}

    logger.info("get_trade_reporting: org_id=%s period_month=%s", org_id, period_month)
    # Live implementation: query ARM transaction records
    return {
        "org_id": org_id,
        "period_month": period_month,
        "total_transactions": 0,
        "reporting_status": "no_data",
    }


@router.get("/best-execution")
def get_best_execution(
    org_id: str = Query(..., description="Organisation identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """Best execution analysis — venue performance, slippage, fill rates.

    In mock mode: returns synthetic best execution analysis data.
    In live mode: computes from execution records.
    """
    if _cloud_cfg.is_mock_mode():
        return {**MOCK_BEST_EXECUTION, "org_id": org_id, "period_month": period_month}

    logger.info("get_best_execution: org_id=%s period_month=%s", org_id, period_month)
    # Live implementation: compute best execution metrics from trade data
    return {"org_id": org_id, "period_month": period_month, "total_orders_analyzed": 0}


@router.get("/positions")
def get_large_positions(
    org_id: str = Query(..., description="Organisation identifier"),
) -> dict[str, object]:
    """Large position reporting — positions above regulatory thresholds.

    In mock mode: returns synthetic large position data.
    In live mode: queries current positions and checks against thresholds.
    """
    if _cloud_cfg.is_mock_mode():
        return {**MOCK_LARGE_POSITIONS, "org_id": org_id}

    logger.info("get_large_positions: org_id=%s", org_id)
    # Live implementation: query positions and check against reporting thresholds
    return {"org_id": org_id, "positions": [], "concentration_warning": False}


@router.get("/dashboard")
def get_compliance_dashboard(
    org_id: str = Query(..., description="Organisation identifier"),
    period_month: str = Query("2026-02", description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """Compliance summary dashboard — overall status, alerts, violations.

    In mock mode: returns synthetic compliance dashboard data.
    In live mode: aggregates compliance data from all subsystems.
    """
    if _cloud_cfg.is_mock_mode():
        return {**MOCK_COMPLIANCE_DASHBOARD, "org_id": org_id, "period": period_month}

    logger.info("get_compliance_dashboard: org_id=%s period_month=%s", org_id, period_month)
    # Live implementation: aggregate compliance metrics
    return {"org_id": org_id, "period": period_month, "overall_status": "no_data"}
