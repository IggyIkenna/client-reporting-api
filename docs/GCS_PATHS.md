# client-reporting-api — GCS Paths

`client-reporting-api` **does read from and write to GCS** in its current implementation. HWM state, invoices,
payments, statements, and attribution/ledger data are persisted to and served from GCS via the UTL
`resolve_bucket_name(...)` + `get_storage_client()` helpers
(`client_reporting_api/core/{hwm_reader,attribution_reader,ledger_views,invoice_state,recon_view}.py`), never an inline
`gs://` literal and never a raw `google.cloud.storage` client.

## Buckets (resolved via `resolve_bucket_name`)

Bucket names are resolved by `kind` — never hardcode them; `{project_id}` is the GCP project placeholder, never a
literal.

| `resolve_bucket_name` kind | Used by                                                     |
| -------------------------- | ----------------------------------------------------------- |
| `client-statements`        | HWM state, invoices, payments, per-client backfill/snapshot |
| `client-reports`           | Attribution / rendered report artefacts                     |

Naming/tiering SSOT:
[`/codex/05-infrastructure/bucket-isolation-model.md`](../../unified-trading-pm/codex/05-infrastructure/bucket-isolation-model.md).
Object-operations SSOT:
[`/codex/05-infrastructure/gcs-object-operations.md`](../../unified-trading-pm/codex/05-infrastructure/gcs-object-operations.md).

## Layout

The full operational persistence layout (`backfill/{client_id}/…`, `state/{client_id}/…`, object versioning + 7-year
MiFID retention, and the Cloud Run backfill/update jobs that write it) is documented in the repo-local operations
runbook: [`CLIENT_OPERATIONS_GUIDE.md` — Data Persistence (GCS)](./CLIENT_OPERATIONS_GUIDE.md). That runbook is the
SSOT for this repo's object layout; this file only records that the service is a live GCS reader/writer and which bucket
kinds it resolves.

## Event Logging

Service lifecycle events are logged via `unified-trading-library` `setup_events()` with `sink="cloud_logging"`
(Cloud Logging, not GCS).
