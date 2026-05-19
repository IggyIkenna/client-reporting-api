"""Generate comprehensive audit HTML for ALL accounts.

Uses the exact same production code paths:
- backfill_store.get_equity_curve / compute_performance_stats / compute_monthly_returns
- transfer_store.get_transfers / get_net_deposits / get_daily_transfers
- tear_sheet_generator.generate_tear_sheet

Outputs:
1. Individual tear sheets per client
2. Combined tear sheet (all 10 overlaid)
3. Comprehensive audit HTML showing raw data, balances, transfers, returns
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client_reporting_api.core.backfill_store import (
    _get_equity,
    _get_transfer,
    _is_btc_account,
    compute_monthly_returns,
    compute_performance_stats,
    get_backfill_summary,
    get_equity_curve,
)
from client_reporting_api.core.hwm_seeds import get_hwm_seed, get_pnl_recovery_seed
from client_reporting_api.core.pnl_chart_generator import CLIENT_NAMES
from client_reporting_api.core.tear_sheet_generator import generate_tear_sheet
from client_reporting_api.core.transfer_store import (
    get_daily_transfers,
    get_net_deposits,
    get_transfers,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALL_CLIENTS = ["PR", "NN", "ET", "STD", "GP", "SL", "SL2", "ANU", "IK", "ODUM_PROP"]
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "audit"


def _fmt(val: float, is_btc: bool) -> str:
    """Format value based on denomination."""
    if is_btc:
        return f"{val:.6f}"
    return f"{val:,.2f}"


def _sign_class(val: float) -> str:
    """CSS class for positive/negative."""
    if val > 0:
        return "pos"
    if val < 0:
        return "neg"
    return ""


def build_audit_html() -> str:
    """Build comprehensive audit HTML for all accounts."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # Collect all data
    all_data: dict[str, dict] = {}
    for cid in ALL_CLIENTS:
        curve = get_equity_curve(cid)
        if not curve:
            continue

        is_btc = _is_btc_account(curve)
        first_date = str(curve[0].get("date", ""))
        stats = compute_performance_stats(
            curve,
            hwm_seed=get_hwm_seed(cid),
            pnl_recovery=get_pnl_recovery_seed(cid),
        )
        monthly = compute_monthly_returns(curve)
        transfers = get_transfers(cid)
        net_deps = get_net_deposits(cid)
        daily_xfers = get_daily_transfers(cid)
        summary = get_backfill_summary(cid)

        # Build daily equity table data
        daily_rows = []
        twr = 1.0
        peak = 1.0
        cum_xfer = 0.0
        for i, point in enumerate(curve):
            date = str(point.get("date", ""))
            eq = _get_equity(point, is_btc)
            day_ret = 0.0
            xfer = 0.0
            if i > 0:
                eq_prev = _get_equity(curve[i - 1], is_btc)
                xfer = _get_transfer(point, curve[i - 1], is_btc, first_date)
                cum_xfer += xfer
                if eq_prev > 0 and eq > 0:
                    day_ret = (eq - xfer) / eq_prev - 1.0
                twr *= 1.0 + day_ret

            if twr > peak:
                peak = twr
            dd = (peak - twr) / peak * 100 if peak > 0 else 0

            row = {
                "date": date,
                "equity": eq,
                "twr": twr,
                "dd_pct": -dd,
                "day_ret_pct": day_ret * 100,
                "transfer": xfer,
                "cum_transfer": cum_xfer,
            }
            # Add raw balance fields if present
            if "btc_balance" in point:
                row["btc_balance"] = float(point.get("btc_balance", 0))
            if "usdt_balance" in point:
                row["usdt_balance"] = float(point.get("usdt_balance", 0))
            if "btc_price_usd" in point:
                row["btc_price"] = float(point.get("btc_price_usd", 0))
            if "equity_usd" in point:
                row["equity_usd"] = float(point.get("equity_usd", 0))

            daily_rows.append(row)

        all_data[cid] = {
            "curve": curve,
            "is_btc": is_btc,
            "stats": stats,
            "monthly": monthly,
            "transfers": transfers,
            "net_deposits": net_deps,
            "daily_transfers": daily_xfers,
            "summary": summary,
            "daily_rows": daily_rows,
            "name": CLIENT_NAMES.get(cid, cid),
            "denomination": "BTC" if is_btc else "USD",
            "unit": "\u20bf" if is_btc else "$",
        }

    # Build HTML
    sections = []

    # Summary table across all accounts
    sections.append(_build_summary_table(all_data))

    # Per-client detailed sections
    for cid in ALL_CLIENTS:
        if cid not in all_data:
            continue
        sections.append(_build_client_section(cid, all_data[cid]))

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Full Account Audit — All Strategies</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  :root {{
    --bg: #fff; --card: #fafbfc; --border: #e5e7eb;
    --text: #1a1a2e; --muted: #6b7280;
    --green: #059669; --red: #dc2626; --blue: #2563eb;
    --yellow: #d97706;
  }}
  body {{
    font-family: 'Inter', -apple-system, sans-serif;
    background: var(--bg); color: var(--text);
    font-size: 12px; line-height: 1.5;
  }}
  .container {{ max-width: 1400px; margin: 0 auto; padding: 32px; }}
  h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
  .subtitle {{ font-size: 12px; color: var(--muted); margin-bottom: 24px; }}
  h2 {{
    font-size: 16px; font-weight: 700; margin: 32px 0 12px;
    padding-bottom: 6px; border-bottom: 2px solid var(--text);
  }}
  h3 {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px;
    color: var(--muted); margin: 20px 0 8px;
    padding-bottom: 4px; border-bottom: 1px solid var(--border);
  }}
  table {{
    width: 100%; border-collapse: collapse;
    font-size: 11px; font-variant-numeric: tabular-nums;
    margin-bottom: 16px;
  }}
  th {{
    padding: 6px 8px; text-align: right; font-weight: 600;
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--muted); border-bottom: 2px solid var(--text);
    position: sticky; top: 0; background: var(--bg);
  }}
  th:first-child {{ text-align: left; }}
  td {{
    padding: 4px 8px; text-align: right;
    border-bottom: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace; font-size: 10.5px;
  }}
  td:first-child {{ text-align: left; font-family: 'Inter', sans-serif; font-weight: 500; }}
  .pos {{ color: var(--green); }}
  .neg {{ color: var(--red); }}
  .warn {{ color: var(--yellow); font-weight: 600; }}
  .flag {{ background: #fef3c7; }}
  .xfer-row {{ background: #eff6ff; }}
  .summary-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1px; background: var(--border);
    border: 1px solid var(--border); border-radius: 6px;
    overflow: hidden; margin-bottom: 16px;
  }}
  .stat {{
    background: var(--bg); padding: 10px 12px;
  }}
  .stat .label {{
    font-size: 9px; text-transform: uppercase; letter-spacing: 0.8px;
    color: var(--muted); margin-bottom: 2px;
  }}
  .stat .value {{
    font-size: 16px; font-weight: 700;
    font-variant-numeric: tabular-nums;
  }}
  .client-section {{
    page-break-before: always;
    border-top: 3px solid var(--text);
    margin-top: 40px; padding-top: 16px;
  }}
  .client-section:first-of-type {{ border-top: none; margin-top: 0; }}
  .scroll-table {{
    max-height: 400px; overflow-y: auto;
    border: 1px solid var(--border); border-radius: 4px;
  }}
  .footer {{
    margin-top: 48px; padding-top: 16px;
    border-top: 1px solid var(--border);
    font-size: 10px; color: var(--muted);
  }}
  @media print {{
    body {{ font-size: 9px; }}
    .container {{ padding: 12px; max-width: none; }}
    .scroll-table {{ max-height: none; overflow: visible; }}
    .client-section {{ page-break-before: always; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>Odum Capital — Full Account Audit</h1>
  <div class="subtitle">
    Generated {now} &middot; All 10 strategies &middot;
    Production data pipeline (backfill_store + transfer_store)
  </div>

  {"".join(sections)}

  <div class="footer">
    Generated {now} &middot; Odum Capital Management &middot;
    Data source: backfill_store.get_equity_curve + transfer_store.get_transfers &middot;
    TWR = Time-Weighted Return (transfer-adjusted) &middot;
    Past performance is not indicative of future results.
  </div>
</div>
</body>
</html>"""

    return html


def _build_summary_table(all_data: dict[str, dict]) -> str:
    """Build cross-account summary comparison table."""
    rows = []
    for cid in ALL_CLIENTS:
        if cid not in all_data:
            continue
        d = all_data[cid]
        s = d["stats"]
        nd = d["net_deposits"]
        is_btc = d["is_btc"]
        unit = d["unit"]

        total_ret = float(s.get("total_return_pct", 0))
        ann_ret = float(s.get("annualized_return_pct", 0))
        max_dd = float(s.get("max_drawdown_pct", 0))
        sharpe = float(s.get("sharpe_ratio", 0))
        sortino = float(s.get("sortino_ratio", 0))
        vol = float(s.get("volatility_pct", 0))
        win = float(s.get("win_rate_pct", 0))
        hwm_twr = float(s.get("high_water_mark_twr", 0))
        twr_rec_pct = float(s.get("twr_recovery_pct", 0))
        twr_rec_amt = float(s.get("twr_recovery_amount", 0))
        not_hwm = float(s.get("notional_hwm", 0))
        not_rec = float(s.get("notional_recovery", 0))
        not_rec_pct = float(s.get("notional_recovery_pct", 0))
        days = int(s.get("equity_curve_days", 0))

        # Current equity (last point)
        last_eq = d["daily_rows"][-1]["equity"] if d["daily_rows"] else 0
        first_eq = d["daily_rows"][0]["equity"] if d["daily_rows"] else 0

        # Transfers
        n_xfers = len(d["transfers"])
        net_dep = float(nd.get("net_deposits_usd", 0))

        twr_str = f"{twr_rec_pct:.2f}%" if twr_rec_pct > 0 else "At HWM"
        not_str = f"{unit}{_fmt(not_rec, is_btc)} ({not_rec_pct:.1f}%)" if not_rec > 0 else "At HWM"

        # PnL recovery (USD) for pnl_based accounts
        pnl_rec = s.get("pnl_recovery_usd")
        pnl_rec_pct = s.get("pnl_recovery_usd_pct")
        pnl_str = f"${pnl_rec:,.0f} ({pnl_rec_pct:.1f}%)" if pnl_rec is not None else ""

        rows.append(f"""\
    <tr>
      <td>{d["name"]}</td>
      <td>{cid}</td>
      <td>{d["denomination"]}</td>
      <td>{days}</td>
      <td>{unit}{_fmt(first_eq, is_btc)}</td>
      <td>{unit}{_fmt(last_eq, is_btc)}</td>
      <td>{hwm_twr:.4f}</td>
      <td>{twr_str}</td>
      <td>{unit}{_fmt(not_hwm, is_btc)}</td>
      <td>{not_str}</td>
      <td>{pnl_str}</td>
      <td class="{_sign_class(total_ret)}">{total_ret:+.2f}%</td>
      <td class="{_sign_class(ann_ret)}">{ann_ret:+.2f}%</td>
      <td class="neg">-{max_dd:.2f}%</td>
      <td>{sharpe:.2f}</td>
      <td>{n_xfers}</td>
      <td>${net_dep:,.0f}</td>
    </tr>""")

    return f"""\
  <h3>Cross-Account Summary</h3>
  <div class="scroll-table" style="max-height:none;">
  <table>
    <thead>
      <tr>
        <th style="text-align:left;">Name</th>
        <th>ID</th>
        <th>Denom</th>
        <th>Days</th>
        <th>Opening</th>
        <th>Current</th>
        <th>TWR HWM</th>
        <th>TWR Rec.</th>
        <th>Notional HWM</th>
        <th>Notional Rec.</th>
        <th>PnL Rec. USD</th>
        <th>TWR Return</th>
        <th>Ann. Return</th>
        <th>Max DD</th>
        <th>Sharpe</th>
        <th>Xfers</th>
        <th>Net Deps USD</th>
      </tr>
    </thead>
    <tbody>
{"".join(rows)}
    </tbody>
  </table>
  </div>
"""


def _build_client_section(cid: str, d: dict) -> str:
    """Build detailed section for one client."""
    s = d["stats"]
    is_btc = d["is_btc"]
    unit = d["unit"]
    denom = d["denomination"]
    parts = []

    parts.append(f"""\
  <div class="client-section">
  <h2>{d["name"]} ({cid}) — {denom} Account</h2>
""")

    # Stats grid
    total_ret = float(s.get("total_return_pct", 0))
    ann_ret = float(s.get("annualized_return_pct", 0))
    max_dd = float(s.get("max_drawdown_pct", 0))
    sharpe = float(s.get("sharpe_ratio", 0))
    sortino = float(s.get("sortino_ratio", 0))
    calmar = float(s.get("calmar_ratio", 0))
    vol = float(s.get("volatility_pct", 0))
    win = float(s.get("win_rate_pct", 0))
    dd_dur = int(s.get("max_drawdown_duration_days", 0))
    days = int(s.get("equity_curve_days", 0))
    hwm_twr = float(s.get("high_water_mark_twr", 0))
    twr_rec_pct = float(s.get("twr_recovery_pct", 0))
    twr_rec_amt = float(s.get("twr_recovery_amount", 0))
    not_hwm = float(s.get("notional_hwm", 0))
    not_rec = float(s.get("notional_recovery", 0))
    not_rec_pct = float(s.get("notional_recovery_pct", 0))
    total_deps = float(s.get("total_deposits", 0))
    total_wds = float(s.get("total_withdrawals", 0))
    pnl_rec_usd = s.get("pnl_recovery_usd")
    pnl_rec_usd_pct = s.get("pnl_recovery_usd_pct")
    pnl_seed_usd = s.get("pnl_recovery_seed_usd")

    venue = d["summary"].get("venue", "unknown") if d["summary"] else "unknown"
    eq_source = d["summary"].get("equity_source", "unknown") if d["summary"] else "unknown"

    # Build PnL recovery stat card (only for pnl_based accounts)
    pnl_rec_card = ""
    if pnl_rec_usd is not None:
        pnl_rec_card = (
            f'    <div class="stat"><div class="label">PnL Recovery (USD)</div>'
            f'<div class="value neg" style="font-size:13px;">'
            f"${pnl_rec_usd:,.0f} ({pnl_rec_usd_pct:.1f}%)"
            f" — seed ${pnl_seed_usd:,.0f}</div></div>\n"
        )

    parts.append(f"""\
  <div class="summary-grid">
    <div class="stat"><div class="label">Venue</div><div class="value">{venue.upper()}</div></div>
    <div class="stat"><div class="label">Equity Source</div><div class="value" style="font-size:12px;">{eq_source}</div></div>
    <div class="stat"><div class="label">TWR Return</div><div class="value {_sign_class(total_ret)}">{total_ret:+.2f}%</div></div>
    <div class="stat"><div class="label">Annualised</div><div class="value {_sign_class(ann_ret)}">{ann_ret:+.2f}%</div></div>
    <div class="stat"><div class="label">Max Drawdown</div><div class="value neg">-{max_dd:.2f}%</div></div>
    <div class="stat"><div class="label">Sharpe</div><div class="value">{sharpe:.2f}</div></div>
    <div class="stat"><div class="label">Sortino</div><div class="value">{sortino:.2f}</div></div>
    <div class="stat"><div class="label">Calmar</div><div class="value">{calmar:.2f}</div></div>
    <div class="stat"><div class="label">Volatility</div><div class="value">{vol:.1f}%</div></div>
    <div class="stat"><div class="label">Win Rate</div><div class="value">{win:.1f}%</div></div>
    <div class="stat"><div class="label">Max DD Duration</div><div class="value">{dd_dur}d</div></div>
    <div class="stat"><div class="label">Track Record</div><div class="value">{days}d</div></div>
    <div class="stat"><div class="label">TWR HWM (Perf.)</div><div class="value">{hwm_twr:.4f}</div></div>
    <div class="stat"><div class="label">TWR Recovery</div><div class="value" style="font-size:13px;">{"At HWM" if twr_rec_pct == 0 else f"{twr_rec_pct:.2f}% ({unit}{_fmt(twr_rec_amt, is_btc)})"}</div></div>
    <div class="stat"><div class="label">Notional HWM</div><div class="value" style="font-size:13px;">{unit}{_fmt(not_hwm, is_btc)}</div></div>
    <div class="stat"><div class="label">Notional Recovery</div><div class="value" style="font-size:13px;">{"At HWM" if not_rec == 0 else f"{unit}{_fmt(not_rec, is_btc)} ({not_rec_pct:.1f}%)"}</div></div>
{pnl_rec_card}\
    <div class="stat"><div class="label">Total Deposits ({denom})</div><div class="value" style="font-size:13px;">{unit}{_fmt(total_deps, is_btc)}</div></div>
    <div class="stat"><div class="label">Total Withdrawals ({denom})</div><div class="value" style="font-size:13px;">{unit}{_fmt(abs(total_wds), is_btc)}</div></div>
  </div>
""")

    # Flagged days
    flagged = s.get("flagged_days", [])
    if flagged:
        parts.append("""\
  <h3>Flagged Days (>25% daily return — possible missing transfers)</h3>
  <table>
    <thead><tr><th style="text-align:left;">Date</th><th>Daily Return</th><th>Equity</th><th>Prev Equity</th><th>Detected Transfer</th></tr></thead>
    <tbody>
""")
        for f in flagged:
            ret = float(f.get("daily_return_pct", 0))
            parts.append(f"""\
      <tr class="flag">
        <td>{f.get("date", "")}</td>
        <td class="warn">{ret:+.2f}%</td>
        <td>{unit}{f.get("equity", 0)}</td>
        <td>{unit}{f.get("prev_equity", 0)}</td>
        <td>{unit}{f.get("detected_transfer", 0)}</td>
      </tr>
""")
        parts.append("    </tbody></table>\n")

    # Transfers
    transfers = d["transfers"]
    if transfers:
        parts.append(f"""\
  <h3>Canonical Transfers ({len(transfers)} records via transfer_store)</h3>
  <table>
    <thead><tr>
      <th style="text-align:left;">Date</th><th>Direction</th><th>Currency</th>
      <th>Amount</th><th>USD Equiv</th><th>Venue</th><th>Source</th>
    </tr></thead>
    <tbody>
""")
        for t in transfers:
            parts.append(f"""\
      <tr class="xfer-row">
        <td>{t.timestamp.strftime("%Y-%m-%d")}</td>
        <td class="{"pos" if t.direction == "deposit" else "neg"}">{t.direction.upper()}</td>
        <td>{t.currency}</td>
        <td>{t.amount}</td>
        <td>${float(t.usd_amount):,.2f}</td>
        <td>{t.venue}</td>
        <td>{t.transfer_id[:20]}</td>
      </tr>
""")
        parts.append("    </tbody></table>\n")

        # Net deposits summary
        nd = d["net_deposits"]
        parts.append(f"""\
  <div class="summary-grid" style="max-width:600px;">
    <div class="stat"><div class="label">Total Deposits USD</div><div class="value pos">${float(nd.get("total_deposits_usd", 0)):,.0f}</div></div>
    <div class="stat"><div class="label">Total Withdrawals USD</div><div class="value neg">${float(nd.get("total_withdrawals_usd", 0)):,.0f}</div></div>
    <div class="stat"><div class="label">Net Deposits USD</div><div class="value">${float(nd.get("net_deposits_usd", 0)):,.0f}</div></div>
    <div class="stat"><div class="label">Primary Currency</div><div class="value">{nd.get("primary_currency", "N/A")}</div></div>
  </div>
""")
    else:
        parts.append(
            "  <h3>Canonical Transfers</h3>\n  <p style='color:var(--muted);'>No transfers detected (initial deposit on first day is implicit).</p>\n"
        )

    # Monthly returns
    monthly = d["monthly"]
    if monthly:
        parts.append("""\
  <h3>Monthly Returns (TWR)</h3>
  <table>
    <thead><tr><th style="text-align:left;">Month</th><th>Return %</th></tr></thead>
    <tbody>
""")
        for m in monthly:
            ret = float(m["return_pct"])
            parts.append(f"""\
      <tr><td>{m["month"]}</td><td class="{_sign_class(ret)}">{ret:+.2f}%</td></tr>
""")
        parts.append("    </tbody></table>\n")

    # Daily equity table (scrollable)
    rows = d["daily_rows"]
    has_btc_fields = any("btc_balance" in r for r in rows)

    parts.append(f"""\
  <h3>Daily Equity Curve ({len(rows)} days)</h3>
  <div class="scroll-table">
  <table>
    <thead><tr>
      <th style="text-align:left;">Date</th>
      <th>Equity ({denom})</th>
""")
    if has_btc_fields:
        parts.append("      <th>BTC Bal</th><th>USDT Bal</th><th>BTC Price</th>\n")
    parts.append("""\
      <th>TWR Index</th>
      <th>Drawdown %</th>
      <th>Daily Ret %</th>
      <th>Transfer</th>
      <th>Cum Transfer</th>
""")
    if not is_btc:
        parts.append("      <th>Equity USD</th>\n")
    parts.append("    </tr></thead>\n    <tbody>\n")

    for r in rows:
        xfer = r["transfer"]
        is_xfer_day = abs(xfer) > 0.001
        row_class = ' class="xfer-row"' if is_xfer_day else ""
        day_ret = r["day_ret_pct"]
        is_flagged = abs(day_ret) > 25
        ret_class = "warn" if is_flagged else _sign_class(day_ret)

        parts.append(f"      <tr{row_class}>\n")
        parts.append(f"        <td>{r['date']}</td>\n")
        parts.append(f"        <td>{unit}{_fmt(r['equity'], is_btc)}</td>\n")
        if has_btc_fields:
            btc_b = r.get("btc_balance", "")
            usdt_b = r.get("usdt_balance", "")
            btc_p = r.get("btc_price", "")
            parts.append(f"        <td>{f'{btc_b:.6f}' if btc_b != '' else ''}</td>\n")
            parts.append(f"        <td>{f'{usdt_b:,.2f}' if usdt_b != '' else ''}</td>\n")
            parts.append(f"        <td>{f'${btc_p:,.0f}' if btc_p != '' else ''}</td>\n")
        parts.append(f"        <td>{r['twr']:.6f}</td>\n")
        parts.append(f'        <td class="{_sign_class(r["dd_pct"])}">{r["dd_pct"]:.2f}%</td>\n')
        parts.append(f'        <td class="{ret_class}">{day_ret:+.4f}%</td>\n')
        xfer_str = _fmt(xfer, is_btc) if is_xfer_day else ""
        parts.append(f'        <td class="{"pos" if xfer > 0 else "neg" if xfer < 0 else ""}">{xfer_str}</td>\n')
        parts.append(f"        <td>{_fmt(r['cum_transfer'], is_btc)}</td>\n")
        if not is_btc and "equity_usd" in r:
            parts.append(f"        <td>${r['equity_usd']:,.2f}</td>\n")
        parts.append("      </tr>\n")

    parts.append("    </tbody>\n  </table>\n  </div>\n")
    parts.append("  </div>\n")

    return "".join(parts)


def main() -> None:
    """Generate all outputs."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Comprehensive audit HTML
    logger.info("Building comprehensive audit HTML for all 10 accounts...")
    html = build_audit_html()
    audit_path = OUTPUT_DIR / "full_account_audit.html"
    audit_path.write_text(html, encoding="utf-8")
    logger.info("Audit HTML: %s", audit_path)

    # 2. Individual tear sheets
    for cid in ALL_CLIENTS:
        curve = get_equity_curve(cid)
        if curve:
            path = generate_tear_sheet(
                [cid],
                title=f"{CLIENT_NAMES.get(cid, cid)} — Strategy Performance",
            )
            if path:
                logger.info("Tear sheet: %s -> %s", cid, path)

    # 3. Combined tear sheet (all 10)
    path = generate_tear_sheet(
        ALL_CLIENTS,
        title="Odum Capital — All Strategies Performance",
    )
    if path:
        logger.info("Combined tear sheet: %s", path)

    print(f"\n{'=' * 60}")
    print("AUDIT COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Audit HTML:      {audit_path}")
    print(f"  Tear sheets:     {OUTPUT_DIR.parent / 'tear_sheets'}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
