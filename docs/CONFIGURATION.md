# client-reporting-api — Configuration

Configuration is managed via `UnifiedCloudConfig` from
`unified-config-interface`. No separate Settings class.

## Environment Variables

| Variable         | Required   | Description                                     |
| ---------------- | ---------- | ----------------------------------------------- |
| `API_KEY`        | Yes (prod) | API key for `X-API-Key` header validation       |
| `DISABLE_AUTH`   | No         | Set `true` for local dev; blocked in production |
| `ENVIRONMENT`    | No         | `development`, `staging`, or `production`       |
| `GCP_PROJECT_ID` | No         | GCP project ID (for cloud logging events)       |

## Optional Integrations

| Variable            | Description                                         |
| ------------------- | --------------------------------------------------- |
| `ANTHROPIC_API_KEY` | Anthropic API key for AI-generated report summaries |

## Credentials Registry

The tranche router reads:

```
../execution-services/configs/credentials-registry.yaml
```

This path is resolved relative to the workspace root. The file contains
per-client config:

- `tranche` — client tier type
- `secret_name` — Secret Manager key for API credentials
- `odum_fee_pct`, `trader_fee_pct` — fee rates
- `introducer_id`, `introducer_fee_pct` — referral fee config

## Example .env (local development)

```bash
DISABLE_AUTH=true
ENVIRONMENT=development
```

## Docs Availability

`/docs`, `/redoc`, and `/openapi.json` are disabled in production
(`ENVIRONMENT=production`).
