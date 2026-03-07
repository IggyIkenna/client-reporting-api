# client-reporting-api — Schema Validation

## Current Endpoints

### `GET /health`

No request parameters.

**Response:**

```json
{ "status": "ok" }
```

### `GET /metrics`

No request parameters.

**Response:** Empty text/plain body (Prometheus stub — not yet implemented).

## Core Internal Schemas

### `FeeStructure` (from unified_internal_contracts)

Used by `FeeCalculator.calculate_period_fees()`.

| Field                | Type          | Description                                     |
| -------------------- | ------------- | ----------------------------------------------- |
| `trader_fee_pct`     | float         | Trader's performance fee rate (e.g. 0.20 = 20%) |
| `odum_fee_pct`       | float         | Odum's performance fee rate (e.g. 0.02 = 2%)    |
| `introducer_id`      | str \| None   | Introducer client ID (if applicable)            |
| `introducer_fee_pct` | float \| None | Introducer's share of Odum fee                  |

### `FeeCalculator.calculate_period_fees()` Return Type

Returns a 4-tuple of `Decimal`:

```python
(trader_fee, odum_fee, introducer_fee, server_cost)
```

| Value            | Description                                    |
| ---------------- | ---------------------------------------------- |
| `trader_fee`     | Fee owed to the trader (HWM-gated)             |
| `odum_fee`       | Fee retained by Odum (HWM-gated)               |
| `introducer_fee` | Portion of odum_fee owed to introducer         |
| `server_cost`    | Server cost charged when account is underwater |

### `ClientConfig` (TypedDict, from `tranche_router.py`)

| Field                | Type             | Required | Description                               |
| -------------------- | ---------------- | -------- | ----------------------------------------- |
| `full_name`          | str              | No       | Client display name                       |
| `tranche`            | str              | No       | Client tier (`managed`, `fund_of_fund`)   |
| `currency`           | str              | No       | Account currency                          |
| `venue`              | str              | No       | Trading venue                             |
| `secret_name`        | str              | No       | Secret Manager key for API credentials    |
| `odum_fee_pct`       | float            | No       | Odum fee rate                             |
| `trader_fee_pct`     | float            | No       | Trader fee rate                           |
| `introducer_id`      | str              | No       | Introducer client ID                      |
| `introducer_fee_pct` | float            | No       | Introducer fee rate                       |
| `is_underwater`      | bool             | No       | Whether account is currently underwater   |
| `is_active`          | bool             | No       | Whether account is active                 |
| `data_source`        | str              | No       | Override for data source routing          |
| `is_pooled`          | bool             | No       | Whether this is a pooled account          |
| `pool_investors`     | dict[str, float] | No       | Pool investor shares `{client_id: share}` |

### Report Template Inputs

#### `render_executive_summary()`

| Parameter      | Type        | Description                      |
| -------------- | ----------- | -------------------------------- |
| `client_id`    | str         | Client identifier                |
| `period_month` | str         | Reporting period (YYYY-MM)       |
| `closing_aum`  | float       | AUM at period end                |
| `return_pct`   | float       | Period return percentage         |
| `currency`     | str         | Account currency                 |
| `chart_base64` | str         | Base64-encoded PNG chart         |
| `ai_summary`   | str \| None | Optional AI-generated commentary |

**Returns:** HTML string.

#### `render_btc_investor_note()`

| Parameter          | Type                         | Description                |
| ------------------ | ---------------------------- | -------------------------- |
| `client_id`        | str                          | Client identifier          |
| `period_month`     | str                          | Reporting period (YYYY-MM) |
| `monthly_return`   | float                        | Month-over-month return    |
| `inception_return` | float                        | Return since inception     |
| `start_balance`    | float                        | Opening balance            |
| `end_balance`      | float                        | Closing balance            |
| `currency`         | str                          | Account currency           |
| `inception_date`   | str \| None                  | Fund inception date        |
| `monthly_returns`  | list[dict[str, str]] \| None | Historical monthly returns |

**Returns:** HTML string.

## Error Responses

| Status | Body                            | Condition                                    |
| ------ | ------------------------------- | -------------------------------------------- |
| 401    | `{"detail": "Missing API key"}` | No `X-API-Key` header (for protected routes) |
| 401    | `{"detail": "Invalid API key"}` | Wrong API key                                |
