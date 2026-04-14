"""Generate monthly PnL reports as printable HTML (PDF-ready via browser print).

Each report includes:
- Month header with client details
- Performance summary (return, Sharpe, DD, volume)
- Equity curve chart for the month
- Coin-level PnL breakdown table
- Daily PnL bar chart
- Transfer log
- Footer with Odum branding

Designed for window.print() → PDF export with clean page breaks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Template

from client_reporting_api.core.backfill_store import get_equity_curve
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES
from client_reporting_api.core.trade_analytics import CLIENT_IDS, _load_json

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backfill"
_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"

_REPORT_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ client_name }} — {{ month_label }} Performance Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Inter', sans-serif; background: #fff; color: #1a1a2e; font-size: 12px; }
  .page { max-width: 800px; margin: 0 auto; padding: 40px; }

  /* Header */
  .report-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #fff; padding: 32px 40px; border-radius: 8px 8px 0 0;
    position: relative;
  }
  .report-header::after {
    content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, #e94560, #0f3460, #e94560);
  }
  .report-header h1 { font-size: 22px; font-weight: 700; }
  .report-header .subtitle { font-size: 12px; color: rgba(255,255,255,0.6); margin-top: 4px; }
  .report-header .logo { font-size: 10px; color: rgba(255,255,255,0.4); margin-top: 8px; letter-spacing: 2px; text-transform: uppercase; }

  /* Content */
  .content { border: 1px solid #e9ecef; border-top: none; border-radius: 0 0 8px 8px; padding: 24px 40px; }

  /* Stats grid */
  .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .stat { text-align: center; padding: 12px; background: #f8f9fa; border-radius: 6px; }
  .stat .label { font-size: 9px; text-transform: uppercase; letter-spacing: 1px; color: #6c757d; }
  .stat .value { font-size: 18px; font-weight: 700; margin-top: 4px; }
  .positive { color: #28a745; }
  .negative { color: #dc3545; }

  /* Charts */
  .chart-section { margin-bottom: 24px; }
  .chart-section h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #6c757d; margin-bottom: 8px; }
  .chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }

  /* Tables */
  table { width: 100%; border-collapse: collapse; margin-bottom: 16px; }
  th { text-align: left; padding: 8px 6px; border-bottom: 2px solid #1a1a2e; color: #6c757d; font-size: 9px; text-transform: uppercase; letter-spacing: 1px; }
  td { padding: 6px; border-bottom: 1px solid #f0f0f0; font-variant-numeric: tabular-nums; }
  .right { text-align: right; }
  tfoot td { font-weight: 700; border-top: 2px solid #1a1a2e; }

  /* Footer */
  .footer { margin-top: 32px; padding-top: 16px; border-top: 1px solid #e9ecef; text-align: center; color: #adb5bd; font-size: 10px; }

  @media print {
    .page { padding: 20px; max-width: none; }
    .report-header { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .stat { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  }
</style>
</head>
<body>
<div class="page">
  <div class="report-header">
    <h1>{{ client_name }}</h1>
    <div class="subtitle">Monthly Performance Report — {{ month_label }}</div>
    <div class="logo">Odum Research Ltd &bull; Mean Reversion Grid Strategy</div>
  </div>

  <div class="content">
    <!-- Performance Summary -->
    <div class="stats-grid">
      <div class="stat">
        <div class="label">Starting Equity</div>
        <div class="value">${{ '{:,.0f}'.format(start_equity) }}</div>
      </div>
      <div class="stat">
        <div class="label">Ending Equity</div>
        <div class="value">${{ '{:,.0f}'.format(end_equity) }}</div>
      </div>
      <div class="stat">
        <div class="label">Month PnL</div>
        <div class="value {{ 'positive' if month_pnl >= 0 else 'negative' }}">${{ '{:,.0f}'.format(month_pnl) }}</div>
      </div>
      <div class="stat">
        <div class="label">Return</div>
        <div class="value {{ 'positive' if month_return >= 0 else 'negative' }}">{{ '{:.1f}'.format(month_return) }}%</div>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat">
        <div class="label">Trading Volume</div>
        <div class="value">${{ '{:,.0f}'.format(month_volume) }}</div>
      </div>
      <div class="stat">
        <div class="label">Trades</div>
        <div class="value">{{ '{:,}'.format(month_trades) }}</div>
      </div>
      <div class="stat">
        <div class="label">Net Transfers</div>
        <div class="value">${{ '{:,.0f}'.format(month_transfers) }}</div>
      </div>
      <div class="stat">
        <div class="label">Trading Days</div>
        <div class="value">{{ trading_days }}</div>
      </div>
    </div>

    <!-- Charts -->
    <div class="chart-row">
      <div class="chart-section">
        <h3>Equity Curve</h3>
        <canvas id="equityChart" height="160"></canvas>
      </div>
      <div class="chart-section">
        <h3>Daily PnL</h3>
        <canvas id="dailyPnlChart" height="160"></canvas>
      </div>
    </div>

    <!-- Coin Breakdown -->
    <div class="chart-section">
      <h3>Coin-Level PnL Breakdown</h3>
      <table>
        <thead>
          <tr>
            <th>Coin</th>
            <th class="right">Realized</th>
            <th class="right">Fees</th>
            <th class="right">Funding</th>
            <th class="right">Net PnL</th>
            <th class="right">Volume</th>
            <th class="right">Trades</th>
          </tr>
        </thead>
        <tbody>
          {% for c in coins %}
          <tr>
            <td><strong>{{ c.symbol }}</strong></td>
            <td class="right {{ 'positive' if c.realized >= 0 else 'negative' }}">${{ '{:,.2f}'.format(c.realized) }}</td>
            <td class="right">${{ '{:,.2f}'.format(c.fees) }}</td>
            <td class="right {{ 'positive' if c.funding >= 0 else 'negative' }}">${{ '{:,.2f}'.format(c.funding) }}</td>
            <td class="right {{ 'positive' if c.net >= 0 else 'negative' }}"><strong>${{ '{:,.2f}'.format(c.net) }}</strong></td>
            <td class="right">${{ '{:,.0f}'.format(c.volume) }}</td>
            <td class="right">{{ c.trades }}</td>
          </tr>
          {% endfor %}
        </tbody>
        <tfoot>
          <tr>
            <td>TOTAL</td>
            <td class="right">${{ '{:,.2f}'.format(total_realized) }}</td>
            <td class="right">${{ '{:,.2f}'.format(total_fees) }}</td>
            <td class="right">${{ '{:,.2f}'.format(total_funding) }}</td>
            <td class="right"><strong>${{ '{:,.2f}'.format(total_net) }}</strong></td>
            <td class="right">${{ '{:,.0f}'.format(total_volume) }}</td>
            <td class="right">{{ total_trades }}</td>
          </tr>
        </tfoot>
      </table>
    </div>

    {% if transfers %}
    <div class="chart-section">
      <h3>Transfers</h3>
      <table>
        <thead><tr><th>Date</th><th class="right">Amount</th><th>Type</th></tr></thead>
        <tbody>
        {% for t in transfers %}
        <tr>
          <td>{{ t.date }}</td>
          <td class="right {{ 'positive' if t.amount > 0 else 'negative' }}">
            {{ '$' if t.amount >= 0 else '-$' }}{{ '{:,.0f}'.format(t.amount if t.amount >= 0 else -t.amount) }}
          </td>
          <td>{{ 'Deposit' if t.amount > 0 else 'Withdrawal' }}</td>
        </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}
  </div>

  <div class="footer">
    <p>Odum Research Ltd &bull; 9 Appold Street, London EC2A 2AP</p>
    <p>Generated {{ generated_at }} &bull; Confidential</p>
  </div>
</div>

<script>
Chart.defaults.font.family = 'Inter';
Chart.defaults.font.size = 10;

new Chart(document.getElementById('equityChart'), {
  type: 'line',
  data: {
    labels: {{ equity_dates | tojson }},
    datasets: [{
      data: {{ equity_values | tojson }},
      borderColor: '#1a1a2e', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
    }],
  },
  options: { responsive: true, plugins: { legend: { display: false } },
    scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } },
              y: { ticks: { callback: v => '$' + (v/1000).toFixed(0) + 'K' } } } }
});

const dpnl = {{ daily_pnl_values | tojson }};
new Chart(document.getElementById('dailyPnlChart'), {
  type: 'bar',
  data: {
    labels: {{ equity_dates | tojson }},
    datasets: [{
      data: dpnl,
      backgroundColor: dpnl.map(v => v >= 0 ? '#28a74588' : '#dc354588'),
      borderColor: dpnl.map(v => v >= 0 ? '#28a745' : '#dc3545'),
      borderWidth: 1,
    }],
  },
  options: { responsive: true, plugins: { legend: { display: false } },
    scales: { x: { ticks: { maxTicksLimit: 6 }, grid: { display: false } },
              y: { ticks: { callback: v => '$' + v.toLocaleString() } } } }
});
</script>
</body>
</html>
""")


def generate_monthly_report(client_id: str, year: int, month: int) -> str | None:
    """Generate monthly PnL report for a client."""
    month_str = f"{year}-{month:02d}"
    month_label = datetime(year, month, 1).strftime("%B %Y")

    # Equity curve for this month
    ec = get_equity_curve(client_id)
    if not ec:
        return None

    month_points = [p for p in ec if str(p.get("date", "")).startswith(month_str)]
    if not month_points:
        return None

    equity_dates = [str(p["date"]) for p in month_points]
    equity_values = [float(p.get("equity_usd", 0)) for p in month_points]

    start_eq = equity_values[0]
    end_eq = equity_values[-1]

    # Transfers this month
    month_transfers_total = 0.0
    transfers_list: list[dict[str, str | float]] = []
    for p in month_points:
        t = p.get("transfer_usd")
        if t is not None and str(p.get("date")) != equity_dates[0]:
            month_transfers_total += float(t)
            transfers_list.append({"date": str(p["date"]), "amount": float(t)})

    month_pnl = end_eq - start_eq - month_transfers_total

    # Daily PnL
    daily_pnl = [equity_values[0] - start_eq]
    prev = start_eq
    for i, p in enumerate(month_points):
        if i == 0:
            continue
        t = float(p.get("transfer_usd", 0) or 0) if str(p.get("date")) != equity_dates[0] else 0
        daily = equity_values[i] - t - prev
        daily_pnl.append(round(daily, 2))
        prev = equity_values[i]

    # Month return
    month_return = (month_pnl / start_eq * 100) if start_eq > 0 else 0

    # Coin breakdown for this month (from bills)
    bills = _load_json(client_id, "bills_ledger.json") or []
    from collections import defaultdict

    coin_data: dict[str, dict[str, float]] = defaultdict(
        lambda: {"realized": 0, "fees": 0, "funding": 0, "volume": 0, "trades": 0}
    )

    for b in bills:
        ts = b.get("timestamp", 0)
        if not ts:
            continue
        dt = datetime.fromtimestamp(ts / 1000, tz=UTC)
        if dt.strftime("%Y-%m") != month_str:
            continue
        inst = b.get("inst_id", "")
        sym = inst.split("-")[0] if inst else ""
        if not sym:
            continue
        change = float(b.get("change", 0))
        st = b.get("sub_type", "")
        if st == "5":
            coin_data[sym]["realized"] += change
        elif st == "3":
            coin_data[sym]["fees"] += change
        elif st in ("173", "174") or b.get("type") == "8":
            coin_data[sym]["funding"] += change

    # Trade volume for this month
    trades = _load_json(client_id, "trades.json") or []
    month_trade_count = 0
    for t in trades:
        dt_str = t.get("datetime", "")
        if dt_str and dt_str[:7] == month_str:
            sym = t.get("symbol", "").split("/")[0]
            vol = float(t.get("cost", 0) or 0)
            coin_data[sym]["volume"] += vol
            coin_data[sym]["trades"] += 1
            month_trade_count += 1

    # Build coin list
    coins = []
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
    coins.sort(key=lambda c: abs(c["net"]), reverse=True)

    total_realized = sum(c["realized"] for c in coins)
    total_fees = sum(c["fees"] for c in coins)
    total_funding = sum(c["funding"] for c in coins)
    total_net = sum(c["net"] for c in coins)
    total_volume = sum(c["volume"] for c in coins)

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
        month_transfers=month_transfers_total,
        trading_days=len(equity_dates),
        equity_dates=equity_dates,
        equity_values=equity_values,
        daily_pnl_values=daily_pnl,
        coins=coins,
        total_realized=total_realized,
        total_fees=total_fees,
        total_funding=total_funding,
        total_net=total_net,
        total_volume=total_volume,
        total_trades=month_trade_count,
        transfers=transfers_list,
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
    print("Done.")
