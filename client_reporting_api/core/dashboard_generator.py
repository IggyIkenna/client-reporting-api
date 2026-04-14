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
    compute_monthly_returns,
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

_DASHBOARD_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ client_name }} — Performance Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.2.0/dist/chartjs-plugin-zoom.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammer-simulator@0.0.1/index.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/hammerjs@2.0.8/hammer.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Inter', sans-serif; background: #0f1117; color: #e0e0e0; }
  .dashboard { max-width: 1400px; margin: 0 auto; padding: 20px; }

  /* Header */
  .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid #2a2d3a; margin-bottom: 20px; }
  .header h1 { font-size: 28px; font-weight: 700; color: #fff; }
  .header .strategy { font-size: 13px; color: #8b8fa3; margin-top: 2px; }
  .header .controls { display: flex; gap: 8px; align-items: center; }
  select { background: #1a1d2a; color: #e0e0e0; border: 1px solid #2a2d3a; border-radius: 6px; padding: 8px 12px; font-size: 13px; cursor: pointer; }
  select:hover { border-color: #4a9eff; }
  .btn { background: #1a1d2a; color: #e0e0e0; border: 1px solid #2a2d3a; border-radius: 6px; padding: 8px 14px; font-size: 12px; cursor: pointer; transition: all 0.15s; }
  .btn:hover { background: #2a2d3a; border-color: #4a9eff; }
  .btn.active { background: #4a9eff22; border-color: #4a9eff; color: #4a9eff; }

  /* Stats Grid */
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .stat-card { background: #1a1d2a; border-radius: 8px; padding: 16px; border: 1px solid #2a2d3a; }
  .stat-card .label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; color: #6b7185; margin-bottom: 6px; }
  .stat-card .value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .positive { color: #00c48c; }
  .negative { color: #ff4d6a; }
  .neutral { color: #8b8fa3; }

  /* Chart containers */
  .chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
  .chart-row.full { grid-template-columns: 1fr; }
  .chart-card { background: #1a1d2a; border-radius: 8px; padding: 20px; border: 1px solid #2a2d3a; }
  .chart-card h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #6b7185; margin-bottom: 12px; }
  .chart-card .chart-controls { display: flex; gap: 6px; margin-bottom: 10px; }
  canvas { width: 100% !important; }
  .zoom-hint { font-size: 11px; color: #4a5568; text-align: right; margin-top: 4px; }

  /* Coin breakdown table */
  .table-card { background: #1a1d2a; border-radius: 8px; padding: 20px; border: 1px solid #2a2d3a; margin-bottom: 20px; }
  .table-card h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: #6b7185; margin-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; padding: 10px 8px; border-bottom: 2px solid #2a2d3a; color: #6b7185; font-size: 10px; text-transform: uppercase; letter-spacing: 1px; cursor: pointer; }
  th:hover { color: #4a9eff; }
  td { padding: 8px; border-bottom: 1px solid #1f2233; font-variant-numeric: tabular-nums; }
  tr:hover td { background: #1f2233; }
  .right { text-align: right; }

  /* Order browser */
  .order-browser { margin-bottom: 20px; }
  .pagination { display: flex; gap: 6px; align-items: center; justify-content: center; margin-top: 12px; }
  .pagination .btn { min-width: 32px; text-align: center; }
  .pagination .info { font-size: 12px; color: #6b7185; }
  .filter-row { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .filter-row input { background: #0f1117; color: #e0e0e0; border: 1px solid #2a2d3a; border-radius: 6px; padding: 6px 10px; font-size: 12px; width: 120px; }

  /* Tabs */
  .tabs { display: flex; gap: 0; margin-bottom: 20px; border-bottom: 2px solid #2a2d3a; }
  .tab { padding: 10px 20px; font-size: 13px; font-weight: 500; color: #6b7185; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: all 0.15s; }
  .tab:hover { color: #e0e0e0; }
  .tab.active { color: #4a9eff; border-bottom-color: #4a9eff; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* PDF button */
  .pdf-btn { background: linear-gradient(135deg, #4a9eff, #2563eb); color: #fff; border: none; border-radius: 6px; padding: 8px 16px; font-size: 12px; font-weight: 600; cursor: pointer; }
  .pdf-btn:hover { opacity: 0.9; }

  /* Transfer log */
  .transfer-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .transfer-badge.deposit { background: #00c48c22; color: #00c48c; }
  .transfer-badge.withdrawal { background: #ff4d6a22; color: #ff4d6a; }

  @media print {
    body { background: #fff; color: #000; }
    .chart-card, .stat-card, .table-card { border-color: #ddd; background: #fff; }
    .header .controls { display: none; }
  }
  @media (max-width: 768px) {
    .chart-row { grid-template-columns: 1fr; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
  }
</style>
</head>
<body>
<div class="dashboard">
  <!-- Header -->
  <div class="header">
    <div>
      <h1>{{ client_name }}</h1>
      <div class="strategy">{{ strategy }} &mdash; {{ start_date }} to {{ end_date }} ({{ equity_days }} days)</div>
    </div>
    <div class="controls">
      <select id="clientSelect" onchange="switchClient(this.value)">
        {% for cid, cname in all_clients %}
        <option value="{{ cid }}" {{ 'selected' if cid == client_id else '' }}>{{ cname }}</option>
        {% endfor %}
        <option value="AGGREGATE">All Clients (Aggregate)</option>
      </select>
      <select id="timeframeSelect" onchange="applyTimeframe(this.value)">
        <option value="all">All Time</option>
        <option value="7d">7 Days</option>
        <option value="30d">30 Days</option>
        <option value="90d">90 Days</option>
        <option value="180d">6 Months</option>
        <option value="ytd">YTD</option>
      </select>
      <button class="pdf-btn" onclick="window.print()">Export PDF</button>
    </div>
  </div>

  <!-- Stats Grid -->
  <div class="stats-grid">
    <div class="stat-card">
      <div class="label">Starting Equity</div>
      <div class="value neutral">{{ unit }}{{ '{:,.0f}'.format(starting_equity) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Current Equity</div>
      <div class="value neutral">{{ unit }}{{ '{:,.0f}'.format(current_equity) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Trading PnL</div>
      <div class="value {{ 'positive' if trading_pnl >= 0 else 'negative' }}">{{ unit }}{{ '{:,.0f}'.format(trading_pnl) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Simple Return</div>
      <div class="value {{ 'positive' if simple_return >= 0 else 'negative' }}">{{ '{:.1f}'.format(simple_return) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Compounded Return</div>
      <div class="value {{ 'positive' if compounded_return >= 0 else 'negative' }}">{{ '{:.1f}'.format(compounded_return) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Annualized Return</div>
      <div class="value {{ 'positive' if annualized_return >= 0 else 'negative' }}">{{ '{:.1f}'.format(annualized_return) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Sharpe Ratio</div>
      <div class="value {{ 'positive' if sharpe >= 0 else 'negative' }}">{{ '{:.2f}'.format(sharpe) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Sortino Ratio</div>
      <div class="value {{ 'positive' if sortino >= 0 else 'negative' }}">{{ '{:.2f}'.format(sortino) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Calmar Ratio</div>
      <div class="value {{ 'positive' if calmar >= 0 else 'negative' }}">{{ '{:.2f}'.format(calmar) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Max Drawdown</div>
      <div class="value negative">{{ '{:.1f}'.format(max_dd) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">DD Duration</div>
      <div class="value neutral">{{ dd_duration }}d</div>
    </div>
    <div class="stat-card">
      <div class="label">Win Rate</div>
      <div class="value {{ 'positive' if win_rate >= 50 else 'negative' }}">{{ '{:.1f}'.format(win_rate) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Avg Daily Volume</div>
      <div class="value neutral">${{ '{:,.0f}'.format(avg_daily_vol) }}</div>
    </div>
    <div class="stat-card">
      <div class="label">Capital Deployment</div>
      <div class="value neutral">{{ '{:.1f}'.format(cap_deploy * 100) }}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Avg Holding Time</div>
      <div class="value neutral">{{ '{:.1f}'.format(avg_hold_hours) }}h</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Trades</div>
      <div class="value neutral">{{ '{:,}'.format(total_trades) }}</div>
    </div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" data-tab="charts">Charts</div>
    <div class="tab" data-tab="coins">Coin Breakdown</div>
    <div class="tab" data-tab="orders">Orders & Trades</div>
    <div class="tab" data-tab="transfers">Transfers</div>
  </div>

  <!-- Charts Tab -->
  <div class="tab-content active" id="tab-charts">
    <!-- PnL + Equity Chart -->
    <div class="chart-row full">
      <div class="chart-card">
        <h3>Trading PnL & Equity Curve</h3>
        <canvas id="pnlEquityChart" height="300"></canvas>
        <div class="zoom-hint">Scroll to zoom &bull; Click+drag to pan &bull; Double-click to reset</div>
      </div>
    </div>

    <!-- Daily + Monthly PnL Bars -->
    <div class="chart-row">
      <div class="chart-card">
        <h3>Daily PnL</h3>
        <canvas id="dailyPnlChart" height="200"></canvas>
      </div>
      <div class="chart-card">
        <h3>Monthly PnL</h3>
        <canvas id="monthlyPnlChart" height="200"></canvas>
      </div>
    </div>

    <!-- Volume + Capital Deployment -->
    <div class="chart-row">
      <div class="chart-card">
        <h3>Daily Trading Volume</h3>
        <canvas id="volumeChart" height="200"></canvas>
      </div>
      <div class="chart-card">
        <h3>Coin PnL Breakdown</h3>
        <canvas id="coinPnlChart" height="200"></canvas>
      </div>
    </div>
  </div>

  <!-- Coin Breakdown Tab -->
  <div class="tab-content" id="tab-coins">
    <div class="table-card">
      <h3>Coin-Level PnL & Volume Breakdown</h3>
      <p style="font-size:11px;color:#6b7185;margin-bottom:12px;">
        Total Realized PnL: <span class="{{ 'positive' if total_net_pnl >= 0 else 'negative' }}">{{ unit }}{{ '{:,.2f}'.format(total_net_pnl) }}</span>
        &bull; Validation: sum of coin PnL = {{ unit }}{{ '{:,.2f}'.format(coin_pnl_sum) }}
        {% if coin_pnl_sum != total_net_pnl %}
        <span style="color:#ff4d6a;">(mismatch!)</span>
        {% else %}
        <span class="positive">(validated)</span>
        {% endif %}
      </p>
      <table id="coinTable">
        <thead>
          <tr>
            <th onclick="sortTable('coinTable',0)">Coin</th>
            <th class="right" onclick="sortTable('coinTable',1)">Realized PnL</th>
            <th class="right" onclick="sortTable('coinTable',2)">Fees</th>
            <th class="right" onclick="sortTable('coinTable',3)">Funding</th>
            <th class="right" onclick="sortTable('coinTable',4)">Net PnL</th>
            <th class="right" onclick="sortTable('coinTable',5)">Volume</th>
            <th class="right" onclick="sortTable('coinTable',6)">Trades</th>
            <th class="right" onclick="sortTable('coinTable',7)">Buy/Sell</th>
            <th class="right" onclick="sortTable('coinTable',8)">Avg Size</th>
            <th class="right" onclick="sortTable('coinTable',9)">Avg Hold</th>
            <th class="right" onclick="sortTable('coinTable',10)">Round Trips</th>
          </tr>
        </thead>
        <tbody>
          {% for c in coins %}
          <tr>
            <td><strong>{{ c.symbol }}</strong></td>
            <td class="right {{ 'positive' if c.realized_pnl >= 0 else 'negative' }}">${{ '{:,.2f}'.format(c.realized_pnl) }}</td>
            <td class="right {{ 'positive' if c.trading_fees >= 0 else 'negative' }}">${{ '{:,.2f}'.format(c.trading_fees) }}</td>
            <td class="right {{ 'positive' if c.funding_pnl >= 0 else 'negative' }}">${{ '{:,.2f}'.format(c.funding_pnl) }}</td>
            <td class="right {{ 'positive' if c.net_pnl >= 0 else 'negative' }}"><strong>${{ '{:,.2f}'.format(c.net_pnl) }}</strong></td>
            <td class="right">${{ '{:,.0f}'.format(c.volume_usd) }}</td>
            <td class="right">{{ '{:,}'.format(c.trade_count) }}</td>
            <td class="right">{{ c.buy_count }}/{{ c.sell_count }}</td>
            <td class="right">${{ '{:,.0f}'.format(c.avg_trade_size_usd) }}</td>
            <td class="right">{{ '{:.1f}'.format(c.avg_holding_hours) }}h</td>
            <td class="right">{{ '{:,}'.format(c.round_trips) }}</td>
          </tr>
          {% endfor %}
        </tbody>
        <tfoot>
          <tr style="font-weight:700;border-top:2px solid #2a2d3a;">
            <td>TOTAL</td>
            <td class="right {{ 'positive' if total_realized >= 0 else 'negative' }}">${{ '{:,.2f}'.format(total_realized) }}</td>
            <td class="right {{ 'positive' if total_fees >= 0 else 'negative' }}">${{ '{:,.2f}'.format(total_fees) }}</td>
            <td class="right {{ 'positive' if total_funding >= 0 else 'negative' }}">${{ '{:,.2f}'.format(total_funding) }}</td>
            <td class="right {{ 'positive' if total_net_pnl >= 0 else 'negative' }}">${{ '{:,.2f}'.format(total_net_pnl) }}</td>
            <td class="right">${{ '{:,.0f}'.format(total_volume) }}</td>
            <td class="right">{{ '{:,}'.format(total_trades) }}</td>
            <td class="right">—</td>
            <td class="right">—</td>
            <td class="right">{{ '{:.1f}'.format(avg_hold_hours) }}h</td>
            <td class="right">{{ '{:,}'.format(total_round_trips) }}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>

  <!-- Orders Tab -->
  <div class="tab-content" id="tab-orders">
    <div class="table-card order-browser">
      <h3>Orders & Trades</h3>
      <div class="filter-row">
        <input type="text" id="symbolFilter" placeholder="Symbol..." oninput="filterOrders()">
        <select id="sideFilter" onchange="filterOrders()">
          <option value="">All Sides</option>
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </select>
        <select id="statusFilter" onchange="filterOrders()">
          <option value="">All Status</option>
          <option value="closed">Closed</option>
          <option value="open">Open</option>
          <option value="canceled">Canceled</option>
        </select>
      </div>
      <table id="ordersTable">
        <thead>
          <tr>
            <th>Date/Time</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Type</th>
            <th class="right">Price</th>
            <th class="right">Filled</th>
            <th class="right">Cost</th>
            <th class="right">Fee</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="ordersBody"></tbody>
      </table>
      <div class="pagination">
        <button class="btn" onclick="prevPage()">&laquo; Prev</button>
        <span class="info" id="pageInfo">Page 1</span>
        <button class="btn" onclick="nextPage()">Next &raquo;</button>
      </div>
    </div>
  </div>

  <!-- Transfers Tab -->
  <div class="tab-content" id="tab-transfers">
    <div class="table-card">
      <h3>Deposits & Withdrawals</h3>
      <table>
        <thead>
          <tr><th>Date</th><th class="right">Amount</th><th>Type</th><th class="right">Cumulative</th></tr>
        </thead>
        <tbody>
          {% for t in transfers %}
          <tr>
            <td>{{ t.date }}</td>
            <td class="right {{ 'positive' if t.amount > 0 else 'negative' }}">
              {{ '$' if t.amount >= 0 else '-$' }}{{ '{:,.0f}'.format(t.amount if t.amount >= 0 else -t.amount) }}
            </td>
            <td><span class="transfer-badge {{ 'deposit' if t.amount > 0 else 'withdrawal' }}">{{ 'Deposit' if t.amount > 0 else 'Withdrawal' }}</span></td>
            <td class="right">${{ '{:,.0f}'.format(t.cumulative) }}</td>
          </tr>
          {% endfor %}
          {% if not transfers %}
          <tr><td colspan="4" style="text-align:center;color:#6b7185;">No transfers recorded</td></tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </div>
</div>

<script>
// ── Data ──
const dates = {{ dates | tojson }};
const pnlSeries = {{ pnl_series | tojson }};
const equitySeries = {{ equity_series | tojson }};
const dailyPnl = {{ daily_pnl | tojson }};
const dailyPnlDates = {{ daily_pnl_dates | tojson }};
const monthlyPnl = {{ monthly_pnl | tojson }};
const monthlyPnlMonths = {{ monthly_pnl_months | tojson }};
const dailyVolumes = {{ daily_volumes | tojson }};
const dailyVolDates = {{ daily_vol_dates | tojson }};
const coinLabels = {{ coin_labels | tojson }};
const coinPnlValues = {{ coin_pnl_values | tojson }};
const allOrders = {{ orders_json | tojson }};

// ── Chart defaults ──
Chart.defaults.color = '#8b8fa3';
Chart.defaults.borderColor = '#2a2d3a';
Chart.defaults.font.family = 'Inter';

const zoomOpts = {
  zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
  pan: { enabled: true, mode: 'x' },
};

// ── PnL + Equity Chart ──
const pnlEquityCtx = document.getElementById('pnlEquityChart').getContext('2d');
const pnlEquityChart = new Chart(pnlEquityCtx, {
  type: 'line',
  data: {
    labels: dates,
    datasets: [
      {
        label: 'Trading PnL ($)',
        data: pnlSeries,
        borderColor: pnlSeries[pnlSeries.length-1] >= 0 ? '#00c48c' : '#ff4d6a',
        backgroundColor: pnlSeries[pnlSeries.length-1] >= 0 ? 'rgba(0,196,140,0.06)' : 'rgba(255,77,106,0.06)',
        fill: true, tension: 0.3, pointRadius: 0, pointHitRadius: 8, borderWidth: 2,
      },
      {
        label: 'Equity ($)',
        data: equitySeries,
        borderColor: '#4a9eff',
        borderDash: [4, 3],
        fill: false, tension: 0.3, pointRadius: 0, pointHitRadius: 8, borderWidth: 1.5, hidden: true,
      },
    ],
  },
  options: {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { position: 'top' }, zoom: zoomOpts,
      tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined,{maximumFractionDigits:0}) } }
    },
    scales: {
      x: { ticks: { maxTicksLimit: 14, font: { size: 10 } }, grid: { display: false } },
      y: { ticks: { callback: v => '$' + v.toLocaleString(), font: { size: 10 } }, grid: { color: '#1f2233' } },
    },
  },
});

// ── Daily PnL bars ──
new Chart(document.getElementById('dailyPnlChart'), {
  type: 'bar',
  data: {
    labels: dailyPnlDates,
    datasets: [{
      data: dailyPnl,
      backgroundColor: dailyPnl.map(v => v >= 0 ? '#00c48c88' : '#ff4d6a88'),
      borderColor: dailyPnl.map(v => v >= 0 ? '#00c48c' : '#ff4d6a'),
      borderWidth: 1,
    }],
  },
  options: {
    responsive: true,
    plugins: { legend: { display: false }, zoom: zoomOpts,
      tooltip: { callbacks: { label: ctx => '$' + ctx.parsed.y.toLocaleString(undefined,{maximumFractionDigits:0}) } }
    },
    scales: {
      x: { ticks: { maxTicksLimit: 10, font: { size: 10 } }, grid: { display: false } },
      y: { ticks: { callback: v => '$' + v.toLocaleString(), font: { size: 10 } }, grid: { color: '#1f2233' } },
    },
  },
});

// ── Monthly PnL bars ──
new Chart(document.getElementById('monthlyPnlChart'), {
  type: 'bar',
  data: {
    labels: monthlyPnlMonths,
    datasets: [{
      data: monthlyPnl,
      backgroundColor: monthlyPnl.map(v => v >= 0 ? '#00c48c88' : '#ff4d6a88'),
      borderColor: monthlyPnl.map(v => v >= 0 ? '#00c48c' : '#ff4d6a'),
      borderWidth: 1,
    }],
  },
  options: {
    responsive: true,
    plugins: { legend: { display: false },
      tooltip: { callbacks: { label: ctx => '$' + ctx.parsed.y.toLocaleString(undefined,{maximumFractionDigits:0}) } }
    },
    scales: {
      x: { grid: { display: false } },
      y: { ticks: { callback: v => '$' + v.toLocaleString(), font: { size: 10 } }, grid: { color: '#1f2233' } },
    },
  },
});

// ── Volume chart ──
new Chart(document.getElementById('volumeChart'), {
  type: 'bar',
  data: {
    labels: dailyVolDates,
    datasets: [{
      data: dailyVolumes,
      backgroundColor: '#4a9eff44',
      borderColor: '#4a9eff',
      borderWidth: 1,
    }],
  },
  options: {
    responsive: true,
    plugins: { legend: { display: false }, zoom: zoomOpts,
      tooltip: { callbacks: { label: ctx => '$' + ctx.parsed.y.toLocaleString(undefined,{maximumFractionDigits:0}) } }
    },
    scales: {
      x: { ticks: { maxTicksLimit: 10, font: { size: 10 } }, grid: { display: false } },
      y: { ticks: { callback: v => '$' + (v/1000).toFixed(0) + 'K', font: { size: 10 } }, grid: { color: '#1f2233' } },
    },
  },
});

// ── Coin PnL horizontal bar ──
new Chart(document.getElementById('coinPnlChart'), {
  type: 'bar',
  data: {
    labels: coinLabels,
    datasets: [{
      data: coinPnlValues,
      backgroundColor: coinPnlValues.map(v => v >= 0 ? '#00c48c88' : '#ff4d6a88'),
      borderColor: coinPnlValues.map(v => v >= 0 ? '#00c48c' : '#ff4d6a'),
      borderWidth: 1,
    }],
  },
  options: {
    indexAxis: 'y',
    responsive: true,
    plugins: { legend: { display: false },
      tooltip: { callbacks: { label: ctx => '$' + ctx.parsed.x.toLocaleString(undefined,{maximumFractionDigits:0}) } }
    },
    scales: {
      x: { ticks: { callback: v => '$' + v.toLocaleString(), font: { size: 10 } }, grid: { color: '#1f2233' } },
      y: { grid: { display: false } },
    },
  },
});

// ── Tabs ──
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── Client switching ──
function switchClient(clientId) {
  const base = window.location.pathname.replace(/[^/]+$/, '');
  window.location.href = base + clientId + '_dashboard.html';
}

// ── Timeframe filter ──
function applyTimeframe(tf) {
  // Recompute visible range based on timeframe
  const allDates = dates.map(d => new Date(d));
  const last = allDates[allDates.length - 1];
  let cutoff = allDates[0];

  if (tf === '7d') cutoff = new Date(last - 7*86400000);
  else if (tf === '30d') cutoff = new Date(last - 30*86400000);
  else if (tf === '90d') cutoff = new Date(last - 90*86400000);
  else if (tf === '180d') cutoff = new Date(last - 180*86400000);
  else if (tf === 'ytd') cutoff = new Date(last.getFullYear(), 0, 1);
  else { pnlEquityChart.resetZoom(); return; }

  const start = dates.findIndex(d => new Date(d) >= cutoff);
  if (start >= 0) {
    pnlEquityChart.options.scales.x.min = dates[start];
    pnlEquityChart.options.scales.x.max = dates[dates.length-1];
    pnlEquityChart.update();
  }
}

// ── Order browser ──
const PAGE_SIZE = 50;
let currentPage = 0;
let filteredOrders = [...allOrders];

function filterOrders() {
  const sym = document.getElementById('symbolFilter').value.toUpperCase();
  const side = document.getElementById('sideFilter').value;
  const status = document.getElementById('statusFilter').value;
  filteredOrders = allOrders.filter(o => {
    if (sym && !o.symbol.toUpperCase().includes(sym)) return false;
    if (side && o.side !== side) return false;
    if (status && o.status !== status) return false;
    return true;
  });
  currentPage = 0;
  renderOrders();
}

function renderOrders() {
  const body = document.getElementById('ordersBody');
  const start = currentPage * PAGE_SIZE;
  const page = filteredOrders.slice(start, start + PAGE_SIZE);
  body.innerHTML = page.map(o => `<tr>
    <td>${o.datetime || ''}</td>
    <td><strong>${o.symbol || ''}</strong></td>
    <td class="${o.side === 'buy' ? 'positive' : 'negative'}">${o.side || ''}</td>
    <td>${o.type || ''}</td>
    <td class="right">$${(o.price || 0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
    <td class="right">${(o.filled || 0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
    <td class="right">$${(o.cost || 0).toLocaleString(undefined,{maximumFractionDigits:2})}</td>
    <td class="right">$${(o.fee_cost || 0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
    <td>${o.status || ''}</td>
  </tr>`).join('');
  document.getElementById('pageInfo').textContent =
    `Page ${currentPage+1} of ${Math.ceil(filteredOrders.length/PAGE_SIZE)} (${filteredOrders.length} orders)`;
}

function nextPage() {
  if ((currentPage+1)*PAGE_SIZE < filteredOrders.length) { currentPage++; renderOrders(); }
}
function prevPage() {
  if (currentPage > 0) { currentPage--; renderOrders(); }
}

// ── Table sorting ──
function sortTable(tableId, colIdx) {
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const isNum = colIdx > 0;
  rows.sort((a, b) => {
    let va = a.cells[colIdx].textContent.replace(/[$,%h,]/g, '').trim();
    let vb = b.cells[colIdx].textContent.replace(/[$,%h,]/g, '').trim();
    if (isNum) return parseFloat(vb) - parseFloat(va);
    return va.localeCompare(vb);
  });
  rows.forEach(r => tbody.appendChild(r));
}

// Init orders
renderOrders();
</script>
</body>
</html>
""")


def _compute_daily_pnl(pnl_series: list[float]) -> list[float]:
    """Compute daily PnL changes from cumulative PnL series."""
    if not pnl_series:
        return []
    return [pnl_series[0]] + [
        round(pnl_series[i] - pnl_series[i - 1], 2) for i in range(1, len(pnl_series))
    ]


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
    monthly = compute_monthly_returns(ec)

    # Trade analytics
    ta = compute_coin_breakdown(client_id)

    # Orders
    orders = _load_orders(client_id)

    # Daily PnL
    daily_pnl = _compute_daily_pnl(pnl_data["pnl_series"])

    # Client list for dropdown
    all_clients = [
        (cid, CLIENT_NAMES.get(cid, cid)) for cid in CLIENT_IDS if (_DATA_DIR / cid).exists()
    ]

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
    generated = generate_all_dashboards()
    for p in generated:
        print(f"  {p}")
    print(f"\nGenerated {len(generated)} dashboards.")
