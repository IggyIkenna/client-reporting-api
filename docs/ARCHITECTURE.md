# client-reporting-api — Architecture

## Overview

`client-reporting-api` is a FastAPI service responsible for generating
client-facing performance reports, computing period fees, and managing invoices.
It serves the client-reporting-ui and integrates with the execution-services
ecosystem for trade data.

## Service Layer Diagram

```
client-reporting-ui
        │
        │ HTTP (X-API-Key auth)
        ▼
client-reporting-api (FastAPI, port 8080)
        │
   ┌────┴──────────────────────────────────────┐
   │                API Routes                  │
   │  /health   /metrics   (+ future routes)   │
   └────┬──────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────┐
   │                Core Services               │
   │  ReportGenerator  — Jinja2 + matplotlib    │
   │  FeeCalculator    — HWM fee model          │
   │  TrancheRouter    — Client registry        │
   └────┬──────────────────────────────────────┘
        │
   ┌────┴──────────────────────────────────────┐
   │           External Integrations            │
   │  Jinja2 templates (HTML reports)          │
   │  matplotlib (chart generation)            │
   │  Anthropic SDK (AI summaries, optional)   │
   │  execution-services/configs/credentials-registry.yaml │
   └────────────────────────────────────────────┘
```

## Module Layout

```
client_reporting_api/
├── main.py                    — Entry point: setup_events, setup_tracing, uvicorn.run
├── auth.py                    — X-API-Key verification via UnifiedCloudConfig
├── _google_auth_sync.py       — Synchronous Google auth helper
├── core/
│   ├── report_generator.py    — generate_chart_base64, render_executive_summary, render_btc_investor_note
│   ├── fee_calculator.py      — FeeCalculator: HWM fee model (trader/odum/introducer/server fees)
│   └── tranche_router.py      — load_registry, get_client_config, get_data_source
└── api/
    ├── main.py                — FastAPI app, lifespan, auth router setup
    └── routes/
        └── health.py          — GET /health
```

## Core Components

### `FeeCalculator`

Implements a 4-tier high-water mark fee model:

```
period_fees = calculate_period_fees(
    client_id, opening_aum, closing_aum,
    trader_hwm, odum_hwm, fee_structure,
    is_underwater, server_cost_usd=50
)
→ (trader_fee, odum_fee, introducer_fee, server_cost)
```

- **Trader fee**: Applied on PnL above the trader's HWM
- **Odum fee**: Applied on PnL above Odum's HWM
- **Introducer fee**: Percentage of Odum fee if an introducer is configured
- **Server cost**: Charged when account is underwater (default $50/month)

### `ReportGenerator`

Generates HTML reports using Jinja2 templates stored in
`client_reporting_api/templates/`:

- `odum_executive_summary.html` — Monthly executive summary with embedded chart
- `btc_investor_note.html` — BTC investor performance note

Charts are rendered by matplotlib to in-memory PNG buffers, base64-encoded, and
embedded in HTML.

### `TrancheRouter`

Reads a YAML credentials registry
(`execution-services/configs/credentials-registry.yaml`) to determine each
client's data source:

| Tranche                         | Data Source  |
| ------------------------------- | ------------ |
| `managed` with `secret_name`    | `api_live`   |
| `managed` without `secret_name` | `api_static` |
| `fund_of_fund`                  | `manual`     |
| (unknown)                       | `manual`     |

### AI Summaries (optional)

The `anthropic` SDK is available as a dependency. When configured, AI-generated
commentary can be injected into the executive summary template via the
`ai_summary` parameter.

## Dependencies

| Library                    | Purpose                          |
| -------------------------- | -------------------------------- |
| fastapi                    | HTTP framework                   |
| uvicorn                    | ASGI server                      |
| jinja2                     | HTML template rendering          |
| matplotlib                 | Chart generation (PNG, base64)   |
| anthropic                  | AI summary generation (optional) |
| google-auth                | GCP authentication               |
| pyyaml                     | Credentials registry parsing     |
| unified-trading-library    | setup_tracing                    |
| unified-config-interface   | UnifiedCloudConfig               |
| unified-events-interface   | setup_events                     |
| unified-internal-contracts | FeeStructure and domain types    |

## Tier Classification

T3 service — depends on T0 (unified-internal-contracts), T1
(unified-config-interface, unified-events-interface, unified-trading-library).
