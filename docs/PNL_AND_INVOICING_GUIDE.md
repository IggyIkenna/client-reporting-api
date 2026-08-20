---
doc_type: runbook
title: PnL, HWM & Invoicing — repo data-pipeline & code reference — client-reporting-api
execution:
  {
    owner: ikenna,
    cadence: hourly update + ad-hoc invoice issuance,
    verifier: ikenna + harsh (cross-operator),
    last_executed: 2026-07-28 (roster/fees operator-confirmed),
  }
---

# PnL, HWM & Invoicing — repo reference — client-reporting-api

> **The commercial model is NOT in this file** — the three HWM methods, per-client HWM nuances/seeds, fee structure, and
> the committed invoice/refund history all live in codex. Its canonical SSOT is
> [`/codex/14-customer-journeys/commercial-model/client-roster-and-fee-model.md`](../../unified-trading-pm/codex/14-customer-journeys/commercial-model/client-roster-and-fee-model.md).
> Grep codex for committed numbers — do not re-add the roster/fee/HWM/invoice tables here (S5.11: repo docs defer to the
> codex SSOT; if this file disagrees with codex, codex wins).
>
> This file carries only the repo-local **data pipeline, scripts, code-file map, and reporting outputs** for
> `client-reporting-api` — how the HWM math and invoices are actually produced and where the code lives.

---

## 1. Data Pipeline

### What's in GCS

```
gs://client-reporting-data-{project_id}/backfill/{client_id}/
  equity_curve.json    — daily snapshots (date, equity, balances, transfers)
  trades.json          — all executed trades/fills
  bills_ledger.json    — exchange income ledger (PnL, fees, funding)
  orders.json          — full order history
  balance.json         — current account balance
  positions.json       — current open positions
  transfers.json       — detected deposits/withdrawals
  summary.json         — metadata (venue, equity source, days tracked)
```

Local copies live in `data/backfill/{client_id}/` for development. (GCS bucket + Cloud Run scheduling detail lives in
[`CLIENT_OPERATIONS_GUIDE.md`](CLIENT_OPERATIONS_GUIDE.md).)

### Scripts

| Script                           | What It Does                                                                                   | When to Run                                    |
| -------------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `backfill_history.py`            | Full historical pull from exchange API (CCXT). Rebuilds all JSONs from scratch. ~3 min/client. | Initial setup, or when data needs full rebuild |
| `client-reporting-manage update` | Incremental update. Appends new equity points and trades. ~30s/client.                         | Hourly via cron or Cloud Scheduler             |
| `generate_full_audit.py`         | Generates audit HTML + tear sheets for all 10 accounts using production code paths.            | Ad-hoc, for validation/review                  |

### Running Locally

```bash
# Full backfill for one client
python scripts/backfill_history.py --client PR

# Full backfill for all clients
python scripts/backfill_history.py

# Incremental update (all active clients)
client-reporting-manage update

# Generate audit + tear sheets
python scripts/generate_full_audit.py
```

### Deployed (Cloud Run)

Two Cloud Run targets built from the same repo:

| Target                   | Image                           | Entry Point                           | Purpose                |
| ------------------------ | ------------------------------- | ------------------------------------- | ---------------------- |
| `client-reporting-api`   | `client-reporting-api:latest`   | `client-reporting` (FastAPI on :8080) | API server for UI      |
| `client-reporting-batch` | `client-reporting-batch:latest` | `client-reporting-manage update`      | Scheduled data refresh |

**CLI commands available in batch image:**

```bash
client-reporting-manage update [--client PR]    # incremental
client-reporting-manage backfill [--client PR]  # full rebuild
client-reporting-manage status                  # data freshness
client-reporting-manage onboard --client-id X --venue okx ...
```

### Credentials

Exchange API keys stored in Secret Manager (`exec-{client_id}-{venue}-{api-key|api-secret|passphrase}`); client config
in `execution-service/configs/credentials-registry.yaml`. See
[`CLIENT_OPERATIONS_GUIDE.md`](CLIENT_OPERATIONS_GUIDE.md) for the full credential/onboarding runbook.

## 2. HWM-Seed Code-File Map (repo-specific)

New clients with prior trading history before our equity-curve data starts need seeds added in code (the commercial
seed VALUES are in the codex roster/fee SSOT; this is where they wire in):

- **Equity-based HWM** → `hwm_seeds.py` → `_SEEDS` dict (value in native units). Used by `compute_performance_stats`
  for reporting.
- **PnL recovery** → `hwm_seeds.py` → `_PNL_RECOVERY_SEEDS` dict (`amount_usd` + `tracking_start`).
- **Full fee/invoice state** → `invoice_state.py` → `_HWM_SEED` dict (`trader_hwm`, `odum_hwm`, `credits`,
  `net_deposits`, etc.). This is the SSOT for FEE CALCULATIONS.

## 3. Reporting Outputs

### Tear Sheets (per client + combined overlay)

`data/tear_sheets/{client_id}_tear_sheet.html`

- Equity curve (TWR indexed, base 1.0)
- Performance stats grid (TWR return, annualised, max DD, Sharpe, Sortino, Calmar, volatility, win rate, drawdown
  duration, track record)
- TWR HWM + recovery, Notional HWM + recovery, PnL Recovery (if applicable)
- Monthly return heatmap (year x month)
- Drawdown chart, rolling 30-day Sharpe
- CSV download button for daily data

### Full Audit HTML

`data/audit/full_account_audit.html`

- Cross-account summary table (all 10 accounts side by side)
- Per-client detail sections: stats grid, flagged days (>25% daily return), canonical transfers, monthly returns, full
  daily equity table with TWR index, drawdown, transfers

### Monthly Reports

`data/reports/{CLIENT}_{YYYY-MM}_report.html`

- Executive summary, equity curve chart, coin-level PnL breakdown, daily PnL bars, transfer log, Odum branding

### Invoices

`data/invoices/{INVOICE_ID}.html`

- Full HWM math: previous HWM, current balance, gross profit, fee breakdown (Odum/trader/introducer), payment details,
  period covered

## 4. What's Working vs What's Manual

### Fully Automated

- TWR / Notional / PnL Recovery computation from the equity curve
- Tear sheet and audit HTML generation
- Invoice generation with HWM math
- Transfer detection (including day-1 deposits)
- Multi-client chart date alignment
- BTC/USDT denomination handling

### Manual Steps Required

- **Data refresh:** Run `backfill_history.py` or `client-reporting-manage update` to pull latest exchange data. Cloud
  Run Jobs automate this in production, but local dev requires manual runs.
- **HWM seeds:** New clients with prior history need seeds added to `hwm_seeds.py` and `invoice_state.py` (§2).
- **Invoice issuance:** Triggered manually via CLI or API. Not auto-scheduled.
- **Fund-of-fund NAV:** YOAV and GUY_ASRAF require manual NAV entry each period.
- **Fee rate changes:** Edit `credentials-registry.yaml`.

### Planned (Phase 2)

- GCS-backed invoice/HWM state persistence (currently hardcoded seeds)
- Live exchange data collection via `snapshot_collector.py` → Firestore
- Automated invoice scheduling
- RBAC with org-scoped visibility (client sees only their data)
- DocuSign integration for invoice signing

## 5. Known Issues / Things to Validate (code-level)

- The `_HWM_SEED` in `invoice_state.py` is the SSOT for fee calculations. The `_SEEDS` in `hwm_seeds.py` is used by
  `compute_performance_stats` for reporting. **These must stay in sync.**
- IK's pooled investor weights should be validated periodically (investors may add/withdraw capital, changing the
  split — weights in the codex roster/fee SSOT).
- Client-specific data caveats (GP PnL-recovery transfer edge cases, SL/SL2 TWR-vs-Notional divergence, ODUM_PROP short
  track record) are documented in the codex roster/fee SSOT § Per-client HWM nuances.
