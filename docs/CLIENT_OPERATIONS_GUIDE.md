# Client Reporting Operations Guide

Complete reference for the client lifecycle: onboarding, API keys, backfill, hourly/daily updates, fee structure, and organisational hierarchy.

---

## Table of Contents

1. [Organisation & Client Hierarchy](#organisation--client-hierarchy)
2. [Strategies](#strategies)
3. [Exchange API Keys (Binance & OKX)](#exchange-api-keys-binance--okx)
4. [Client Onboarding](#client-onboarding)
5. [Backfill Process](#backfill-process)
6. [Hourly Update](#hourly-update)
7. [Daily Full Snapshot](#daily-full-snapshot)
8. [Fee Structure & Invoicing](#fee-structure--invoicing)
9. [Data Persistence (GCS)](#data-persistence-gcs)
10. [Cloud Run Jobs & Scheduling](#cloud-run-jobs--scheduling)
11. [Credential Registry Reference](#credential-registry-reference)
12. [Troubleshooting](#troubleshooting)

---

## Organisation & Client Hierarchy

Every client belongs to an **organisation**. Organisations are either `internal` (Odum's own accounts) or `client` (external managed accounts).

```
Organisation (org)
  └── Client (account)
        └── Strategy (what we trade on their behalf)
```

### Current Organisations

| Org ID       | Name          | Type     | Contact   |
| ------------ | ------------- | -------- | --------- |
| `odum`       | Odum Capital  | internal | —         |
| `prism`      | Prism Capital | client   | Max       |
| `namnar`     | Namnar        | client   | —         |
| `eqvilent`   | Eqvilent      | client   | Bluecoast |
| `steadyhash` | Steady Hash   | client   | —         |
| `gpd`        | GPD Capital   | client   | —         |
| `shaun_lim`  | Shaun Lim     | client   | —         |
| `anu`        | Anu           | client   | —         |
| `ik`         | IK Group      | client   | —         |
| `yoav`       | Yoav          | client   | —         |
| `guy_asraf`  | Guy Asraf     | client   | —         |

### Current Clients

| Client ID   | Organisation | Venue   | Currency | Strategy              | Tranche      | Fee (Odum) | Fee (Trader) |
| ----------- | ------------ | ------- | -------- | --------------------- | ------------ | ---------- | ------------ |
| `PR`        | Prism        | OKX     | USDT     | Mean Reversion Top 20 | managed      | 40%        | 10%          |
| `NN`        | Namnar       | OKX     | USDT     | Mean Reversion Top 20 | managed      | 30%        | 10%          |
| `ET`        | Eqvilent     | Binance | USDT     | Mean Reversion Top 20 | managed      | 30%        | 10%          |
| `STD`       | Steady Hash  | OKX     | USDT     | Mean Reversion Top 20 | managed      | 35%        | 10%          |
| `GP`        | GPD Capital  | OKX     | USDT     | Mean Reversion Top 20 | managed      | 30%        | 10%          |
| `SL`        | Shaun Lim    | OKX     | USDT     | Mean Reversion Top 20 | managed      | 30%        | 10%          |
| `SL2`       | Shaun Lim    | OKX     | BTC      | Mean Reversion Top 20 | managed      | 30%        | 10%          |
| `ANU`       | Anu          | OKX     | BTC      | Mean Reversion Top 20 | managed      | 30%        | 10%          |
| `IK`        | IK Group     | OKX     | USDT     | Mean Reversion Top 20 | managed      | 35%        | 10%          |
| `YOAV`      | Yoav         | —       | BTC      | DeFi BTC Yield        | fund_of_fund | 20%        | 0%           |
| `GUY_ASRAF` | Guy Asraf    | —       | BTC      | DeFi BTC Yield        | fund_of_fund | 20%        | 0%           |
| `ODUM_PROP` | Odum Capital | Binance | USDT     | Mean Reversion Top 20 | managed      | 0%         | 0%           |

### Client Tranches

| Tranche        | Data Source  | Description                                       |
| -------------- | ------------ | ------------------------------------------------- |
| `managed`      | Exchange API | We hold client's API keys, trade on their behalf  |
| `fund_of_fund` | Manual entry | No exchange API — NAV entered manually per period |

### Pooled Accounts

Client `IK` is a **pooled account** — multiple investors share one exchange sub-account:

```yaml
pool_investors:
  jihane: 0.25344 # 25.3%
  amaka: 0.216 # 21.6%
  ik: 0.53056 # 53.1%
```

Fees and P&L are split pro-rata by these weights.

---

## Strategies

| Strategy ID            | Name                  | Description                                                            |
| ---------------------- | --------------------- | ---------------------------------------------------------------------- |
| `mean_reversion_top20` | Mean Reversion Top 20 | Perpetual futures mean reversion on top 20 crypto assets by market cap |
| `defi_btc_yield`       | DeFi BTC Yield        | BTC-denominated yield via DeFi protocols and fund-of-fund allocation   |

All 10 managed clients currently run `mean_reversion_top20`. The 2 fund-of-fund clients run `defi_btc_yield`.

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
| Secret Name | Client | Venue | Type |
| ---------------------------------- | ------ | ------- | ---------- |
| `exec-pr-okx-api-key` | PR | OKX | API key |
| `exec-pr-okx-api-secret` | PR | OKX | API secret |
| `exec-pr-okx-passphrase` | PR | OKX | Passphrase |
| `exec-et-binance-api-key` | ET | Binance | API key |
| `exec-et-binance-api-secret` | ET | Binance | API secret |
| `exec-odum-prop-binance-api-key` | ODUM_PROP | Binance | API key |

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

## Fee Structure & Invoicing

### Fee Tiers

Each client has up to 4 fee components:

| Fee            | Applies When          | Typical Range | Who Gets Paid              |
| -------------- | --------------------- | ------------- | -------------------------- |
| Trader fee     | PnL > trader HWM      | 10%           | Desk trader                |
| Odum fee       | PnL > Odum HWM        | 20-40%        | Odum Capital               |
| Introducer fee | If introducer exists  | 5-15%         | Referrer (of Odum's share) |
| Server cost    | Account is underwater | $50/month     | Infrastructure             |

### High-Water Mark (HWM)

Fees are only charged on **new profits above the previous high**. If the account loses money, no performance fees until it recovers past the previous peak.

- **Trader HWM**: Separate from Odum HWM (trader can be paid on new profits even if Odum's HWM isn't breached, if they have different entry points)
- **Odum HWM**: Reset at fee crystallisation (typically monthly)
- **Dual HWM**: Each party tracks their own peak independently

### Underwater Accounts

When `is_underwater: true` in the registry:

- No performance fees charged
- Server cost ($50/month) charged instead
- Tracked until equity exceeds the HWM again

### Introducer Fees

Some clients were referred. The introducer gets a percentage of **Odum's fee** (not the total P&L):

| Client | Introducer | Introducer Fee | Effective Split                         |
| ------ | ---------- | -------------- | --------------------------------------- |
| PR     | Max        | 15% of Odum    | Odum 34%, Introducer 6%, Trader 10%     |
| ET     | Bluecoast  | 5% of Odum     | Odum 28.5%, Introducer 1.5%, Trader 10% |

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
gs://client-reporting-data-central-element-323112/
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
    secret_name: exec-{id}-{venue}-{currency} # Secret Manager key prefix
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
