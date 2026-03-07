# client-reporting-api

Client reporting, fee engine, and invoice management service. Generates client
performance reports (HTML, PDF), calculates period fees using a high-water mark
model, and serves reporting data to the client-reporting-ui.

## Quick Start

```bash
cd client-reporting-api
uv pip install -e ".[dev]"
uvicorn client_reporting_api.api.main:app --port 8080
```

## Key Capabilities

- **Report generation:** HTML reports rendered via Jinja2 templates (executive
  summary, BTC investor notes) with embedded matplotlib charts
- **Fee calculation:** High-water mark model with trader fee, Odum fee, and
  introducer fee tiers
- **Tranche routing:** Routes clients to the appropriate data source based on
  their tranche type (managed / fund_of_fund)
- **AI summaries:** Optional LLM-generated commentary via the Anthropic SDK

## Authentication

All endpoints require `X-API-Key` header. Set `DISABLE_AUTH=true` for local
development.

## Running Tests

```bash
pytest tests/ -v
```
