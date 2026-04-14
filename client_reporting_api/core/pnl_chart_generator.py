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
from pathlib import Path

from jinja2 import Template

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

_CHART_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ client_name }} — PnL Chart</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: #f8f9fa;
    color: #1a1a2e;
  }
  .container {
    max-width: 1100px;
    margin: 40px auto;
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.08);
    overflow: hidden;
  }
  .header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    color: #fff;
    padding: 32px 40px;
    position: relative;
  }
  .header::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 4px;
    background: linear-gradient(90deg, #e94560, #0f3460, #e94560);
  }
  .header h1 { font-size: 24px; font-weight: 700; }
  .header .subtitle { font-size: 13px; color: rgba(255,255,255,0.6); margin-top: 4px; }
  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px;
    padding: 24px 40px;
    border-bottom: 1px solid #e9ecef;
  }
  .stat { text-align: center; }
  .stat .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #6c757d;
  }
  .stat .value {
    font-size: 22px;
    font-weight: 700;
    margin-top: 4px;
    font-variant-numeric: tabular-nums;
  }
  .stat .value.positive { color: #28a745; }
  .stat .value.negative { color: #dc3545; }
  .chart-wrap {
    padding: 24px 40px 40px;
  }
  canvas { width: 100% !important; }
  .transfer-log {
    padding: 0 40px 32px;
  }
  .transfer-log h3 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: #6c757d;
    margin-bottom: 12px;
  }
  .transfer-log table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .transfer-log th {
    text-align: left;
    padding: 8px 0;
    border-bottom: 2px solid #1a1a2e;
    color: #6c757d;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .transfer-log td {
    padding: 6px 0;
    border-bottom: 1px solid #f0f0f0;
  }
  .transfer-log .deposit { color: #28a745; }
  .transfer-log .withdrawal { color: #dc3545; }
  @media print {
    body { background: #fff; }
    .container { box-shadow: none; margin: 0; border-radius: 0; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{{ client_name }}</h1>
    <div class="subtitle">Trading PnL — {{ dates[0] }} to {{ dates[-1] }} ({{ dates | length }} days)</div>
  </div>

  <div class="stats">
    <div class="stat">
      <div class="label">Starting Equity</div>
      <div class="value">{{ unit }}{{ '{:,.4f}'.format(starting_equity) if is_btc else '{:,.0f}'.format(starting_equity) }}</div>
    </div>
    <div class="stat">
      <div class="label">Current Equity</div>
      <div class="value">{{ unit }}{{ '{:,.4f}'.format(current_equity) if is_btc else '{:,.0f}'.format(current_equity) }}</div>
    </div>
    <div class="stat">
      <div class="label">Net {{ 'BTC' if is_btc else '' }} Deposits</div>
      <div class="value">{{ unit }}{{ '{:,.4f}'.format(net_deposits) if is_btc else '{:,.0f}'.format(net_deposits) }}</div>
    </div>
    <div class="stat">
      <div class="label">Trading PnL{{ ' (BTC)' if is_btc else '' }}</div>
      <div class="value {{ 'positive' if trading_pnl >= 0 else 'negative' }}">{{ unit }}{{ '{:,.4f}'.format(trading_pnl) if is_btc else '{:,.0f}'.format(trading_pnl) }}</div>
    </div>
    <div class="stat">
      <div class="label">PnL %</div>
      <div class="value {{ 'positive' if pnl_pct >= 0 else 'negative' }}">{{ '{:.1f}'.format(pnl_pct) }}%</div>
    </div>
    {% if is_btc %}
    <div class="stat">
      <div class="label">USDT Trading PnL</div>
      <div class="value {{ 'positive' if usdt_pnl >= 0 else 'negative' }}">${{ '{:,.0f}'.format(usdt_pnl) }}</div>
    </div>
    {% endif %}
  </div>

  <div class="chart-wrap">
    <canvas id="pnlChart" height="320"></canvas>
  </div>

  {% if transfers %}
  <div class="transfer-log">
    <h3>Transfers</h3>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          {% if is_btc %}<th>BTC</th><th>USDT</th>{% else %}<th>Amount</th>{% endif %}
          <th>Type</th>
        </tr>
      </thead>
      <tbody>
      {% for t in transfers %}
        <tr>
          <td>{{ t.date }}</td>
          {% if is_btc %}
          <td class="{{ 'deposit' if t.btc_change > 0 else ('withdrawal' if t.btc_change < 0 else '') }}">
            {% if t.btc_change != 0 %}{{ '{:+.4f}'.format(t.btc_change) }}{% else %}—{% endif %}
          </td>
          <td class="{{ 'deposit' if t.usdt_change > 0 else ('withdrawal' if t.usdt_change < 0 else '') }}">
            {% if t.usdt_change != 0 %}${{ '{:+,.0f}'.format(t.usdt_change) }}{% else %}—{% endif %}
          </td>
          {% else %}
          <td class="{{ 'deposit' if t.amount > 0 else 'withdrawal' }}">
            {{ '$' if t.amount >= 0 else '-$' }}{{ '{:,.0f}'.format(t.amount if t.amount >= 0 else -t.amount) }}
          </td>
          {% endif %}
          <td>{{ 'Deposit' if t.amount > 0 else 'Withdrawal' }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>

<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
const dates = {{ dates | tojson }};
const pnl = {{ pnl_series | tojson }};
const equity = {{ equity_series | tojson }};

new Chart(ctx, {
  type: 'line',
  data: {
    labels: dates,
    datasets: [
      {
        label: 'Trading PnL ({{ "BTC" if is_btc else "$" }})',
        data: pnl,
        borderColor: pnl[pnl.length-1] >= 0 ? '#28a745' : '#dc3545',
        backgroundColor: (pnl[pnl.length-1] >= 0 ? 'rgba(40,167,69,0.08)' : 'rgba(220,53,69,0.08)'),
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 8,
        borderWidth: 2.5,
      },
      {
        label: 'Balance ({{ "BTC" if is_btc else "$" }})',
        data: equity,
        borderColor: '#6c757d',
        borderDash: [5, 3],
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 8,
        borderWidth: 1.5,
        hidden: true,
      },
    ],
  },
  options: {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top' },
      tooltip: {
        callbacks: {
          label: function(ctx) {
            const isBtc = {{ 'true' if is_btc else 'false' }};
            if (isBtc) return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(4) + ' BTC';
            return ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {maximumFractionDigits: 0});
          }
        }
      }
    },
    scales: {
      x: {
        ticks: { maxTicksLimit: 12, font: { size: 11 } },
        grid: { display: false },
      },
      y: {
        ticks: {
          callback: function(v) {
            const isBtc = {{ 'true' if is_btc else 'false' }};
            if (isBtc) return v.toFixed(3) + ' BTC';
            return '$' + v.toLocaleString();
          },
          font: { size: 11 },
        },
        grid: { color: 'rgba(0,0,0,0.05)' },
      },
    },
  },
});
</script>
</body>
</html>
""")


BTC_ACCOUNTS = {"GP", "SL2", "ANU"}


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

    is_btc = client_id in BTC_ACCOUNTS

    dates: list[str] = []
    equity_series: list[float] = []
    pnl_series: list[float] = []
    transfers: list[dict[str, float | str]] = []

    from client_reporting_api.core.backfill_store import (
        _get_equity,
        _get_transfer,
        _is_btc_account,
    )

    is_btc = _is_btc_account(curve)
    first_date = str(curve[0].get("date", ""))
    initial_equity = _get_equity(curve[0], is_btc)

    cumulative_transfers = 0.0
    prev_equity_val = initial_equity
    twr_product = 1.0

    # For MaxDD on TWR-adjusted equity
    twr_equity_curve = [1.0]

    for i, point in enumerate(curve):
        date = str(point.get("date", ""))
        equity_val = _get_equity(point, is_btc)

        day_transfer = 0.0
        if i > 0:
            day_transfer = _get_transfer(point, curve[i - 1], is_btc, first_date)

        if day_transfer != 0:
            cumulative_transfers += day_transfer
            if is_btc:
                prev_btc = float(curve[i - 1].get("btc_balance", 0))
                btc_change = float(point.get("btc_balance", 0)) - prev_btc
                prev_usdt_val = float(curve[i - 1].get("usdt_balance", 0))
                usdt_change_val = float(point.get("usdt_balance", 0)) - prev_usdt_val
                usdt_xfer = usdt_change_val if abs(usdt_change_val) > 500 else 0.0
                transfers.append(
                    {
                        "date": date,
                        "amount": round(day_transfer, 6),
                        "btc_change": round(btc_change, 6),
                        "usdt_change": round(usdt_xfer, 2),
                        "cumulative": round(cumulative_transfers, 6),
                    }
                )
            else:
                transfers.append(
                    {
                        "date": date,
                        "amount": day_transfer,
                        "cumulative": cumulative_transfers,
                    }
                )

        # Daily TWR return (compounded)
        if i > 0 and prev_equity_val > 0:
            adjusted_end = equity_val - day_transfer
            day_return = adjusted_end / prev_equity_val - 1.0
            twr_product *= 1.0 + day_return
            twr_equity_curve.append(twr_equity_curve[-1] * (1.0 + day_return))

        trading_pnl = equity_val - initial_equity - cumulative_transfers
        prev_equity_val = equity_val

        rnd = 6 if is_btc else 2
        dates.append(date)
        equity_series.append(round(equity_val, rnd))
        pnl_series.append(round(trading_pnl, rnd))

    # MaxDD on TWR-adjusted equity (transfer-adjusted, not raw)
    peak_twr = twr_equity_curve[0]
    max_dd = 0.0
    for eq in twr_equity_curve:
        if eq > peak_twr:
            peak_twr = eq
        dd = (peak_twr - eq) / peak_twr if peak_twr > 0 else 0
        if dd > max_dd:
            max_dd = dd

    current_equity = equity_series[-1] if equity_series else 0
    trading_pnl_final = pnl_series[-1] if pnl_series else 0
    pnl_pct = (twr_product - 1.0) * 100  # compounded TWR

    result: dict[str, list[float] | list[str] | float | bool] = {
        "dates": dates,
        "pnl_series": pnl_series,
        "equity_series": equity_series,
        "transfers": transfers,
        "starting_equity": round(initial_equity, 6 if is_btc else 2),
        "current_equity": current_equity,
        "net_deposits": round(cumulative_transfers, 6 if is_btc else 2),
        "trading_pnl": trading_pnl_final,
        "pnl_pct": pnl_pct,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "is_btc": is_btc,
        "usdt_pnl": 0,
    }

    if is_btc:
        last = curve[-1]
        first = curve[0]
        result["usdt_pnl"] = round(
            float(last.get("usdt_balance", 0)) - float(first.get("usdt_balance", 0)), 2
        )

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
    generated = generate_all_charts()
    for p in generated:
        print(f"  {p}")
    print(f"\nGenerated {len(generated)} charts.")
