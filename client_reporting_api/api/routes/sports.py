"""Sports betting reporting endpoints — P&L, CLV, venue performance, positions, risk."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from unified_config_interface import UnifiedCloudConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sports", tags=["sports"])

_cloud_cfg = UnifiedCloudConfig()

MOCK_SPORTS_PNL: dict[str, object] = {
    "total_pnl": "12450.30",
    "total_volume": "485000.00",
    "roi_pct": "2.57",
    "total_bets": 3842,
    "win_rate_pct": "54.2",
    "by_venue": [
        {
            "venue": "betfair_ex_uk",
            "pnl": "8200.00",
            "volume": "220000",
            "bets": 1200,
            "roi_pct": "3.73",
        },
        {"venue": "pinnacle", "pnl": "3100.00", "volume": "150000", "bets": 980, "roi_pct": "2.07"},
        {"venue": "smarkets", "pnl": "1800.00", "volume": "65000", "bets": 420, "roi_pct": "2.77"},
        {
            "venue": "draftkings",
            "pnl": "-650.00",
            "volume": "50000",
            "bets": 1242,
            "roi_pct": "-1.30",
        },
    ],
    "by_strategy": [
        {"strategy": "arbitrage", "pnl": "5200.00", "bets": 1500, "roi_pct": "1.80"},
        {"strategy": "clv_betting", "pnl": "4800.00", "bets": 800, "roi_pct": "3.20"},
        {"strategy": "steam_move", "pnl": "1600.00", "bets": 342, "roi_pct": "4.10"},
        {"strategy": "ml_prediction", "pnl": "850.00", "bets": 1200, "roi_pct": "0.95"},
    ],
}

MOCK_CLV_DATA: dict[str, object] = {
    "average_clv_edge_pct": "2.34",
    "total_bets_with_clv": 2800,
    "positive_clv_pct": "62.5",
    "by_venue": [
        {
            "venue": "pinnacle",
            "avg_clv": "1.85",
            "bets": 980,
            "closing_line_source": "pinnacle_close",
        },
        {
            "venue": "betfair_ex_uk",
            "avg_clv": "3.10",
            "bets": 1200,
            "closing_line_source": "betfair_sp",
        },
        {
            "venue": "draftkings",
            "avg_clv": "-0.45",
            "bets": 620,
            "closing_line_source": "pinnacle_close",
        },
    ],
    "by_sport": [
        {"sport": "soccer", "avg_clv": "2.80", "bets": 1400},
        {"sport": "basketball", "avg_clv": "1.90", "bets": 800},
        {"sport": "tennis", "avg_clv": "2.10", "bets": 600},
    ],
    "trend_7d": [
        {"date": "2026-03-09", "avg_clv": "2.10"},
        {"date": "2026-03-10", "avg_clv": "2.30"},
        {"date": "2026-03-11", "avg_clv": "1.95"},
        {"date": "2026-03-12", "avg_clv": "2.50"},
        {"date": "2026-03-13", "avg_clv": "2.80"},
        {"date": "2026-03-14", "avg_clv": "2.15"},
        {"date": "2026-03-15", "avg_clv": "2.34"},
    ],
}

MOCK_VENUE_PERFORMANCE: dict[str, object] = {
    "venues": [
        {
            "venue": "betfair_ex_uk",
            "status": "active",
            "account_state": "active",
            "total_volume": "220000",
            "roi_pct": "3.73",
            "limiting_status": "none",
            "last_bet_placed": "2026-03-15T14:30:00Z",
            "max_accepted_stake": "5000",
        },
        {
            "venue": "pinnacle",
            "status": "active",
            "account_state": "active",
            "total_volume": "150000",
            "roi_pct": "2.07",
            "limiting_status": "none",
            "last_bet_placed": "2026-03-15T14:25:00Z",
            "max_accepted_stake": "10000",
        },
        {
            "venue": "draftkings",
            "status": "active",
            "account_state": "restricted",
            "total_volume": "50000",
            "roi_pct": "-1.30",
            "limiting_status": "stake_limited",
            "last_bet_placed": "2026-03-14T22:00:00Z",
            "max_accepted_stake": "50",
        },
        {
            "venue": "bet365_au",
            "status": "suspended",
            "account_state": "suspended",
            "total_volume": "15000",
            "roi_pct": "5.20",
            "limiting_status": "account_closed",
            "last_bet_placed": "2026-02-28T10:00:00Z",
            "max_accepted_stake": "0",
        },
    ],
    "summary": {
        "total_active": 45,
        "total_restricted": 8,
        "total_suspended": 3,
        "avg_roi_active": "2.45",
        "total_volume_all": "485000",
    },
}

MOCK_POSITIONS: dict[str, object] = {
    "open_positions": [
        {
            "bet_id": "bf-12345",
            "venue": "betfair_ex_uk",
            "market": "soccer_epl/man_city_vs_arsenal",
            "side": "back",
            "selection": "Man City",
            "odds": "2.10",
            "stake": "500.00",
            "potential_pnl": "550.00",
            "status": "matched",
        },
        {
            "bet_id": "sm-67890",
            "venue": "smarkets",
            "market": "basketball_nba/lakers_vs_celtics",
            "side": "lay",
            "selection": "Lakers",
            "odds": "1.85",
            "stake": "300.00",
            "liability": "255.00",
            "status": "matched",
        },
    ],
    "total_exposure": "1805.00",
    "total_potential_pnl": "805.00",
    "pending_settlements": 12,
}

MOCK_RISK: dict[str, object] = {
    "total_liability": "15200.00",
    "max_single_event_exposure": "5000.00",
    "correlated_exposure_pct": "12.5",
    "by_sport": [
        {"sport": "soccer", "liability": "8500.00", "open_bets": 45},
        {"sport": "basketball", "liability": "4200.00", "open_bets": 28},
        {"sport": "tennis", "liability": "2500.00", "open_bets": 15},
    ],
    "by_venue": [
        {
            "venue": "betfair_ex_uk",
            "liability": "6000.00",
            "back_exposure": "4000.00",
            "lay_exposure": "2000.00",
        },
        {
            "venue": "pinnacle",
            "liability": "5200.00",
            "back_exposure": "5200.00",
            "lay_exposure": "0.00",
        },
        {
            "venue": "smarkets",
            "liability": "4000.00",
            "back_exposure": "1500.00",
            "lay_exposure": "2500.00",
        },
    ],
    "limits": {
        "max_total_liability": "50000.00",
        "max_single_event": "10000.00",
        "max_correlated_pct": "25.0",
        "utilization_pct": "30.4",
    },
}


@router.get("/pnl")
def get_sports_pnl(
    client_id: str = Query(..., description="Client identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """Sports P&L breakdown by venue, strategy, and period."""
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_SPORTS_PNL, "client_id": client_id, "period_month": period_month}
    logger.info("get_sports_pnl: client_id=%s period_month=%s", client_id, period_month)
    return {"client_id": client_id, "period_month": period_month, "status": "live_not_configured"}


@router.get("/clv")
def get_sports_clv(
    client_id: str = Query(..., description="Client identifier"),
    period_month: str = Query(..., description="Period in YYYY-MM format"),
) -> dict[str, object]:
    """CLV (Closing Line Value) analysis — edge per venue/strategy."""
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_CLV_DATA, "client_id": client_id, "period_month": period_month}
    logger.info("get_sports_clv: client_id=%s period_month=%s", client_id, period_month)
    return {"client_id": client_id, "period_month": period_month, "status": "live_not_configured"}


@router.get("/venue-performance")
def get_venue_performance(
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Per-venue performance metrics (ROI, volume, limiting status)."""
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_VENUE_PERFORMANCE, "client_id": client_id}
    logger.info("get_venue_performance: client_id=%s", client_id)
    return {"client_id": client_id, "status": "live_not_configured"}


@router.get("/positions")
def get_sports_positions(
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Open sports positions — pending bets, exposure."""
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_POSITIONS, "client_id": client_id}
    logger.info("get_sports_positions: client_id=%s", client_id)
    return {"client_id": client_id, "status": "live_not_configured"}


@router.get("/risk")
def get_sports_risk(
    client_id: str = Query(..., description="Client identifier"),
) -> dict[str, object]:
    """Current sports risk exposure — liability, correlation, limits."""
    if _cloud_cfg.cloud_mock_mode:
        return {**MOCK_RISK, "client_id": client_id}
    logger.info("get_sports_risk: client_id=%s", client_id)
    return {"client_id": client_id, "status": "live_not_configured"}
