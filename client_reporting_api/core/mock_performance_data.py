"""Realistic mock data for performance dashboard, trade history, and coin breakdowns.

Used when CLOUD_MOCK_MODE=true. Provides TradeLink-quality sample data
for all new endpoints (performance summary, equity curve, trades, positions).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

# -- Mock clients list --
MOCK_CLIENTS: list[dict[str, str | bool]] = [
    {
        "id": "PR",
        "name": "Prism Capital",
        "venue": "okx",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "ET",
        "name": "Eqvilent",
        "venue": "binance",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "STD",
        "name": "Steady Hash",
        "venue": "okx",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "NN",
        "name": "Namnar",
        "venue": "okx",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "ODUM_PROP",
        "name": "Odum Prop (Flagship)",
        "venue": "binance",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "GP",
        "name": "GPD Capital",
        "venue": "okx",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": True,
    },
    {
        "id": "SL",
        "name": "Shaun Lim",
        "venue": "okx",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": True,
    },
    {
        "id": "IK",
        "name": "IK Pooled",
        "venue": "okx",
        "currency": "USDT",
        "tranche": "managed",
        "is_active": True,
        "is_underwater": True,
    },
    {
        "id": "YOAV",
        "name": "Yoav",
        "venue": "n/a",
        "currency": "BTC",
        "tranche": "fund_of_fund",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "GUY_ASRAF",
        "name": "Guy Asraf",
        "venue": "n/a",
        "currency": "BTC",
        "tranche": "fund_of_fund",
        "is_active": True,
        "is_underwater": False,
    },
    {
        "id": "demo-internal",
        "name": "Demo Client (May-23 Cutover)",
        "venue": "bybit,hyperliquid",
        "currency": "USDT",
        "tranche": "demo",
        "is_active": True,
        "is_underwater": False,
    },
]


def _generate_equity_curve(
    start_equity: float, months: int, monthly_return_avg: float
) -> list[dict[str, str | float]]:
    """Generate realistic equity curve data points (daily)."""
    random.seed(42)
    points: list[dict[str, str | float]] = []
    equity = start_equity
    hwm = start_equity
    base = datetime(2025, 7, 10, tzinfo=UTC)

    for day in range(months * 30):
        daily_return = (monthly_return_avg / 30) + random.gauss(0, 0.005)
        equity *= 1 + daily_return
        hwm = max(hwm, equity)
        dd = (equity - hwm) / hwm if hwm > 0 else 0.0

        dt = base + timedelta(days=day)
        points.append(
            {
                "timestamp": dt.isoformat(),
                "equity_usd": round(equity, 2),
                "hwm_usd": round(hwm, 2),
                "drawdown_pct": round(dd, 4),
            }
        )
    return points


def _generate_monthly_returns(months: int) -> list[dict[str, int | float]]:
    """Generate realistic monthly return records."""
    random.seed(42)
    records: list[dict[str, int | float]] = []
    equity = 300000.0
    base_month = 7
    base_year = 2025

    for i in range(months):
        month = ((base_month - 1 + i) % 12) + 1
        year = base_year + ((base_month - 1 + i) // 12)
        ret = 0.02 + random.gauss(0, 0.03)
        opening = equity
        closing = equity * (1 + ret)
        pnl = closing - opening
        equity = closing

        records.append(
            {
                "year": year,
                "month": month,
                "return_pct": round(ret, 4),
                "pnl_usd": round(pnl, 2),
                "opening_equity": round(opening, 2),
                "closing_equity": round(closing, 2),
            }
        )
    return records


def get_mock_performance_summary(client_id: str) -> dict[str, object]:
    """Generate mock PerformanceSummary for a client."""
    # Use different params per client for variety
    params: dict[str, tuple[float, float]] = {
        "PR": (300000, 0.021),
        "ET": (2020000, 0.0085),
        "STD": (512000, 0.0083),
        "NN": (111000, 0.018),
        "ODUM_PROP": (280000, 0.025),
        "GP": (409000, -0.005),
    }
    start, avg_ret = params.get(client_id, (100000, 0.015))

    equity_curve = _generate_equity_curve(start, 9, avg_ret)
    monthly_returns = _generate_monthly_returns(9)
    current_eq = equity_curve[-1]["equity_usd"] if equity_curve else start

    return {
        "client_id": client_id,
        "client_name": next((c["name"] for c in MOCK_CLIENTS if c["id"] == client_id), client_id),
        "venue": next((c["venue"] for c in MOCK_CLIENTS if c["id"] == client_id), "unknown"),
        "currency": "USD",
        "period_label": "all",
        "current_equity_usd": current_eq,
        "current_hwm_usd": max(p["equity_usd"] for p in equity_curve) if equity_curve else start,
        "equity_curve": equity_curve,
        "monthly_returns": monthly_returns,
        "stats": {
            "total_return_pct": 0.2101,
            "annualized_return_pct": 0.2845,
            "sharpe_ratio": 1.87,
            "sortino_ratio": 2.41,
            "max_drawdown_pct": -0.0834,
            "calmar_ratio": 3.41,
            "volatility_pct": 0.152,
            "win_rate": 0.7259,
            "profit_factor": 1.56,
            "avg_win_pct": 0.0031,
            "avg_loss_pct": -0.0055,
            "best_month_pct": 0.0693,
            "worst_month_pct": -0.0234,
            "total_trades": 20200,
            "winning_days": 196,
            "losing_days": 72,
            "tracking_days": 270,
        },
    }


MOCK_POSITIONS: list[dict[str, str | float | None]] = [
    {
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "quantity": 0.45,
        "entry_price": 94200.0,
        "mark_price": 96800.0,
        "unrealized_pnl": 1170.0,
        "leverage": 3.0,
        "liquidation_price": 78500.0,
        "notional_usd": 43560.0,
        "margin_used": 14520.0,
    },
    {
        "symbol": "ETH/USDT:USDT",
        "side": "long",
        "quantity": 5.2,
        "entry_price": 3420.0,
        "mark_price": 3510.0,
        "unrealized_pnl": 468.0,
        "leverage": 2.0,
        "liquidation_price": 2100.0,
        "notional_usd": 18252.0,
        "margin_used": 9126.0,
    },
    {
        "symbol": "SOL/USDT:USDT",
        "side": "short",
        "quantity": 120.0,
        "entry_price": 185.0,
        "mark_price": 178.5,
        "unrealized_pnl": 780.0,
        "leverage": 5.0,
        "liquidation_price": 210.0,
        "notional_usd": 21420.0,
        "margin_used": 4284.0,
    },
]


MOCK_COIN_BREAKDOWN: list[dict[str, str | float | int | None]] = [
    {
        "symbol": "BTC",
        "quantity": 3.45,
        "avg_entry_price": 88200.0,
        "current_price": 96800.0,
        "cost_basis_usd": 304290.0,
        "market_value_usd": 333960.0,
        "realized_pnl": 12450.0,
        "unrealized_pnl": 29670.0,
        "total_pnl": 42120.0,
        "allocation_pct": 0.52,
        "trade_count": 847,
    },
    {
        "symbol": "ETH",
        "quantity": 42.8,
        "avg_entry_price": 3180.0,
        "current_price": 3510.0,
        "cost_basis_usd": 136104.0,
        "market_value_usd": 150228.0,
        "realized_pnl": 5820.0,
        "unrealized_pnl": 14124.0,
        "total_pnl": 19944.0,
        "allocation_pct": 0.23,
        "trade_count": 1234,
    },
    {
        "symbol": "SOL",
        "quantity": 580.0,
        "avg_entry_price": 145.0,
        "current_price": 178.5,
        "cost_basis_usd": 84100.0,
        "market_value_usd": 103530.0,
        "realized_pnl": 3240.0,
        "unrealized_pnl": 19430.0,
        "total_pnl": 22670.0,
        "allocation_pct": 0.16,
        "trade_count": 456,
    },
    {
        "symbol": "USDT",
        "quantity": 58200.0,
        "avg_entry_price": 1.0,
        "current_price": 1.0,
        "cost_basis_usd": 58200.0,
        "market_value_usd": 58200.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
        "allocation_pct": 0.09,
        "trade_count": 0,
    },
]


MOCK_TRADES: list[dict[str, str | float]] = [
    {
        "trade_id": "t-001",
        "venue": "binance",
        "symbol": "BTC/USDT",
        "side": "BUY",
        "quantity": 0.15,
        "price": 95200.0,
        "fee": 4.28,
        "fee_currency": "USDT",
        "realized_pnl": 0.0,
        "timestamp": "2026-04-05T14:23:11+00:00",
        "order_id": "o-8812",
        "trade_type": "LIMIT",
        "notional_usd": 14280.0,
    },
    {
        "trade_id": "t-002",
        "venue": "binance",
        "symbol": "ETH/USDT",
        "side": "SELL",
        "quantity": 2.5,
        "price": 3505.0,
        "fee": 2.63,
        "fee_currency": "USDT",
        "realized_pnl": 312.5,
        "timestamp": "2026-04-05T13:45:22+00:00",
        "order_id": "o-8813",
        "trade_type": "MARKET",
        "notional_usd": 8762.5,
    },
    {
        "trade_id": "t-003",
        "venue": "okx",
        "symbol": "SOL/USDT",
        "side": "BUY",
        "quantity": 50.0,
        "price": 177.8,
        "fee": 2.67,
        "fee_currency": "USDT",
        "realized_pnl": 0.0,
        "timestamp": "2026-04-05T12:10:05+00:00",
        "order_id": "o-8814",
        "trade_type": "LIMIT",
        "notional_usd": 8890.0,
    },
    {
        "trade_id": "t-004",
        "venue": "binance",
        "symbol": "BTC/USDT",
        "side": "SELL",
        "quantity": 0.08,
        "price": 96750.0,
        "fee": 2.32,
        "fee_currency": "USDT",
        "realized_pnl": 204.0,
        "timestamp": "2026-04-05T10:32:18+00:00",
        "order_id": "o-8815",
        "trade_type": "MARKET",
        "notional_usd": 7740.0,
    },
    {
        "trade_id": "t-005",
        "venue": "okx",
        "symbol": "BTC/USDT",
        "side": "BUY",
        "quantity": 0.25,
        "price": 94800.0,
        "fee": 7.11,
        "fee_currency": "USDT",
        "realized_pnl": 0.0,
        "timestamp": "2026-04-04T22:15:44+00:00",
        "order_id": "o-8816",
        "trade_type": "LIMIT",
        "notional_usd": 23700.0,
    },
    {
        "trade_id": "t-006",
        "venue": "binance",
        "symbol": "DOGE/USDT",
        "side": "BUY",
        "quantity": 15000.0,
        "price": 0.1823,
        "fee": 0.82,
        "fee_currency": "USDT",
        "realized_pnl": 0.0,
        "timestamp": "2026-04-04T18:45:30+00:00",
        "order_id": "o-8817",
        "trade_type": "LIMIT",
        "notional_usd": 2734.5,
    },
    {
        "trade_id": "t-007",
        "venue": "okx",
        "symbol": "ETH/USDT",
        "side": "BUY",
        "quantity": 3.0,
        "price": 3385.0,
        "fee": 3.05,
        "fee_currency": "USDT",
        "realized_pnl": 0.0,
        "timestamp": "2026-04-04T15:20:12+00:00",
        "order_id": "o-8818",
        "trade_type": "MARKET",
        "notional_usd": 10155.0,
    },
    {
        "trade_id": "t-008",
        "venue": "binance",
        "symbol": "SOL/USDT",
        "side": "SELL",
        "quantity": 80.0,
        "price": 182.3,
        "fee": 4.38,
        "fee_currency": "USDT",
        "realized_pnl": 560.0,
        "timestamp": "2026-04-04T11:05:33+00:00",
        "order_id": "o-8819",
        "trade_type": "LIMIT",
        "notional_usd": 14584.0,
    },
]


MOCK_BALANCE_BREAKDOWN: dict[str, object] = {
    "total_equity_usd": 645918.0,
    "balances": [
        {
            "currency": "USDT",
            "free": "412000.00",
            "locked": "58200.00",
            "total": "470200.00",
            "usd_value": "470200.00",
        },
        {
            "currency": "BTC",
            "free": "0.85",
            "locked": "0.45",
            "total": "1.30",
            "usd_value": "125840.00",
        },
        {
            "currency": "ETH",
            "free": "8.2",
            "locked": "5.2",
            "total": "13.4",
            "usd_value": "47034.00",
        },
        {
            "currency": "SOL",
            "free": "15.0",
            "locked": "0.0",
            "total": "15.0",
            "usd_value": "2677.50",
        },
        {"currency": "BNB", "free": "1.2", "locked": "0.0", "total": "1.2", "usd_value": "166.50"},
    ],
}
