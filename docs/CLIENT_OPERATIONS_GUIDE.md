---
doc_type: runbook
title: Client Reporting Operations Guide — client-reporting-api
execution:
  {
    owner: ikenna,
    cadence: hourly (:05 update job) + daily (00:15 UTC snapshot),
    verifier: ikenna + harsh (cross-operator),
    last_executed: 2026-07-28 (roster/fees operator-confirmed),
  }
---

# Client Reporting Operations Guide — client-reporting-api

> **Commercial facts (roster / org hierarchy / per-client fees / tranches / HWM invoicing model) are NOT in this file.**
> Their canonical SSOT is
> [`/codex/14-customer-journeys/commercial-model/client-roster-and-fee-model.md`](../../unified-trading-pm/codex/14-customer-journeys/commercial-model/client-roster-and-fee-model.md).
> Grep codex for committed numbers — do not re-add the roster/fee tables here (S5.11: repo docs defer to the codex SSOT,
> and if this file disagrees with codex, codex wins). The **live machine config** for per-client fee %s / tranche /
> pooled weights / Secret-Manager keys is `execution-service/configs/credentials-registry.yaml`.
>
> This file is the repo-local **operations runbook**: the exchange-key setup, onboarding CLI, backfill, hourly/daily
> update jobs, GCS persistence layout, Cloud Run scheduling, and troubleshooting — the parts that are specific to
> operating `client-reporting-api`.

---

## Table of Contents

1. [Exchange API Keys (Binance & OKX)](#exchange-api-keys-binance--okx)
2. [Client Onboarding](#client-onboarding)
3. [Backfill Process](#backfill-process)
4. [Hourly Update](#hourly-update)
5. [Daily Full Snapshot](#daily-full-snapshot)
6. [Data Persistence (GCS)](#data-persistence-gcs)
7. [Cloud Run Jobs & Scheduling](#cloud-run-jobs--scheduling)
8. [Credential Registry Reference](#credential-registry-reference)
9. [Troubleshooting](#troubleshooting)

> **Roster / strategies / fee structure / invoicing** → see the codex SSOT linked above.

---

## Exchange API Keys (Binance & OKX)

### What We Need from the Client

The client creates a **read-only + trading** sub-account API key on their exchange. We never hold withdrawal permissions.

#### OKX

1. Client logs into OKX web → **Profile → API → Create API Key**
2. Settings:
   - **Permissions**: Read + Trade (NOT Withdraw)
   - **IP whitelist**: Our Cloud Run egress IPs (provided during onboarding)
   - **Passphrase**: Client sets a passphrase (OKX-specific, required for all API calls)
3. Client sends us 3 values:
   - `API Key` (e.g. `a1b2c3d4-...`)
   - `Secret Key` (e.g. `ABCDEF123456...`)
   - `Passphrase` (e.g. `MyP@ssphrase!`)

**OKX-specific notes:**

- OKX requires a passphrase on every authenticated request
- Max 100 items per page on order/trade history endpoints
- Uses `fetchClosedOrders()` + `fetchOpenOrders()` (not `fetchOrders()` which is unsupported)
- Bills ledger (`/api/v5/account/bills`) for P&L data

#### Binance

1. Client logs into Binance web → **Profile → API Management → Create API**
2. Settings:
   - **Permissions**: Enable Futures (Read + Trade). Do NOT enable Withdraw or Transfer.
   - **IP whitelist**: Our Cloud Run egress IPs
3. Client sends us 2 values:
   - `API Key` (e.g. `xAbCdEf123...`)
   - `Secret Key` (e.g. `NzYxWvUt...`)

**Binance-specific notes:**

- No passphrase required
- Max 200 items per page on order history
- Uses `fetchOrders()` (standard CCXT)
- Income endpoint (`/fapi/v1/income`) for P&L data
- Default type set to `swap` (USDT-M futures)

### How Credentials Are Stored

All credentials go into **GCP Secret Manager** (never committed to code, never in env vars).

**Naming pattern:**

```
exec-{client_id}-{venue}-api-key
exec-{client_id}-{venue}-api-secret
exec-{client_id}-{venue}-passphrase    # OKX only
```

**Examples:**

| Secret Name                      | Client    | Venue   | Type       |
| -------------------------------- | --------- | ------- | ---------- |
| `exec-pr-okx-api-key`            | PR        | OKX     | API key    |
| `exec-pr-okx-api-secret`         | PR        | OKX     | API secret |
| `exec-pr-okx-passphrase`         | PR        | OKX     | Passphrase |
| `exec-et-binance-api-key`        | ET        | Binance | API key    |
| `exec-et-binance-api-secret`     | ET        | Binance | API secret |
| `exec-odum-prop-binance-api-key` | ODUM_PROP | Binance | API key    |

**Access at runtime:**

```python
from unified_trading_library import get_secret
api_key = get_secret("exec-pr-okx-api-key")
```

### Key Rotation

When a client rotates their API keys:

1. Client creates new keys on the exchange
2. We update the values in Secret Manager (same secret names)
3. No code changes or redeployment needed — credentials are fetched at runtime

---

## Client Onboarding

End-to-end automated via the `client-reporting-manage onboard` command.

### Prerequisites

- Client has created exchange API keys (see above)
- GCP credentials available (ADC or service account)
- `credentials-registry.yaml` is accessible

### Process

```bash
client-reporting-manage onboard \
  --client-id NEW_CLIENT \
  --venue okx \
  --api-key "a1b2c3d4..." \
  --api-secret "ABCDEF..." \
  --passphrase "MyP@ss" \
  --full-name "New Client Ltd" \
  --org-id new_client \
  --currency USDT \
  --strategy-id mean_reversion_top20
```

**What happens (4 steps):**

1. **Validate credentials** — Connects to the exchange, fetches balance, confirms keys work
2. **Store in Secret Manager** — Creates `exec-new-client-okx-api-key`, `exec-new-client-okx-api-secret`, `exec-new-client-okx-passphrase`
3. **Add to registry** — Writes new org + client entry to `credentials-registry.yaml` with default fee structure (30% Odum, 10% trader)
4. **Run full backfill** — Pulls all available historical data from the exchange

**Dry run** (validate without storing):

```bash
client-reporting-manage onboard --client-id TEST --venue okx --api-key ... --dry-run
```

> Setting custom fee rates, introducers, `is_underwater`, and HWM seeds after onboarding is a commercial-config step —
> see the codex roster/fee SSOT for the model and `credentials-registry.yaml` for the live fields.

---

## Backfill Process

Pulls all available historical data from the exchange for a client. Run once during onboarding, or to rebuild after data issues.

```bash
client-reporting-manage backfill --client PR          # single client
client-reporting-manage backfill --client PR --days 90  # last 90 days only
```

### What Gets Backfilled

| Data        | OKX Source                                  | Binance Source      | Retention    |
| ----------- | ------------------------------------------- | ------------------- | ------------ |
| Balance     | `fetch_balance()`                           | `fetch_balance()`   | Current only |
| P&L / Bills | `/api/v5/account/bills`                     | `/fapi/v1/income`   | ~3 months    |
| Trades      | `fetch_my_trades()`                         | `fetch_my_trades()` | ~3 months    |
| Orders      | `fetchClosedOrders()` + `fetchOpenOrders()` | `fetch_orders()`    | ~3 months    |
| Positions   | `fetch_positions()`                         | `fetch_positions()` | Current only |

**Note:** Exchange APIs typically only provide ~90 days of history. That's why we persist everything to GCS — once fetched, it's ours forever (7-year retention).

### Backfill Output

Per-client files written to `gs://client-reporting-data-{project_id}/backfill/{client_id}/`:

```
balance.json       — Current balances (refreshed each update)
equity_curve.json  — Daily equity snapshots [{date, equity, pnl, transfer, ...}]
pnl.json           — Full P&L/bills ledger from exchange
trades.json        — All executed trades
orders.json        — Full order history (filled, cancelled, rejected)
positions.json     — Current open positions
metadata.json      — Last update timestamp, days tracked, venue, currency
transfers.json     — Detected deposit/withdrawal events
```

---

## Hourly Update

Runs every hour at `:05` via Cloud Scheduler → Cloud Run Job.

```bash
client-reporting-manage update                     # all active clients
client-reporting-manage update --client PR         # single client
```

### What Happens Each Hour

For each active `managed` client:

1. **Load existing data** from GCS (`equity_curve.json`, `orders.json`, etc.)
2. **Fetch current balance** from exchange → compute current equity in USD
3. **Check for gaps** — if last data point is >1 day old, backfill the gap:
   - Fetch P&L for the gap period (paginated in 7-day windows)
   - Build daily equity curve entries for missing days
   - Detect transfers: `transfer = equity_change - trading_pnl - btc_price_effect`
   - Transfer threshold: `max(100, min(1% of equity, 1000))`
4. **Refresh today's snapshot** — update today's equity point (same-day refresh, no duplicate)
5. **Collect new orders** — fetch recent orders, merge with existing, deduplicate by order ID
6. **Persist to GCS** — upload all 8 JSON files

### Transfer Detection

The system detects deposits/withdrawals automatically using residual analysis:

```
residual = equity_change - trading_pnl - btc_price_effect
if abs(residual) > threshold:
    record as transfer (positive = deposit, negative = withdrawal)
```

This avoids counting a client's $50K deposit as trading profit.

### TWR (Time-Weighted Return)

Daily returns are chain-multiplied, adjusted for transfers:

```
daily_return = (closing_equity - transfer) / (opening_equity) - 1
TWR = product(1 + daily_return_i) - 1
```

> The TWR/Notional/PnL-Recovery HWM model itself is documented in the codex roster/fee SSOT.

---

## Daily Full Snapshot

Runs once at `00:15 UTC` daily (after market close for most regions). Does everything the hourly does plus:

- Extended trade history window
- Full order history with MiFID fields (cancel reasons, slippage, trigger prices)
- Any additional MiFID compliance data

```
Schedule: "15 0 * * *" UTC
Cloud Run Job: {env}-client-reporting-daily-snapshot
```

---

## Data Persistence (GCS)

### Bucket

```
gs://client-reporting-data-{project_id}/
```

- **Object versioning**: Enabled — every update creates an immutable version
- **Lifecycle**: Non-current versions deleted after 2,555 days (7 years) for MiFID compliance
- **Current data**: `backfill/{client_id}/{file}.json`

### File Layout

```
gs://client-reporting-data-{project_id}/
  backfill/
    PR/
      balance.json        # 2KB
      equity_curve.json   # 50KB  (271 daily points)
      pnl.json            # 500KB (paginated exchange bills)
      trades.json         # 1MB   (all executed trades)
      orders.json         # 200KB (all orders incl. cancelled)
      positions.json      # 5KB   (current open positions)
      metadata.json       # 1KB   (last_update, venue, days)
      transfers.json      # 2KB   (detected deposits/withdrawals)
    NN/
      ...
    ET/
      ...
```

### Why GCS (Not Just Exchange API)

Exchange APIs only provide ~90 days of history. GCS gives us:

- **7-year retention** for MiFID II compliance
- **Immutable audit trail** via object versioning
- **No re-fetch needed** — once collected, data persists permanently
- **Fast reads** — hourly updates read from GCS, not exchange (for historical data)

---

## Cloud Run Jobs & Scheduling

### Architecture

```
Cloud Scheduler (cron)
    │
    │ HTTP POST to :run endpoint
    ▼
Cloud Run Job (container)
    │
    │ Reads credentials-registry.yaml
    │ Fetches secrets from Secret Manager
    │ Connects to OKX/Binance via CCXT
    │ Writes to GCS
    ▼
GCS Bucket (durable storage)
```

### Jobs

| Job Name                                | Schedule     | Entrypoint                              | Timeout |
| --------------------------------------- | ------------ | --------------------------------------- | ------- |
| `{env}-client-reporting-update`         | `5 * * * *`  | `client-reporting-manage update`        | 10 min  |
| `{env}-client-reporting-daily-snapshot` | `15 0 * * *` | `client-reporting-manage update --full` | 30 min  |

### Docker

Two build targets in the Dockerfile:

```bash
# API server (Cloud Run Service)
docker build --build-arg PROJECT_ID=... --target api -t client-reporting-api .

# Batch CLI (Cloud Run Job)
docker build --build-arg PROJECT_ID=... --target batch -t client-reporting-batch .
```

The batch image uses:

```dockerfile
ENTRYPOINT ["client-reporting-manage"]
CMD ["update"]
```

Override `CMD` in the Cloud Run Job config for different operations.

### Auto-Discovery

The update job reads `credentials-registry.yaml` at runtime. To add a new client:

1. Run `client-reporting-manage onboard ...`
2. No redeployment needed — next hourly run picks them up automatically

---

## Credential Registry Reference

**SSOT:** `execution-service/configs/credentials-registry.yaml`

### Schema

```yaml
organisations:
  { org_id }:
    name: "Display Name"
    type: internal | client
    contact: "optional contact name"

strategies:
  { strategy_id }:
    name: "Display Name"
    description: "What it does"

clients:
  { CLIENT_ID }:
    full_name: "Full Display Name"
    organisation_id: { org_id } # FK to organisations
    strategy_id: { strategy_id } # FK to strategies
    tranche: managed | fund_of_fund
    currency: USDT | BTC
    venue: okx | binance # only for managed
    secret_names: # Secret Manager keys per field
      api_key: exec-{id}-{venue}-api-key
      api_secret: exec-{id}-{venue}-api-secret
      passphrase: exec-{id}-{venue}-passphrase # OKX only
    odum_fee_pct: 0.30 # 30% of profits
    trader_fee_pct: 0.10 # 10% of profits
    introducer_id: "name" # optional
    introducer_fee_pct: 0.05 # optional, % of Odum fee
    is_active: true
    is_underwater: false # optional
    is_pooled: false # optional
    pool_investors: # only if is_pooled
      investor1: 0.50
      investor2: 0.50
    strategy_start_date: "2025-07-15" # optional
    initial_deposit_usd: 100000 # optional
    data_source: manual # only for fund_of_fund

server_costs_per_underwater_account_usd: 50
```

> The **committed values** of these fields (the actual roster + fee %s + pooled weights) are in the codex roster/fee
> SSOT; this section documents the schema, not the live values.

---

## Troubleshooting

### "No credentials for exec-xxx"

Secret Manager doesn't have the keys. Re-run onboard or manually create secrets:

```bash
echo -n "THE_API_KEY" | gcloud secrets create exec-{client}-{venue}-api-key --data-file=-
```

### OKX "Parameter limit error"

OKX max page size is 100 (not 200). The CLI handles this automatically.

### OKX "fetchOrders not supported"

OKX doesn't support the standard CCXT `fetchOrders()`. The CLI uses `fetchClosedOrders()` + `fetchOpenOrders()` instead.

### Gap in equity curve

Run a targeted update:

```bash
client-reporting-manage update --client PR
```

The update detects the gap and backfills automatically.

### Stale data after key rotation

Credentials are fetched fresh on each run from Secret Manager. If the client rotated keys, update Secret Manager values and the next run will use the new keys.

### Fund-of-fund client shows no data

Fund-of-fund clients (`YOAV`, `GUY_ASRAF`) use `data_source: manual`. Their NAV must be entered manually — they have no exchange API keys.
