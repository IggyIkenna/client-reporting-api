# client-reporting-api — GCS Paths

`client-reporting-api` does not directly read from or write to GCS in its
current implementation. Report data is derived from:

1. The credentials registry YAML file (local/workspace path)
2. Client AUM data passed directly in API requests
3. AI-generated summaries from the Anthropic API

## Future GCS Paths (planned)

When report archival is implemented, the following paths are expected:

| Path                                                                                   | Description               | Created By           |
| -------------------------------------------------------------------------------------- | ------------------------- | -------------------- |
| `gs://client-reports-{project_id}/reports/{client_id}/{period}/executive_summary.html` | Monthly executive summary | client-reporting-api |
| `gs://client-reports-{project_id}/reports/{client_id}/{period}/investor_note.html`     | BTC investor note         | client-reporting-api |
| `gs://client-reports-{project_id}/invoices/{client_id}/{period}/invoice.json`          | Period fee invoice        | client-reporting-api |

All paths use `{project_id}` as a placeholder for the GCP project ID — never
hardcoded.

## Event Logging

Service lifecycle events are logged via `unified-events-interface`
`setup_events()` with `sink="cloud_logging"` (Cloud Logging, not GCS).
