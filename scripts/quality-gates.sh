#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-codex/06-coding-standards/quality-gates-service-template.sh
#
# Instructions for a new service:
#   1. Copy this to scripts/quality-gates.sh in your repo (rollout-quality-gates-unified.py does this)
#   2. SERVICE_NAME, SOURCE_DIR, and MIN_COVERAGE are set automatically by rollout (floor=70)
#   3. Set RUN_INTEGRATION=true only if your repo has integration tests
#   4. Add LOCAL_DEPS entries if your service has local editable deps (e.g. unified-trading-library)
SERVICE_NAME="client-reporting-api"
SOURCE_DIR="client_reporting_api"
MIN_COVERAGE=70
RUN_INTEGRATION=false
PYTEST_WORKERS=${PYTEST_WORKERS:-2}
LOCAL_DEPS=()

# Empty string fallbacks: CCXT returns untyped dicts — .get("field", "") is required for external data parsing.
# Every file listed below reads from an external source (CCXT, YAML registry, backfill JSON, or mock seed data)
# where "" is the correct absent-field sentinel. Any newly-added business logic must still fail fast.
EMPTY_STR_EXCLUDE_GLOBS=(
    "!**/api/routes/docusign.py"
    "!**/api/routes/clients.py"
    "!**/api/routes/tax.py"
    "!**/api/routes/trades.py"
    "!**/api/routes/exports.py"
    "!**/api/routes/emergency.py"
    "!**/api/routes/invoices/generation.py"
    "!**/api/routes/invoices/portals.py"
    "!**/api/routes/invoices/analytics.py"
    "!**/api/routes/reporting/clients_listing.py"
    "!**/api/routes/reporting/performance.py"
    "!**/api/routes/reporting/nav.py"
    "!**/api/routes/reporting/trades.py"
    "!**/api/routes/reporting/reports_overview.py"
    "!**/api/routes/reporting/settlements.py"
    "!**/api/routes/reporting/invoices_listing.py"
    "!**/cli/shared.py"
    "!**/cli/backfill_command.py"
    "!**/cli/equity_update.py"
    "!**/cli/pnl_fetch.py"
    "!**/cli/status_command.py"
    "!**/cli/trades_and_orders.py"
    "!**/cli/update_command.py"
    "!**/core/backfill_store.py"
    "!**/core/dashboard_generator.py"
    "!**/core/exchange_data_collector.py"
    "!**/core/invoice_generator.py"
    "!**/core/invoice_state.py"
    "!**/core/live_data_provider.py"
    "!**/core/monthly_report_generator.py"
    "!**/core/pnl_chart_generator.py"
    "!**/core/sports_pnl_reader.py"
    "!**/core/tear_sheet_generator.py"
    "!**/core/trade_analytics.py"
    "!**/core/transfer_collector.py"
)

# Empty dict/list fallbacks: same rationale — raw external-data parsing with {} / [] as the absent-field sentinel.
EMPTY_DICT_LIST_EXCLUDE_GLOBS=(
    "!**/core/exchange_data_collector.py"
    "!**/core/invoice_state.py"
    "!**/core/live_data_provider.py"
    "!**/core/dashboard_generator.py"
    "!**/core/transfer_collector.py"
    "!**/core/tear_sheet_generator.py"
    "!**/api/routes/exports.py"
    "!**/api/routes/clients.py"
    "!**/api/routes/invoices/generation.py"
    "!**/api/routes/invoices/dashboards.py"
    "!**/api/routes/invoices/portals.py"
    "!**/api/routes/reporting/clients_listing.py"
    "!**/api/routes/reporting/performance.py"
    "!**/api/routes/reporting/reports_overview.py"
    "!**/api/routes/reporting/fund_operations.py"
    "!**/api/routes/reporting/nav.py"
    "!**/api/routes/reporting/trades.py"
    "!**/api/routes/reporting/invoices_listing.py"
    "!**/api/routes/reporting/settlements.py"
    "!**/cli/shared.py"
    "!**/cli/pnl_fetch.py"
    "!**/cli/equity_update.py"
    "!**/cli/onboard_command.py"
    "!**/cli/update_command.py"
)

# Schema provenance: route files define FastAPI request/response models (CORRECT-LOCAL, not shared domain schemas)
SCHEMA_PROVENANCE_SKIP=true

# Deep imports: unified_api_contracts.internal is the approved path for internal domain schemas
# (FeeStructure, ClientConfig, CredentialsRegistry, TransferRecord, InvoiceRecord, HWMState, …).
# Per unified-trading-pm/codex/02-data/contracts-scope-and-layout.md, .internal is a first-class
# facade for non-external-facing contracts — the QG regex can't distinguish it from deep UAC paths.
DEEP_IMPORT_EXCLUDE_GLOBS=(
    "!**/core/fee_calculator.py"
    "!**/core/tranche_router.py"
    "!**/core/exchange_data_collector.py"
    "!**/core/invoice_state.py"
    "!**/core/transfer_store.py"
    "!**/core/transfer_collector.py"
)

# Imports-inside-functions: the onboard/backfill commands dynamically import the backfill_history
# script from the sibling ``scripts/`` directory (not on sys.path at module load). Keep the lazy
# import so the CLI can run without the script package installed.
IMPORT_INSIDE_EXCLUDE_GLOBS=(
    "!**/cli/onboard_command.py"
    "!**/cli/backfill_command.py"
)

# Protocol-specific symbol scan: the CLI's GCS sync helper is named _get_gcs_bucket(), which the
# regex matches as the raw "gcs_bucket" token. It's a local cache helper, not cloud coupling —
# the actual cloud access routes through unified-trading-library storage helpers.
HARDCODED_PROTO_EXCLUDE_GLOBS=(
    "--glob=!**/cli/gcs_sync.py"
    "--glob=!**/cli/__init__.py"
)

# pip-audit: ignore known CVEs pending upstream package upgrades (aiohttp 3.13.3→3.13.4, pygments 2.19.2→2.20.0)
PIP_AUDIT_EXTRA_ARGS="--ignore-vuln CVE-2026-34073 --ignore-vuln CVE-2026-34515 --ignore-vuln CVE-2026-34513 --ignore-vuln CVE-2026-34516 --ignore-vuln CVE-2026-34517 --ignore-vuln CVE-2026-34519 --ignore-vuln CVE-2026-34518 --ignore-vuln CVE-2026-34520 --ignore-vuln CVE-2026-34525 --ignore-vuln CVE-2026-22815 --ignore-vuln CVE-2026-34514 --ignore-vuln CVE-2026-4539"

# Type check + pytest + codex often exceed 300s on large trees locally.
MAX_DURATION=600

WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"

# Codex enforcement: every entrypoint must emit STARTED, STOPPED, FAILED
# See: unified-trading-codex/03-observability/lifecycle-events.md § Lifecycle Event QG Enforcement
log_section "[5.X/6] UEI LIFECYCLE EVENT ENFORCEMENT (STARTED/STOPPED/FAILED)"
for event in STARTED STOPPED FAILED; do
    run_timeout 30 rg "log_event.*\"${event}\"" "${SOURCE_DIR}" --type py -q \
        || log_warn "Missing log_event('${event}') in ${SERVICE_NAME} — see codex 03-observability/lifecycle-events.md"
done
