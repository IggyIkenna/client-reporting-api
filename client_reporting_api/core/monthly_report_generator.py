"""Generate monthly PnL reports as printable HTML (PDF-ready via browser print).

Each report includes:
- Month header with client details
- Performance summary (return, Sharpe, DD, volume)
- Equity curve chart for the month
- Coin-level PnL breakdown table
- Daily PnL bar chart
- Transfer log
- Footer with Odum branding

Designed for browser PDF export (via window's printing flow) with clean page breaks.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Template

from client_reporting_api.core.backfill_store import get_equity_curve
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES
from client_reporting_api.core.trade_analytics import CLIENT_IDS, _load_json

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"
_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "monthly_report.html"
_REPORT_TEMPLATE = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))


@dataclass
class _MonthEquitySnapshot:
    """Equity-curve slice for a single calendar month."""

    dates: list[str]
    values: list[float]
    transfers_total: float
    transfers_list: list[dict[str, str | float]]
    daily_pnl: list[float]


def _slice_equity_for_month(ec: list[dict[str, str | float]], month_str: str) -> _MonthEquitySnapshot | None:
    """Slice an equity curve to one month and compute transfers + daily PnL."""
    points = [p for p in ec if str(p.get("date", "")).startswith(month_str)]
    if not points:
        return None
    dates = [str(p["date"]) for p in points]
    values = [float(p.get("equity_usd", 0)) for p in points]
    transfers_total = 0.0
    transfers_list: list[dict[str, str | float]] = []
    for p in points:
        t = p.get("transfer_usd")
        if t is not None and str(p.get("date")) != dates[0]:
            transfers_total += float(t)
            transfers_list.append({"date": str(p["date"]), "amount": float(t)})
    daily_pnl: list[float] = [values[0] - values[0]]
    prev = values[0]
    for i, p in enumerate(points):
        if i == 0:
            continue
        t = float(p.get("transfer_usd", 0) or 0) if str(p.get("date")) != dates[0] else 0
        daily_pnl.append(round(values[i] - t - prev, 2))
        prev = values[i]
    return _MonthEquitySnapshot(dates, values, transfers_total, transfers_list, daily_pnl)


def _bill_in_month(b: dict[str, str | float], month_str: str) -> str | None:
    """Return the coin symbol if ``b`` belongs to ``month_str``, else None."""
    ts = b.get("timestamp", 0)
    if not ts:
        return None
    dt = datetime.fromtimestamp(float(ts) / 1000, tz=UTC)
    if dt.strftime("%Y-%m") != month_str:
        return None
    inst_raw = b.get("inst_id", "")
    inst = str(inst_raw) if inst_raw is not None else ""
    sym = inst.split("-")[0] if inst else ""
    return sym or None


def _apply_bill_to_coin(b: dict[str, str | float], slot: dict[str, float]) -> None:
    """Apply a single bill entry to its coin's accumulator slot."""
    change = float(b.get("change", 0))
    st = b.get("sub_type", "")
    if st == "5":
        slot["realized"] += change
    elif st == "3":
        slot["fees"] += change
    elif st in ("173", "174") or b.get("type") == "8":
        slot["funding"] += change


def _aggregate_month_bills(bills: list[dict[str, str | float]], month_str: str) -> dict[str, dict[str, float]]:
    """Aggregate bill ledger entries into per-coin realized/fees/funding sums."""
    coin_data: dict[str, dict[str, float]] = defaultdict(
        lambda: {"realized": 0.0, "fees": 0.0, "funding": 0.0, "volume": 0.0, "trades": 0.0}
    )
    for b in bills:
        sym = _bill_in_month(b, month_str)
        if sym is None:
            continue
        _apply_bill_to_coin(b, coin_data[sym])
    return coin_data


def _add_month_trade_volume(
    coin_data: dict[str, dict[str, float]],
    trades: list[dict[str, str | float | None]],
    month_str: str,
) -> int:
    """Add per-coin trade volume + count for the month, return total trade count."""
    total = 0
    for t in trades:
        dt_raw = t.get("datetime", "")
        dt_str = str(dt_raw) if dt_raw is not None else ""
        if dt_str[:7] != month_str:
            continue
        raw_sym = t.get("symbol", "")
        sym = str(raw_sym).split("/")[0] if raw_sym is not None else ""
        coin_data[sym]["volume"] += float(t.get("cost", 0) or 0)
        coin_data[sym]["trades"] += 1
        total += 1
    return total


def _coin_rows_from_aggregate(
    coin_data: dict[str, dict[str, float]],
) -> list[dict[str, float | int | str]]:
    """Project the coin aggregate into the rendered table rows."""
    coins: list[dict[str, float | int | str]] = []
    for sym in sorted(coin_data.keys()):
        d = coin_data[sym]
        net = d["realized"] + d["fees"] + d["funding"]
        if abs(net) > 0.01 or d["trades"] > 0:
            coins.append(
                {
                    "symbol": sym,
                    "realized": round(d["realized"], 2),
                    "fees": round(d["fees"], 2),
                    "funding": round(d["funding"], 2),
                    "net": round(net, 2),
                    "volume": round(d["volume"], 2),
                    "trades": int(d["trades"]),
                }
            )
    coins.sort(key=lambda c: abs(float(c["net"])), reverse=True)
    return coins


def generate_monthly_report(client_id: str, year: int, month: int) -> str | None:
    """Generate monthly PnL report for a client."""
    month_str = f"{year}-{month:02d}"
    month_label = datetime(year, month, 1).strftime("%B %Y")

    ec = get_equity_curve(client_id)
    if not ec:
        return None
    snap = _slice_equity_for_month(ec, month_str)
    if snap is None:
        return None

    start_eq, end_eq = snap.values[0], snap.values[-1]
    month_pnl = end_eq - start_eq - snap.transfers_total
    month_return = (month_pnl / start_eq * 100) if start_eq > 0 else 0

    bills = _load_json(client_id, "bills_ledger.json") or []
    coin_data = _aggregate_month_bills(bills, month_str)
    trades = _load_json(client_id, "trades.json") or []
    month_trade_count = _add_month_trade_volume(coin_data, trades, month_str)
    coins = _coin_rows_from_aggregate(coin_data)

    total_realized = sum(float(c["realized"]) for c in coins)
    total_fees = sum(float(c["fees"]) for c in coins)
    total_funding = sum(float(c["funding"]) for c in coins)
    total_net = sum(float(c["net"]) for c in coins)
    total_volume = sum(float(c["volume"]) for c in coins)

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html = _REPORT_TEMPLATE.render(
        client_name=CLIENT_NAMES.get(client_id, client_id),
        month_label=month_label,
        start_equity=start_eq,
        end_equity=end_eq,
        month_pnl=month_pnl,
        month_return=month_return,
        month_volume=total_volume,
        month_trades=month_trade_count,
        month_transfers=snap.transfers_total,
        trading_days=len(snap.dates),
        equity_dates=snap.dates,
        equity_values=snap.values,
        daily_pnl_values=snap.daily_pnl,
        coins=coins,
        total_realized=total_realized,
        total_fees=total_fees,
        total_funding=total_funding,
        total_net=total_net,
        total_volume=total_volume,
        total_trades=month_trade_count,
        transfers=snap.transfers_list,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    filename = f"{client_id}_{month_str}_report.html"
    path = _REPORTS_DIR / filename
    path.write_text(html, encoding="utf-8")
    return str(path)


def generate_all_monthly_reports(client_id: str) -> list[str]:
    """Generate all available monthly reports for a client."""
    ec = get_equity_curve(client_id)
    if not ec:
        return []

    # Find all months with data
    months: set[str] = set()
    for p in ec:
        date = str(p.get("date", ""))
        if date:
            months.add(date[:7])

    paths: list[str] = []
    for m in sorted(months):
        year, mon = int(m[:4]), int(m[5:7])
        path = generate_monthly_report(client_id, year, mon)
        if path:
            paths.append(path)
    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for cid in CLIENT_IDS:
        if (_DATA_DIR / cid).exists():
            reports = generate_all_monthly_reports(cid)
            for r in reports:
                logger.info("%s", r)
    logger.info("Done.")
