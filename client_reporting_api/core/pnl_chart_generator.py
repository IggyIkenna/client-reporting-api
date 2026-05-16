"""Generate interactive PnL charts for all clients.

For each client, computes:
  - Cumulative transfers (deposits - withdrawals)
  - Trading PnL = equity - initial_equity - cumulative_transfers
  - PnL % = trading_pnl / (initial_equity + cumulative_deposits) * 100

Outputs HTML files with Chart.js to data/charts/{client_id}_pnl.html
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Template

from client_reporting_api.core.backfill_store import (
    _get_equity,
    _get_transfer,
    _is_btc_account,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"
_CHARTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "charts"

CLIENT_NAMES: dict[str, str] = {
    "PR": "Prism Capital",
    "NN": "NamNar",
    "ET": "Eqvilent",
    "STD": "Steady Hash",
    "GP": "GPD Capital",
    "SL": "Shaun Lim (USDT)",
    "SL2": "Shaun Lim (BTC)",
    "ANU": "Anu",
    "IK": "IK Pooled",
    "ODUM_PROP": "Odum Prop",
}

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "pnl_chart.html"
_CHART_TEMPLATE = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))


BTC_ACCOUNTS = {"GP", "SL2", "ANU"}


def _btc_transfer_entry(
    date: str,
    day_transfer: float,
    point: dict[str, float],
    prev_point: dict[str, float],
    cumulative_transfers: float,
) -> dict[str, float | str]:
    """Build a transfer log entry for a BTC-denominated account."""
    prev_btc = float(prev_point.get("btc_balance", 0))
    btc_change = float(point.get("btc_balance", 0)) - prev_btc
    prev_usdt_val = float(prev_point.get("usdt_balance", 0))
    usdt_change_val = float(point.get("usdt_balance", 0)) - prev_usdt_val
    usdt_xfer = usdt_change_val if abs(usdt_change_val) > 500 else 0.0
    return {
        "date": date,
        "amount": round(day_transfer, 6),
        "btc_change": round(btc_change, 6),
        "usdt_change": round(usdt_xfer, 2),
        "cumulative": round(cumulative_transfers, 6),
    }


@dataclass
class _PnLAccumulator:
    """Per-day accumulators for the PnL series walk."""

    dates: list[str] = field(default_factory=list)
    equity_series: list[float] = field(default_factory=list)
    pnl_series: list[float] = field(default_factory=list)
    transfers: list[dict[str, float | str]] = field(default_factory=list)
    twr_equity_curve: list[float] = field(default_factory=lambda: [1.0])
    cumulative_transfers: float = 0.0
    twr_product: float = 1.0


def _walk_pnl_series(curve: list[dict[str, float]], is_btc: bool, initial_equity: float) -> _PnLAccumulator:
    """Walk the equity curve once, building per-day PnL/transfer/TWR series."""
    acc = _PnLAccumulator()
    first_date = str(curve[0].get("date", ""))
    prev_equity_val = initial_equity
    rnd = 6 if is_btc else 2
    for i, point in enumerate(curve):
        date = str(point.get("date", ""))
        equity_val = _get_equity(point, is_btc)
        day_transfer = _get_transfer(point, curve[i - 1], is_btc, first_date) if i > 0 else 0.0
        if day_transfer != 0:
            acc.cumulative_transfers += day_transfer
            if is_btc:
                acc.transfers.append(
                    _btc_transfer_entry(date, day_transfer, point, curve[i - 1], acc.cumulative_transfers)
                )
            else:
                acc.transfers.append(
                    {
                        "date": date,
                        "amount": day_transfer,
                        "cumulative": acc.cumulative_transfers,
                    }
                )
        if i > 0 and prev_equity_val > 0:
            day_return = (equity_val - day_transfer) / prev_equity_val - 1.0
            acc.twr_product *= 1.0 + day_return
            acc.twr_equity_curve.append(acc.twr_equity_curve[-1] * (1.0 + day_return))
        trading_pnl = equity_val - initial_equity - acc.cumulative_transfers
        prev_equity_val = equity_val
        acc.dates.append(date)
        acc.equity_series.append(round(equity_val, rnd))
        acc.pnl_series.append(round(trading_pnl, rnd))
    return acc


def _max_drawdown(twr_curve: list[float]) -> float:
    """Compute the deepest drawdown on a TWR-adjusted equity curve."""
    peak = twr_curve[0]
    max_dd = 0.0
    for eq in twr_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd


def compute_pnl_series(client_id: str) -> dict[str, list[float] | list[str] | float | bool]:
    """Compute trading PnL series for a client.

    For BTC share class accounts (GP, SL2, ANU):
      - Equity = BTC balance + (USDT balance / BTC price) — denominated in BTC
      - Transfers detected as changes in BTC balance that coincide with transfer_usd flags
      - BTC price appreciation is NOT PnL
      - USDT trading PnL shown separately

    For USDT accounts:
      - Equity = equity_usd
      - Transfers = transfer_usd entries

    Returns dict with dates, pnl_series, equity_series, transfers, stats.
    """
    equity_path = _DATA_DIR / client_id / "equity_curve.json"
    if not equity_path.exists():
        return {}
    with open(equity_path) as f:
        curve = json.load(f)
    if not curve:
        return {}

    is_btc = _is_btc_account(curve)
    initial_equity = _get_equity(curve[0], is_btc)
    acc = _walk_pnl_series(curve, is_btc, initial_equity)
    max_dd = _max_drawdown(acc.twr_equity_curve)

    rnd = 6 if is_btc else 2
    result: dict[str, list[float] | list[str] | float | bool] = {
        "dates": acc.dates,
        "pnl_series": acc.pnl_series,
        "equity_series": acc.equity_series,
        "transfers": acc.transfers,
        "starting_equity": round(initial_equity, rnd),
        "current_equity": acc.equity_series[-1] if acc.equity_series else 0,
        "net_deposits": round(acc.cumulative_transfers, rnd),
        "trading_pnl": acc.pnl_series[-1] if acc.pnl_series else 0,
        "pnl_pct": (acc.twr_product - 1.0) * 100,  # compounded TWR
        "max_drawdown_pct": round(max_dd * 100, 2),
        "is_btc": is_btc,
        "usdt_pnl": 0,
    }
    if is_btc:
        last = curve[-1]
        first = curve[0]
        result["usdt_pnl"] = round(float(last.get("usdt_balance", 0)) - float(first.get("usdt_balance", 0)), 2)
    return result


def generate_pnl_chart(client_id: str) -> str | None:
    """Generate PnL chart HTML for a client. Returns file path or None."""
    data = compute_pnl_series(client_id)
    if not data:
        return None

    _CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    client_name = CLIENT_NAMES.get(client_id, client_id)
    is_btc = data.get("is_btc", False)
    html = _CHART_TEMPLATE.render(
        client_name=client_name,
        client_id=client_id,
        unit="₿" if is_btc else "$",
        **data,
    )

    path = _CHARTS_DIR / f"{client_id}_pnl.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def generate_all_charts() -> list[str]:
    """Generate PnL charts for all clients with data."""
    paths: list[str] = []
    for client_id in CLIENT_NAMES:
        path = generate_pnl_chart(client_id)
        if path:
            paths.append(path)
            logger.info("Chart: %s -> %s", client_id, path)
    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _main_logger = logging.getLogger(__name__)
    _generated = generate_all_charts()
    for _p in _generated:
        _main_logger.info("  %s", _p)
    _main_logger.info("Generated %d charts.", len(_generated))
