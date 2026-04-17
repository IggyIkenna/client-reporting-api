# PnL, HWM & Invoicing Guide

> Odum Capital client reporting — how we track performance, calculate fees,
> and invoice clients. Written 2026-04-11.

---

## 1. Three HWM Methods

We track three different high-water marks simultaneously. Each tells a different
story. Per-client invoicing picks which one matters for fees.

### Method 1 — TWR HWM (Performance %)

**Question it answers:** "What % return does the trader need to make back?"

- Uses Time-Weighted Return index (unitless, base = 1.0 at inception)
- Deposits/withdrawals don't change the % recovery needed
- If you lose 10%, withdraw 90% of funds, you still only need 10% return
- Tracks pure trader performance — irrelevant of capital flows

**Fields:** `high_water_mark_twr`, `twr_recovery_pct`, `twr_recovery_amount`

### Method 2 — Notional HWM (Transfer-adjusted, native units)

**Question it answers:** "How much actual money/BTC needs to be recovered?"

- Starts at historical equity peak (or seed value)
- Deposits raise HWM, withdrawals lower it
- If you start at $100K, lose 10%, withdraw $80K, you have $10K and need $10K more (50% recovery)
- Tracks capital recovery in native units (USD or BTC)

**Fields:** `notional_hwm`, `notional_recovery`, `notional_recovery_pct`

### Method 3 — PnL Recovery (USDT, for pnl_based accounts)

**Question it answers:** "How much USDT trading P&L needs to be recovered?"

- For accounts where BTC balance changes are all transfers (no BTC trading)
- Only USDT trading generates P&L
- Recovery = seed_amount - USDT_balance_growth since tracking_start
- Stays in USDT regardless of account denomination — never convert to BTC

**Fields:** `pnl_recovery_usd`, `pnl_recovery_usd_pct`, `pnl_recovery_seed_usd`

### Why all three?

For unseeded accounts with no transfers, Methods 1 and 2 agree — a good
cross-check. For accounts with large deposits/withdrawals, they diverge and
each perspective is valid. Method 3 exists specifically for GP's invoicing model.

---

## 2. Per-Client Nuances

### At HWM (no seed needed)

| Client        | Venue   | Currency | Notes                                                                             |
| ------------- | ------- | -------- | --------------------------------------------------------------------------------- |
| **PR**        | OKX     | USDT     | Best performer. HWM bumped to $335,070 on Apr 9 invoice.                          |
| **NN**        | OKX     | USDT     | HWM bumped to $111,986 on Apr 9 invoice.                                          |
| **ET**        | Binance | USDT     | HWM bumped to $537,939 on Apr 9 invoice. Blue Coast introducer.                   |
| **STD**       | OKX     | USDT     | HWM bumped to $1,012,861 on Apr 9 invoice. Largest AUM.                           |
| **ODUM_PROP** | Binance | USDT     | Reference/prop account. No fees. ~49 days track record. TWR recovery 2.82% ($51). |

### Underwater — Equity HWM Seeds

These accounts had historical peaks before our equity curve data starts.
Seeds extracted from `invoice_state._HWM_SEED` as of 2026-02-17/19.

| Client  | Venue | Currency | HWM Seed  | TWR Recovery | Notional Recovery  |
| ------- | ----- | -------- | --------- | ------------ | ------------------ |
| **SL**  | OKX   | USDT     | $650,000  | 318.55%      | $492,861 (216.8%)  |
| **SL2** | OKX   | BTC      | 3.216 BTC | 430.29%      | 2.578 BTC (125.5%) |
| **ANU** | OKX   | BTC      | 1.01 BTC  | 53.13%       | 0.350 BTC (53.1%)  |
| **IK**  | OKX   | USDT     | $89,000   | 73.86%       | $37,809 (73.9%)    |

- SL/SL2: Same person (Shaun Lim), two accounts — USDT and BTC share classes
- ANU/IK: TWR and Notional agree closely (no large transfers distorting)
- SL/SL2: Methods diverge significantly (large historical transfers)
- All underwater accounts pay $50/month server cost instead of performance fees

### GP — PnL-Based Recovery (Special Case)

| Field             | Value                                            |
| ----------------- | ------------------------------------------------ |
| Venue             | OKX                                              |
| Currency          | BTC account, but P&L tracked in USDT             |
| HWM Model         | `pnl_based` (not equity-based)                   |
| PnL Recovery Seed | $75,000 USDT (was $80K, $5K credited Mar 2026)   |
| Tracking Start    | 2026-03-02 (after last USDT sweep of -$4,989.45) |
| Current Recovery  | ~$70,464 (16.2% of equity)                       |

**Why GP is different:**

- BTC balance changes are ALL transfers (no BTC trading)
- Only USDT trading generates P&L
- Recovery = $75K - cumulative USDT P&L since tracking start
- At tracking start: USDT balance $258. Current: ~$4,794. P&L earned: ~$4,536.
- Shows: TWR=At HWM, Notional=At HWM (in BTC terms), PnL Recovery=$70,464 USD
- Two voided invoices totalling $3,888 refunded. Trader credits: -$1,501.70

**Transfer resilience:** If GP withdraws $5K USDT, the balance drops but
the PnL recovery amount stays the same — the code detects USDT transfers
(>$500 jumps on transfer-flagged days) and subtracts them from the balance
change so only actual trading P&L counts.

### IK — Pooled Account

IK is a single OKX sub-account shared by three investors:

| Investor | Weight  |
| -------- | ------- |
| Jihane   | 25.344% |
| Amaka    | 21.6%   |
| IK       | 53.056% |

P&L and fees split proportionally by these weights.

### Fund-of-Fund Clients (Manual Entry)

| Client    | Currency | Odum Fee | Notes                            |
| --------- | -------- | -------- | -------------------------------- |
| YOAV      | BTC      | 20%      | NAV entered manually each period |
| GUY_ASRAF | BTC      | 20%      | NAV entered manually each period |

No exchange API. No trader fee (0%). DeFi BTC yield strategy.

---

## 3. Fee Structure

### 4-Tier HWM Model

| Tier           | Beneficiary    | Typical %         | Calculated On                           |
| -------------- | -------------- | ----------------- | --------------------------------------- |
| Trader Fee     | Desk trader    | 10%               | PnL above trader HWM                    |
| Odum Fee       | Odum Capital   | 20-35%            | PnL above Odum HWM                      |
| Introducer Fee | Introducer     | 5-15% of Odum fee | Triggered only if introducer configured |
| Server Cost    | Infrastructure | $50/month         | Only when account is underwater         |

### Per-Client Fee Rates

| Client    | Odum % | Trader % | Introducer        | Introducer % |
| --------- | ------ | -------- | ----------------- | ------------ |
| PR        | 34%    | 10%      | Max (Maxim Shilo) | 15% of Odum  |
| NN        | 30%    | 10%      | —                 | —            |
| ET        | 30%    | 10%      | Blue Coast        | 5% of Odum   |
| STD       | 35%    | 10%      | —                 | —            |
| GP        | 30%    | 10%      | —                 | —            |
| SL        | 30%    | 10%      | —                 | —            |
| SL2       | 30%    | 10%      | —                 | —            |
| ANU       | 30%    | 10%      | —                 | —            |
| IK        | 35%    | 10%      | —                 | —            |
| YOAV      | 20%    | 0%       | —                 | —            |
| GUY_ASRAF | 20%    | 0%       | —                 | —            |
| ODUM_PROP | 0%     | 0%       | —                 | —            |

### Dual HWM for Fees

Each client has two independent HWMs — trader and Odum. They can differ
because Odum may reset its HWM (e.g. after a refund) while the trader's
stays. This means the trader can earn fees even if Odum hasn't recouped yet,
and vice versa.

After an invoice is issued, both HWMs are bumped to the closing AUM for
that period.

---

## 4. Invoice Lifecycle

### States

| Status | Meaning                        | Action          |
| ------ | ------------------------------ | --------------- |
| ISSUED | Invoice sent, awaiting payment | Client pays     |
| PAID   | Payment received and confirmed | HWM bumped      |
| VOIDED | Cancelled/refunded             | Credits created |

### Generation Flow

1. Compute period P&L (equity_end - equity_start, transfer-adjusted)
2. Compare against trader HWM and Odum HWM
3. If profit above either HWM → calculate fees
4. Generate invoice HTML with full HWM math breakdown
5. Issue invoice (status=ISSUED)
6. On payment → mark PAID, bump HWMs to closing AUM
7. If refund needed → mark VOIDED, create trader credits

### Latest Invoice Run (Apr 9, 2026)

| Invoice          | Client          | Total     | PnL Above HWM |
| ---------------- | --------------- | --------- | ------------- |
| INV-2026-PR-002  | PR              | $2,954.60 | $8,690        |
| INV-2026-NN-002  | NN              | $1,075.80 | $3,586        |
| INV-2026-ET-002  | ET              | $5,681.70 | $18,939       |
| INV-2026-STD-002 | STD             | $4,458.65 | $12,739       |
| INT-MAX-002      | PR (introducer) | $443.00   | 15% of Odum   |
| INT-BC-001       | ET (introducer) | $284.00   | 5% of Odum    |

### Refund History

| Client | Voided Invoices            | Refunded Total | Trader Credits |
| ------ | -------------------------- | -------------- | -------------- |
| GP     | INV-2025-007, INV-2025-017 | $3,888         | -$1,501.70     |
| SL     | INV-2025-003, INV-2025-008 | $21,757        | -$3,949.93     |
| SL2    | INV-2025-004               | $8,308         | -$1,660.70     |
| ANU    | INV-2025-006, INV-2025-009 | $1,517         | -$309.90       |
| IK     | (none voided)              | $0             | -$1,241.18     |

---

## 5. Data Pipeline

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

Local copies live in `data/backfill/{client_id}/` for development.

### Scripts

| Script                   | What It Does                                                                                   | When to Run                                    |
| ------------------------ | ---------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `backfill_history.py`    | Full historical pull from exchange API (CCXT). Rebuilds all JSONs from scratch. ~3 min/client. | Initial setup, or when data needs full rebuild |
| `daily_update.py`        | Incremental update. Appends new equity points and trades. ~30s/client.                         | Hourly via cron or Cloud Scheduler             |
| `generate_full_audit.py` | Generates audit HTML + tear sheets for all 10 accounts using production code paths.            | Ad-hoc, for validation/review                  |

### Running Locally

```bash
# Full backfill for one client
python scripts/backfill_history.py --client PR

# Full backfill for all clients
python scripts/backfill_history.py

# Incremental update (all active clients)
python scripts/daily_update.py

# Generate audit + tear sheets
python scripts/generate_full_audit.py
```

### Deployed (Cloud Run)

Two Cloud Run targets built from the same repo:

| Target                   | Image                           | Entry Point                           | Purpose                |
| ------------------------ | ------------------------------- | ------------------------------------- | ---------------------- |
| `client-reporting-api`   | `client-reporting-api:latest`   | `client-reporting` (FastAPI on :8080) | API server for UI      |
| `client-reporting-batch` | `client-reporting-batch:latest` | `client-reporting-manage update`      | Scheduled data refresh |

**Cloud Run Jobs:**

- `{env}-client-reporting-update` — hourly (`5 * * * *`)
- `{env}-client-reporting-daily-snapshot` — daily (`15 0 * * *`)

**CLI commands available in batch image:**

```bash
client-reporting-manage update [--client PR]    # incremental
client-reporting-manage backfill [--client PR]  # full rebuild
client-reporting-manage status                  # data freshness
client-reporting-manage onboard --client-id X --venue okx ...
```

### Credentials

Exchange API keys stored in Secret Manager:

- `exec-{client_id}-{venue}-api-key`
- `exec-{client_id}-{venue}-api-secret`
- `exec-{client_id}-{venue}-passphrase` (OKX only)

Client config in `credentials-registry.yaml` — maps client IDs to venues,
currencies, strategies, fee structures, and Secret Manager key names.

---

## 6. Client Onboarding Lifecycle

### Step 1: Credentials

Client creates exchange sub-account with read-only + trading API key
(no withdrawal permissions).

- **OKX:** API key + secret + passphrase
- **Binance:** API key + secret (no passphrase)

### Step 2: Onboard via CLI

```bash
client-reporting-manage onboard \
  --client-id NEW_CLIENT \
  --venue okx \
  --api-key "..." \
  --api-secret "..." \
  --passphrase "..." \
  --full-name "Client Name" \
  --org-id new_org \
  --currency USDT \
  --strategy-id mean_reversion_top20
```

This validates the keys, stores them in Secret Manager, adds the client
to `credentials-registry.yaml` with default fees (30% Odum, 10% trader),
and runs a full backfill.

### Step 3: Configure Fees

Edit `credentials-registry.yaml` to set custom fee rates, add introducer
if applicable, mark `is_underwater` if starting below HWM.

### Step 4: Set HWM Seeds (if applicable)

If the client has prior trading history before our data starts, add their
historical HWM to `hwm_seeds.py`:

- Equity-based HWM → `_SEEDS` dict (value in native units)
- PnL recovery → `_PNL_RECOVERY_SEEDS` dict (amount_usd + tracking_start)

And to `invoice_state.py` → `_HWM_SEED` dict for the full fee/invoice
state (trader_hwm, odum_hwm, credits, net_deposits, etc.).

### Step 5: Ongoing

- Hourly updates via Cloud Run Job refresh the equity curve
- Monthly: generate invoices, issue to client
- On payment: mark PAID, bump HWMs
- If underwater: $50/month server cost until recovery

---

## 7. Reporting Outputs

### Tear Sheets (per client + combined overlay)

`data/tear_sheets/{client_id}_tear_sheet.html`

- Equity curve (TWR indexed, base 1.0)
- Performance stats grid (TWR return, annualised, max DD, Sharpe, Sortino,
  Calmar, volatility, win rate, drawdown duration, track record)
- TWR HWM + recovery, Notional HWM + recovery, PnL Recovery (if applicable)
- Monthly return heatmap (year x month)
- Drawdown chart, rolling 30-day Sharpe
- CSV download button for daily data

### Full Audit HTML

`data/audit/full_account_audit.html`

- Cross-account summary table (all 10 accounts side by side)
- Per-client detail sections: stats grid, flagged days (>25% daily return),
  canonical transfers, monthly returns, full daily equity table with TWR
  index, drawdown, transfers

### Monthly Reports

`data/reports/{CLIENT}_{YYYY-MM}_report.html`

- Executive summary, equity curve chart, coin-level PnL breakdown,
  daily PnL bars, transfer log, Odum branding

### Invoices

`data/invoices/{INVOICE_ID}.html`

- Full HWM math: previous HWM, current balance, gross profit, fee breakdown
  (Odum/trader/introducer), payment details, period covered

---

## 8. What's Working vs What's Manual

### Fully Automated

- TWR / Notional / PnL Recovery computation from equity curve
- Tear sheet and audit HTML generation
- Invoice generation with HWM math
- Transfer detection (including day-1 deposits)
- Multi-client chart date alignment
- BTC/USDT denomination handling

### Manual Steps Required

- **Data refresh:** Run `backfill_history.py` or `daily_update.py` to pull
  latest exchange data. Cloud Run Jobs automate this in production, but
  local dev requires manual runs.
- **HWM seeds:** New clients with prior history need seeds added to
  `hwm_seeds.py` and `invoice_state.py`
- **Invoice issuance:** Triggered manually via CLI or API. Not auto-scheduled.
- **Fund-of-fund NAV:** YOAV and GUY_ASRAF require manual NAV entry each period.
- **Fee rate changes:** Edit `credentials-registry.yaml`

### Planned (Phase 2)

- GCS-backed invoice/HWM state persistence (currently hardcoded seeds)
- Live exchange data collection via `snapshot_collector.py` → Firestore
- Automated invoice scheduling
- RBAC with org-scoped visibility (client sees only their data)
- DocuSign integration for invoice signing

---

## 9. Known Issues / Things to Validate

- GP's PnL recovery assumes no USDT transfers after tracking start (Mar 2).
  If GP makes a USDT deposit/withdrawal, the code detects jumps >$500 on
  transfer-flagged days, but edge cases (many small transfers) could slip through.
- SL/SL2 have large TWR vs Notional divergence (318% vs 217% for SL). This is
  expected due to large historical transfers but worth spot-checking.
- ODUM_PROP has only 49 days of data. Its 2.82% TWR recovery is real but based
  on a short track record.
- The `_HWM_SEED` in `invoice_state.py` is the SSOT for fee calculations.
  The `_SEEDS` in `hwm_seeds.py` is used by `compute_performance_stats` for
  reporting. These must stay in sync.
- IK's pooled investor weights should be validated periodically (investors may
  add/withdraw capital, changing the split).
