"""Mock data for client-reporting-api when CLOUD_MOCK_MODE=true.

Returns realistic sample data for reports, performance, PnL, and report
generation endpoints so the API can run without cloud storage backends.
"""

from __future__ import annotations

MOCK_REPORTS: list[dict[str, str | int | float | bool]] = [
    {
        "report_id": "RPT-2026-001",
        "client_id": "client-alpha",
        "type": "performance",
        "period_month": "2026-02",
        "status": "completed",
        "generated_at": "2026-03-01T08:30:00Z",
        "title": "Monthly Performance Report — February 2026",
    },
    {
        "report_id": "RPT-2026-002",
        "client_id": "client-alpha",
        "type": "risk",
        "period_month": "2026-02",
        "status": "completed",
        "generated_at": "2026-03-01T09:15:00Z",
        "title": "Risk Exposure Report — February 2026",
    },
    {
        "report_id": "RPT-2026-003",
        "client_id": "client-beta",
        "type": "compliance",
        "period_month": "2026-02",
        "status": "completed",
        "generated_at": "2026-03-02T10:00:00Z",
        "title": "Regulatory Compliance Report — February 2026",
    },
    {
        "report_id": "RPT-2026-004",
        "client_id": "client-beta",
        "type": "performance",
        "period_month": "2026-01",
        "status": "completed",
        "generated_at": "2026-02-01T08:45:00Z",
        "title": "Monthly Performance Report — January 2026",
    },
    {
        "report_id": "RPT-2026-005",
        "client_id": "client-gamma",
        "type": "risk",
        "period_month": "2026-02",
        "status": "pending",
        "generated_at": "2026-03-05T14:20:00Z",
        "title": "Risk Exposure Report — February 2026",
    },
]

MOCK_PERFORMANCE: dict[str, str | float | dict[str, float] | list[dict[str, str | float]]] = {
    "client_id": "client-alpha",
    "period_month": "2026-02",
    "total_return_pct": 4.82,
    "annualized_return_pct": 57.84,
    "sharpe_ratio": 2.14,
    "sortino_ratio": 3.07,
    "max_drawdown_pct": -3.21,
    "volatility_pct": 12.45,
    "calmar_ratio": 1.50,
    "win_rate_pct": 62.5,
    "benchmark_return_pct": 2.10,
    "alpha_pct": 2.72,
    "beta": 0.85,
    "strategy_breakdown": [
        {
            "strategy": "momentum-btc-4h",
            "return_pct": 6.12,
            "sharpe": 2.45,
            "allocation_pct": 35.0,
        },
        {
            "strategy": "mean-reversion-eth-1h",
            "return_pct": 3.41,
            "sharpe": 1.89,
            "allocation_pct": 25.0,
        },
        {
            "strategy": "stat-arb-cefi",
            "return_pct": 4.05,
            "sharpe": 2.67,
            "allocation_pct": 40.0,
        },
    ],
}

MOCK_GENERATE_RESPONSE: dict[str, str] = {
    "status": "queued",
    "report_id": "RPT-2026-MOCK-001",
    "client_id": "client-alpha",
    "period_month": "2026-02",
    "estimated_completion": "2026-03-14T12:00:00Z",
    "message": "Report generation queued (mock mode)",
}

MOCK_PNL: dict[str, str | list[dict[str, str | float]]] = {
    "status": "ok",
    "client_id": "client-alpha",
    "period_month": "2026-02",
    "rows": [
        {
            "date": "2026-02-01",
            "instrument": "BTC-USDT-PERP",
            "venue": "binance",
            "strategy": "momentum-btc-4h",
            "realized_pnl": 1245.50,
            "unrealized_pnl": 320.10,
            "fees": -12.30,
            "net_pnl": 1553.30,
            "notional_volume": 125000.00,
        },
        {
            "date": "2026-02-01",
            "instrument": "ETH-USDT-PERP",
            "venue": "binance",
            "strategy": "mean-reversion-eth-1h",
            "realized_pnl": 430.20,
            "unrealized_pnl": -85.40,
            "fees": -8.50,
            "net_pnl": 336.30,
            "notional_volume": 67500.00,
        },
        {
            "date": "2026-02-02",
            "instrument": "BTC-USDT-PERP",
            "venue": "deribit",
            "strategy": "stat-arb-cefi",
            "realized_pnl": 890.75,
            "unrealized_pnl": 150.00,
            "fees": -10.20,
            "net_pnl": 1030.55,
            "notional_volume": 98000.00,
        },
        {
            "date": "2026-02-02",
            "instrument": "SOL-USDT-PERP",
            "venue": "binance",
            "strategy": "momentum-btc-4h",
            "realized_pnl": -210.30,
            "unrealized_pnl": 45.00,
            "fees": -5.80,
            "net_pnl": -171.10,
            "notional_volume": 32000.00,
        },
        {
            "date": "2026-02-03",
            "instrument": "ETH-USDT-PERP",
            "venue": "deribit",
            "strategy": "mean-reversion-eth-1h",
            "realized_pnl": 670.40,
            "unrealized_pnl": 200.25,
            "fees": -9.10,
            "net_pnl": 861.55,
            "notional_volume": 75000.00,
        },
    ],
}
