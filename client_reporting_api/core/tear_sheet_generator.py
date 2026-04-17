"""Institutional-grade tear sheet generator.

Generates self-contained HTML tear sheets with:
- Equity curves (multi-client overlay)
- TWR performance metrics grid
- Monthly return heatmap table
- Drawdown series chart
- Rolling Sharpe / rolling volatility
- Transfer-adjusted metrics throughout
- CSV download for daily data

Usage example (import ``generate_tear_sheet`` from this module):

    path = generate_tear_sheet(["PR", "ODUM_PROP"])
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Template

from client_reporting_api.core.backfill_store import (
    _get_equity,
    _get_transfer,
    _is_btc_account,
    compute_monthly_returns,
    compute_performance_stats,
    get_equity_curve,
)
from client_reporting_api.core.hwm_seeds import get_hwm_seed, get_pnl_recovery_seed
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "tear_sheets"

# Colours for multi-client overlay
_COLOURS = [
    "#4a9eff",  # blue
    "#00c48c",  # green
    "#ff6b6b",  # red
    "#ffd93d",  # yellow
    "#c084fc",  # purple
    "#fb923c",  # orange
    "#67e8f9",  # cyan
    "#f472b6",  # pink
]


_TearSheetData = dict[
    str,
    list[float] | list[str] | dict[str, float | str] | list[dict[str, str | float]] | bool,
]


def _compute_daily_series(
    curve: list[dict[str, str | float]],
    is_btc: bool,
    first_date: str,
) -> tuple[
    list[str],
    list[float],
    list[float],
    list[float],
    list[float],
    list[float | None],
    list[float | None],
]:
    """Compute daily time series from equity curve."""
    dates: list[str] = []
    equity_series: list[float] = []
    twr_equity_series: list[float] = [1.0]
    drawdown_series: list[float] = [0.0]
    daily_returns: list[float] = []
    rolling_sharpe: list[float | None] = [None]
    rolling_vol: list[float | None] = [None]

    peak_twr = 1.0
    rnd = 6 if is_btc else 2

    for i, point in enumerate(curve):
        date = str(point.get("date", ""))
        eq = _get_equity(point, is_btc)
        dates.append(date)
        equity_series.append(round(eq, rnd))

        if i > 0:
            eq_prev = _get_equity(curve[i - 1], is_btc)
            xfer = _get_transfer(point, curve[i - 1], is_btc, first_date)
            day_ret = (eq - xfer) / eq_prev - 1.0 if eq_prev > 0 and eq > 0 else 0.0

            daily_returns.append(day_ret)
            twr_val = twr_equity_series[-1] * (1.0 + day_ret)
            twr_equity_series.append(round(twr_val, 6))

            if twr_val > peak_twr:
                peak_twr = twr_val
            dd = (peak_twr - twr_val) / peak_twr if peak_twr > 0 else 0
            drawdown_series.append(round(-dd * 100, 2))

            _append_rolling(daily_returns, rolling_sharpe, rolling_vol)

    return (
        dates,
        equity_series,
        twr_equity_series,
        drawdown_series,
        daily_returns,
        rolling_sharpe,
        rolling_vol,
    )


def _append_rolling(
    daily_returns: list[float],
    rolling_sharpe: list[float | None],
    rolling_vol: list[float | None],
    window: int = 30,
) -> None:
    """Append rolling Sharpe and volatility values."""
    if len(daily_returns) >= window:
        recent = daily_returns[-window:]
        mean_r = sum(recent) / window
        var_r = sum((r - mean_r) ** 2 for r in recent) / window
        std_r = var_r**0.5
        sharpe = (mean_r / std_r * (365**0.5)) if std_r > 0 else 0.0
        vol = std_r * (365**0.5) * 100
        rolling_sharpe.append(round(sharpe, 2))
        rolling_vol.append(round(vol, 2))
    else:
        rolling_sharpe.append(None)
        rolling_vol.append(None)


def _compute_client_data(client_id: str) -> _TearSheetData:
    """Compute all tear sheet data for one client."""
    curve = get_equity_curve(client_id)
    if len(curve) < 2:
        return {}

    is_btc = _is_btc_account(curve)
    first_date = str(curve[0].get("date", ""))

    (
        dates,
        equity_series,
        twr_equity_series,
        drawdown_series,
        _daily_returns,
        rolling_sharpe,
        rolling_vol,
    ) = _compute_daily_series(curve, is_btc, first_date)

    stats = compute_performance_stats(
        curve,
        hwm_seed=get_hwm_seed(client_id),
        pnl_recovery=get_pnl_recovery_seed(client_id),
    )
    monthly = compute_monthly_returns(curve)

    # Build monthly return matrix (year x month)
    monthly_matrix: dict[str, dict[str, float | None]] = {}
    for m in monthly:
        month_str = str(m["month"])
        year = month_str[:4]
        mon = month_str[5:7]
        if year not in monthly_matrix:
            monthly_matrix[year] = {f"{i:02d}": None for i in range(1, 13)}
        monthly_matrix[year][mon] = float(m["return_pct"])

    # Compute yearly totals (compounded)
    year_totals: dict[str, float] = {}
    for year, months in monthly_matrix.items():
        prod = 1.0
        for mon_val in months.values():
            if mon_val is not None:
                prod *= 1.0 + mon_val / 100
        year_totals[year] = round((prod - 1.0) * 100, 2)

    denomination = "BTC" if is_btc else "USD"
    unit = "\u20bf" if is_btc else "$"

    return {
        "dates": dates,
        "equity_series": equity_series,
        "twr_equity_series": twr_equity_series,
        "drawdown_series": drawdown_series,
        "rolling_sharpe": rolling_sharpe,
        "rolling_vol": rolling_vol,
        "stats": stats,
        "monthly": monthly,
        "monthly_matrix": monthly_matrix,
        "year_totals": year_totals,
        "is_btc": is_btc,
        "denomination": denomination,
        "unit": unit,
    }


def _generate_csv_data(client_id: str, data: dict[str, list[float] | list[str]]) -> str:
    """Generate CSV content for daily data download."""
    output = io.StringIO()
    writer = csv.writer(output)
    dates = data.get("dates", [])
    equity = data.get("equity_series", [])
    twr = data.get("twr_equity_series", [])
    dd = data.get("drawdown_series", [])

    writer.writerow(["Date", "Equity", "TWR_Index", "Drawdown_Pct"])
    for i, date in enumerate(dates):
        eq_val = equity[i] if i < len(equity) else ""
        twr_val = twr[i] if i < len(twr) else ""
        dd_val = dd[i] if i < len(dd) else ""
        writer.writerow([date, eq_val, twr_val, dd_val])

    return output.getvalue()


def generate_tear_sheet(
    client_ids: list[str],
    title: str = "Odum Capital — Strategy Performance",
) -> str:
    """Generate institutional tear sheet for one or more clients.

    Args:
        client_ids: List of client IDs to include (overlaid on same charts)
        title: Report title

    Returns:
        Path to generated HTML file.
    """
    clients_data: dict[str, _TearSheetData] = {}
    csv_data_map: dict[str, str] = {}

    for cid in client_ids:
        data = _compute_client_data(cid)
        if data:
            clients_data[cid] = data
            csv_data_map[cid] = _generate_csv_data(cid, data)

    if not clients_data:
        logger.warning("No data for clients: %s", client_ids)
        return ""

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    html = _TEAR_SHEET_TEMPLATE.render(
        title=title,
        clients=clients_data,
        client_names=CLIENT_NAMES,
        colours=_COLOURS,
        client_ids=client_ids,
        csv_data=csv_data_map,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )

    filename = "_".join(client_ids) + "_tear_sheet.html"
    path = _OUTPUT_DIR / filename
    path.write_text(html, encoding="utf-8")
    logger.info("Tear sheet: %s", path)
    return str(path)


# ── Template ──────────────────────────────────────────────────────────────────

_TEAR_SHEET_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  :root {
    --bg: #fff;
    --card: #fafbfc;
    --border: #e5e7eb;
    --text: #1a1a2e;
    --muted: #6b7280;
    --green: #059669;
    --red: #dc2626;
    --blue: #2563eb;
  }
  body {
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 13px;
    line-height: 1.5;
  }
  .container { max-width: 1200px; margin: 0 auto; padding: 40px 32px; }

  /* Header */
  .header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    padding-bottom: 24px;
    border-bottom: 2px solid var(--text);
    margin-bottom: 32px;
  }
  .header h1 { font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }
  .header .meta { font-size: 11px; color: var(--muted); text-align: right; }
  .header .meta .date { font-weight: 600; color: var(--text); }

  /* Stats Grid */
  .stats-section { margin-bottom: 32px; }
  .stats-section h2 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--muted);
    margin-bottom: 12px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .stat {
    background: var(--bg);
    padding: 14px 16px;
  }
  .stat .label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    margin-bottom: 4px;
  }
  .stat .value {
    font-size: 18px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .positive { color: var(--green); }
  .negative { color: var(--red); }

  /* Charts */
  .chart-section { margin-bottom: 28px; }
  .chart-section h2 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--muted);
    margin-bottom: 12px;
  }
  .chart-card {
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 20px;
    background: var(--bg);
  }
  .chart-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 20px;
  }
  canvas { width: 100% !important; }

  /* Monthly Returns Table */
  .monthly-section { margin-bottom: 32px; }
  .monthly-section h2 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--muted);
    margin-bottom: 12px;
  }
  .monthly-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .monthly-table th {
    padding: 8px 6px;
    text-align: center;
    font-weight: 600;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--muted);
    border-bottom: 2px solid var(--text);
  }
  .monthly-table td {
    padding: 6px;
    text-align: center;
    border-bottom: 1px solid var(--border);
  }
  .monthly-table td.pos { background: rgba(5,150,105,0.08); color: var(--green); }
  .monthly-table td.neg { background: rgba(220,38,38,0.06); color: var(--red); }
  .monthly-table td.year-total { font-weight: 700; border-left: 2px solid var(--text); }

  /* Download buttons */
  .actions {
    display: flex;
    gap: 8px;
    margin-bottom: 24px;
  }
  .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 500;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
    text-decoration: none;
    transition: all 0.15s;
  }
  .btn:hover { border-color: var(--blue); color: var(--blue); }

  /* Footer */
  .footer {
    margin-top: 48px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
    font-size: 10px;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
  }

  /* Print */
  @media print {
    body { font-size: 11px; }
    .container { padding: 20px; max-width: none; }
    .actions { display: none; }
    .chart-card { page-break-inside: avoid; }
    .monthly-section { page-break-inside: avoid; }
  }
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <h1>{{ title }}</h1>
      <div style="font-size: 12px; color: var(--muted); margin-top: 2px;">
        {% for cid in client_ids %}
        {{ client_names.get(cid, cid) }}{% if not loop.last %} &middot; {% endif %}
        {% endfor %}
      </div>
    </div>
    <div class="meta">
      <div class="date">{{ generated_at }}</div>
      <div>Confidential — For Authorized Recipients Only</div>
    </div>
  </div>

  <!-- Download Buttons -->
  <div class="actions">
    <button class="btn" onclick="window.print()">&#128424; Print / PDF</button>
    {% for cid in client_ids %}
    {% if cid in csv_data %}
    <a class="btn" id="csv-btn-{{ cid }}" href="#" onclick="downloadCSV('{{ cid }}'); return false;">&#128190; {{ client_names.get(cid, cid) }} Daily CSV</a>
    {% endif %}
    {% endfor %}
  </div>

  <!-- Stats per client -->
  {% for cid in client_ids %}
  {% if cid in clients %}
  {% set d = clients[cid] %}
  {% set s = d.stats %}
  <div class="stats-section">
    <h2>{{ client_names.get(cid, cid) }} — Performance Summary ({{ s.get("start_date", "") }} to {{ s.get("end_date", "") }})</h2>
    <div class="stats-grid">
      <div class="stat">
        <div class="label">Total Return (TWR)</div>
        <div class="value {{ 'positive' if s.get('total_return_pct', 0)|float >= 0 else 'negative' }}">{{ '{:.2f}'.format(s.get('total_return_pct', 0)|float) }}%</div>
      </div>
      <div class="stat">
        <div class="label">Annualised Return</div>
        <div class="value {{ 'positive' if s.get('annualized_return_pct', 0)|float >= 0 else 'negative' }}">{{ '{:.2f}'.format(s.get('annualized_return_pct', 0)|float) }}%</div>
      </div>
      <div class="stat">
        <div class="label">Max Drawdown</div>
        <div class="value negative">-{{ '{:.2f}'.format(s.get('max_drawdown_pct', 0)|float) }}%</div>
      </div>
      <div class="stat">
        <div class="label">Sharpe Ratio</div>
        <div class="value">{{ '{:.2f}'.format(s.get('sharpe_ratio', 0)|float) }}</div>
      </div>
      <div class="stat">
        <div class="label">Sortino Ratio</div>
        <div class="value">{{ '{:.2f}'.format(s.get('sortino_ratio', 0)|float) }}</div>
      </div>
      <div class="stat">
        <div class="label">Calmar Ratio</div>
        <div class="value">{{ '{:.2f}'.format(s.get('calmar_ratio', 0)|float) }}</div>
      </div>
      <div class="stat">
        <div class="label">Volatility (Ann.)</div>
        <div class="value">{{ '{:.2f}'.format(s.get('volatility_pct', 0)|float) }}%</div>
      </div>
      <div class="stat">
        <div class="label">Win Rate</div>
        <div class="value">{{ '{:.1f}'.format(s.get('win_rate_pct', 0)|float) }}%</div>
      </div>
      <div class="stat">
        <div class="label">Max DD Duration</div>
        <div class="value">{{ s.get('max_drawdown_duration_days', 0) }}d</div>
      </div>
      <div class="stat">
        <div class="label">Track Record</div>
        <div class="value">{{ s.get('equity_curve_days', 0) }}d</div>
      </div>
      <div class="stat">
        <div class="label">TWR HWM (Perf.)</div>
        <div class="value">{{ '{:.4f}'.format(s.get('high_water_mark_twr', 0)|float) }}</div>
      </div>
      <div class="stat">
        <div class="label">TWR Recovery</div>
        <div class="value">{% if s.get('twr_recovery_pct', 0)|float > 0 %}{{ '{:.2f}'.format(s.get('twr_recovery_pct', 0)|float) }}%{% else %}At HWM{% endif %}</div>
      </div>
      <div class="stat">
        <div class="label">Notional HWM</div>
        <div class="value">{{ d.unit }}{{ '{:,.6f}'.format(s.get('notional_hwm', 0)|float) if d.is_btc else '{:,.0f}'.format(s.get('notional_hwm', 0)|float) }}</div>
      </div>
      <div class="stat">
        <div class="label">Notional Recovery</div>
        <div class="value">{% if s.get('notional_recovery', 0)|float > 0 %}{{ d.unit }}{{ '{:,.6f}'.format(s.get('notional_recovery', 0)|float) if d.is_btc else '{:,.0f}'.format(s.get('notional_recovery', 0)|float) }} ({{ '{:.1f}'.format(s.get('notional_recovery_pct', 0)|float) }}%){% else %}At HWM{% endif %}</div>
      </div>
      {% if s.get('pnl_recovery_usd') is not none %}
      <div class="stat">
        <div class="label">PnL Recovery (USD)</div>
        <div class="value negative">${{ '{:,.0f}'.format(s.get('pnl_recovery_usd', 0)|float) }} ({{ '{:.1f}'.format(s.get('pnl_recovery_usd_pct', 0)|float) }}%)</div>
      </div>
      {% endif %}
    </div>
  </div>
  {% endif %}
  {% endfor %}

  <!-- Equity Curve (overlaid) -->
  <div class="chart-section">
    <h2>Equity Curve — TWR Indexed (Base = 1.0)</h2>
    <div class="chart-card">
      <canvas id="twrChart" height="300"></canvas>
    </div>
  </div>

  <!-- Drawdown + Rolling metrics -->
  <div class="chart-row">
    <div class="chart-card">
      <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);margin-bottom:8px;">Drawdown (%)</h2>
      <canvas id="ddChart" height="200"></canvas>
    </div>
    <div class="chart-card">
      <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);margin-bottom:8px;">Rolling 30-Day Sharpe</h2>
      <canvas id="sharpeChart" height="200"></canvas>
    </div>
  </div>

  <!-- Monthly Returns per client -->
  {% for cid in client_ids %}
  {% if cid in clients and clients[cid].monthly_matrix %}
  {% set d = clients[cid] %}
  <div class="monthly-section">
    <h2>{{ client_names.get(cid, cid) }} — Monthly Returns (%)</h2>
    <table class="monthly-table">
      <thead>
        <tr>
          <th>Year</th>
          <th>Jan</th><th>Feb</th><th>Mar</th><th>Apr</th><th>May</th><th>Jun</th>
          <th>Jul</th><th>Aug</th><th>Sep</th><th>Oct</th><th>Nov</th><th>Dec</th>
          <th>YTD</th>
        </tr>
      </thead>
      <tbody>
        {% for year in d.monthly_matrix.keys()|sort %}
        <tr>
          <td style="font-weight:600; text-align:left;">{{ year }}</td>
          {% for m in ['01','02','03','04','05','06','07','08','09','10','11','12'] %}
          {% set v = d.monthly_matrix[year][m] %}
          {% if v is not none %}
          <td class="{{ 'pos' if v >= 0 else 'neg' }}">{{ '{:+.1f}'.format(v) }}</td>
          {% else %}
          <td style="color:#ccc;">—</td>
          {% endif %}
          {% endfor %}
          <td class="year-total {{ 'pos' if d.year_totals.get(year, 0) >= 0 else 'neg' }}">
            {{ '{:+.1f}'.format(d.year_totals.get(year, 0)) }}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
  {% endfor %}

  <div class="footer">
    <div>Generated {{ generated_at }} &middot; Odum Capital Management</div>
    <div>Past performance is not indicative of future results. All returns are net of trading fees, gross of management/performance fees.</div>
  </div>

</div>

<script>
// Data injection
const clientData = {{ clients | tojson }};
const clientIds = {{ client_ids | tojson }};
const clientNames = {{ client_names | tojson }};
const colours = {{ colours | tojson }};
const csvData = {{ csv_data | tojson }};

// CSV download
function downloadCSV(cid) {
  const data = csvData[cid];
  if (!data) return;
  const blob = new Blob([data], {type: 'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = cid + '_daily_data.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// Chart defaults
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = '#6b7280';

// Build union of all dates (sorted) so each client aligns to its real dates
const allDatesSet = new Set();
clientIds.forEach(cid => {
  const d = clientData[cid];
  if (d) d.dates.forEach(dt => allDatesSet.add(dt));
});
const allDates = Array.from(allDatesSet).sort();

// Align a client's series to the union date axis (null where no data)
function alignSeries(dates, series) {
  const map = {};
  dates.forEach((dt, i) => { if (i < series.length) map[dt] = series[i]; });
  return allDates.map(dt => dt in map ? map[dt] : null);
}

// TWR Equity Curve (overlaid)
const twrCtx = document.getElementById('twrChart').getContext('2d');
const twrDatasets = [];
clientIds.forEach((cid, idx) => {
  const d = clientData[cid];
  if (!d) return;
  twrDatasets.push({
    label: clientNames[cid] || cid,
    data: alignSeries(d.dates, d.twr_equity_series),
    borderColor: colours[idx % colours.length],
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
    pointHitRadius: 8,
    tension: 0.3,
    spanGaps: false,
  });
});

new Chart(twrCtx, {
  type: 'line',
  data: { labels: allDates, datasets: twrDatasets },
  options: {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top', labels: { usePointStyle: true, padding: 16 } },
      tooltip: {
        callbacks: {
          label: ctx => ctx.parsed.y != null ? ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(4) : '',
        },
      },
    },
    scales: {
      x: { ticks: { maxTicksLimit: 12 }, grid: { display: false } },
      y: {
        ticks: { callback: v => v.toFixed(2) },
        grid: { color: 'rgba(0,0,0,0.05)' },
      },
    },
  },
});

// Drawdown Chart
const ddCtx = document.getElementById('ddChart').getContext('2d');
const ddDatasets = [];
clientIds.forEach((cid, idx) => {
  const d = clientData[cid];
  if (!d) return;
  ddDatasets.push({
    label: clientNames[cid] || cid,
    data: alignSeries(d.dates, d.drawdown_series),
    borderColor: colours[idx % colours.length],
    backgroundColor: colours[idx % colours.length] + '15',
    fill: true,
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.3,
    spanGaps: false,
  });
});
new Chart(ddCtx, {
  type: 'line',
  data: { labels: allDates, datasets: ddDatasets },
  options: {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: clientIds.length > 1, labels: { usePointStyle: true } } },
    scales: {
      x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } },
      y: {
        ticks: { callback: v => v.toFixed(1) + '%' },
        grid: { color: 'rgba(0,0,0,0.05)' },
      },
    },
  },
});

// Rolling Sharpe
const sharpeCtx = document.getElementById('sharpeChart').getContext('2d');
const sharpeDatasets = [];
clientIds.forEach((cid, idx) => {
  const d = clientData[cid];
  if (!d) return;
  sharpeDatasets.push({
    label: clientNames[cid] || cid,
    data: alignSeries(d.dates, d.rolling_sharpe),
    borderColor: colours[idx % colours.length],
    backgroundColor: 'transparent',
    borderWidth: 1.5,
    pointRadius: 0,
    tension: 0.3,
    spanGaps: true,
  });
});
// Zero line
sharpeDatasets.push({
  label: 'Zero',
  data: Array(allDates.length).fill(0),
  borderColor: '#e5e7eb',
  borderDash: [4, 4],
  borderWidth: 1,
  pointRadius: 0,
  fill: false,
});
new Chart(sharpeCtx, {
  type: 'line',
  data: { labels: allDates, datasets: sharpeDatasets },
  options: {
    responsive: true,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { maxTicksLimit: 8 }, grid: { display: false } },
      y: {
        ticks: { callback: v => v.toFixed(1) },
        grid: { color: 'rgba(0,0,0,0.05)' },
      },
    },
  },
});
</script>
</body>
</html>
""")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Default: generate for PR (best performing) and ODUM_PROP (longest track)
    _logger = logging.getLogger(__name__)
    _path = generate_tear_sheet(["PR", "ODUM_PROP"])
    _logger.info("Generated: %s", _path)
