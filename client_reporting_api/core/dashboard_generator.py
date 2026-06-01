# pyright: reportUnknownLambdaType=false, reportArgumentType=false
"""Generate comprehensive interactive client performance dashboards.

Each dashboard includes:
- Zoomable equity curve + PnL chart (Chart.js + chartjs-plugin-zoom)
- Daily and monthly PnL bar charts
- Performance stats grid (Sharpe, Calmar, Sortino, max DD, win rate, etc.)
- Coin-level PnL + volume breakdown table
- Volume chart (daily bars)
- Capital deployment over time
- Order/trade browser with pagination
- Timeframe selector for all views
- Client dropdown for aggregation

All numbers from real exchange data — no mocks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jinja2 import Template

from client_reporting_api.core.backfill_store import (
    compute_performance_stats,
    get_equity_curve,
)
from client_reporting_api.core.pnl_chart_generator import (
    CLIENT_NAMES,
    compute_pnl_series,
)
from client_reporting_api.core.trade_analytics import (
    CLIENT_IDS,
    compute_coin_breakdown,
)

logger = logging.getLogger(__name__)

_DASHBOARDS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "dashboards"
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"
_ORDERS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"

# ── Template ────────────────────────────────────────────────────────────────

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "dashboard.html"
_DASHBOARD_TEMPLATE = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))


def _compute_daily_pnl(pnl_series: list[float]) -> list[float]:
    """Compute daily PnL changes from cumulative PnL series."""
    if not pnl_series:
        return []
    return [pnl_series[0]] + [round(pnl_series[i] - pnl_series[i - 1], 2) for i in range(1, len(pnl_series))]


def _load_orders(client_id: str) -> list[dict[str, str | float | None]]:
    """Load orders for client, sorted newest first."""
    path = _ORDERS_DIR / client_id / "orders.json"
    if not path.exists():
        return []
    with open(path) as f:
        orders = json.load(f)
    # Sort newest first
    orders.sort(key=lambda o: o.get("timestamp", 0) or 0, reverse=True)
    return orders


def generate_dashboard(client_id: str) -> str | None:
    """Generate full performance dashboard for a client."""
    # PnL data
    pnl_data = compute_pnl_series(client_id)
    if not pnl_data:
        return None

    # Performance stats
    ec = get_equity_curve(client_id)
    stats = compute_performance_stats(ec)

    # Trade analytics
    ta = compute_coin_breakdown(client_id)

    # Orders
    orders = _load_orders(client_id)

    # Daily PnL
    daily_pnl = _compute_daily_pnl(pnl_data["pnl_series"])

    # Client list for dropdown
    all_clients = [(cid, CLIENT_NAMES.get(cid, cid)) for cid in CLIENT_IDS if (_DATA_DIR / cid).exists()]

    # Top 15 coins for chart
    top_coins = ta.coins[:15]

    _DASHBOARDS_DIR.mkdir(parents=True, exist_ok=True)

    html = _DASHBOARD_TEMPLATE.render(
        client_name=CLIENT_NAMES.get(client_id, client_id),
        client_id=client_id,
        strategy="Mean Reversion Grid",
        unit="$",
        all_clients=all_clients,
        # Dates
        start_date=str(stats.get("start_date", "")),
        end_date=str(stats.get("end_date", "")),
        equity_days=stats.get("equity_curve_days", 0),
        # Stats
        starting_equity=pnl_data["starting_equity"],
        current_equity=pnl_data["current_equity"],
        trading_pnl=pnl_data["trading_pnl"],
        simple_return=float(stats.get("simple_return_pct", 0)),
        compounded_return=float(stats.get("total_return_pct", 0)),
        annualized_return=float(stats.get("annualized_return_pct", 0)),
        sharpe=float(stats.get("sharpe_ratio", 0)),
        sortino=float(stats.get("sortino_ratio", 0)),
        calmar=float(stats.get("calmar_ratio", 0)),
        max_dd=float(stats.get("max_drawdown_pct", 0)),
        dd_duration=int(stats.get("max_drawdown_duration_days", 0)),
        win_rate=float(stats.get("win_rate_pct", 0)),
        avg_daily_vol=ta.avg_daily_volume_usd,
        cap_deploy=ta.capital_deployment_ratio,
        avg_hold_hours=ta.avg_holding_hours,
        total_trades=ta.total_trade_count,
        # PnL series
        dates=pnl_data["dates"],
        pnl_series=pnl_data["pnl_series"],
        equity_series=pnl_data["equity_series"],
        # Daily PnL bars
        daily_pnl=daily_pnl,
        daily_pnl_dates=pnl_data["dates"],
        # Monthly PnL bars
        monthly_pnl=[float(m.get("pnl_usd", 0)) for m in ta.monthly_pnl],
        monthly_pnl_months=[str(m.get("month", "")) for m in ta.monthly_pnl],
        # Volume
        daily_volumes=[float(d.get("volume_usd", 0)) for d in ta.daily_volumes],
        daily_vol_dates=[str(d.get("date", "")) for d in ta.daily_volumes],
        # Coin breakdown
        coins=ta.coins,
        coin_labels=[c.symbol for c in top_coins],
        coin_pnl_values=[c.net_pnl for c in top_coins],
        total_realized=ta.total_realized_pnl,
        total_fees=ta.total_trading_fees,
        total_funding=ta.total_funding_pnl,
        total_net_pnl=ta.total_net_pnl,
        total_volume=ta.total_volume_usd,
        total_round_trips=ta.total_round_trips,
        coin_pnl_sum=round(sum(c.net_pnl for c in ta.coins), 2),
        # Transfers
        transfers=pnl_data.get("transfers", []),
        # Orders (as JSON for JS)
        orders_json=orders[:5000],  # cap at 5K for browser performance
    )

    path = _DASHBOARDS_DIR / f"{client_id}_dashboard.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def generate_all_dashboards() -> list[str]:
    """Generate dashboards for all clients with data."""
    paths: list[str] = []
    for client_id in CLIENT_IDS:
        if (_DATA_DIR / client_id).exists():
            path = generate_dashboard(client_id)
            if path:
                paths.append(path)
                logger.info("Dashboard: %s -> %s", client_id, path)
    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _main_logger = logging.getLogger(__name__)
    _generated = generate_all_dashboards()
    for _p in _generated:
        _main_logger.info("  %s", _p)
    _main_logger.info("Generated %d dashboards.", len(_generated))
