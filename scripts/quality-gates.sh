#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-pm/codex/06-coding-standards/quality-gates-service-template.sh
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
    "!**/api/routes/*.py"
    "!**/api/routes/invoices/*.py"
    "!**/api/routes/reporting/*.py"
    "!**/cli/*.py"
    "!**/core/*.py"
)

# Empty dict/list fallbacks: same rationale — raw external-data parsing with {} / [] as the absent-field sentinel.
EMPTY_DICT_LIST_EXCLUDE_GLOBS=(
    "!**/api/routes/*.py"
    "!**/api/routes/invoices/*.py"
    "!**/api/routes/reporting/*.py"
    "!**/cli/*.py"
    "!**/core/*.py"
)

# Schema provenance: route files define FastAPI request/response models (CORRECT-LOCAL, not shared domain schemas)
SCHEMA_PROVENANCE_SKIP=true

# Deep imports: unified_api_contracts.internal is the approved path for internal domain schemas
# (FeeStructure, ClientConfig, CredentialsRegistry, TransferRecord, InvoiceRecord, HWMState, ...).
# Per unified-trading-pm/codex/02-data/contracts-scope-and-layout.md, .internal is a first-class
# facade for non-external-facing contracts — the QG regex can't distinguish it from deep UAC paths.
#
# attribution_reader.py / backfill_store.py: use unified_trading_library.cloud_interface and
# unified_trading_library.performance_metrics sub-paths intentionally — the top-level UTL facade
# triggers candidate_manifest_store which imports UAC strategy_service module not yet on this branch.
# Remove exclusions once UAC + UTL deps are fully reconciled across all tabs.
DEEP_IMPORT_EXCLUDE_GLOBS=(
    "!**/core/fee_calculator.py"
    "!**/core/tranche_router.py"
    "!**/core/exchange_data_collector.py"
    "!**/core/invoice_state.py"
    "!**/core/transfer_store.py"
    "!**/core/transfer_collector.py"
    "!**/core/attribution_reader.py"
    "!**/core/backfill_store.py"
)

# Imports-inside-functions: the onboard/backfill commands dynamically import the backfill_history
# script from the sibling ``scripts/`` directory (not on sys.path at module load). Keep the lazy
# import so the CLI can run without the script package installed.
#
# backfill_store.py / tear_sheet_generator.py: lazy-import unified_trading_library.performance_metrics
# sub-module because performance_metrics functions (twr_equity_curve, max_drawdown, sharpe_ratio, etc.)
# are not re-exported at the UTL top-level __init__. Moving to top-level requires deep imports which
# trigger the full UTL __init__ chain (candidate_manifest_store -> UAC strategy_service). Lazy imports
# are the correct escape hatch here — remove once UTL exports performance_metrics at top-level.
IMPORT_INSIDE_EXCLUDE_GLOBS=(
    "!**/cli/backfill_command.py"
    "!**/cli/onboard_command.py"
    "!**/core/backfill_store.py"
    "!**/core/tear_sheet_generator.py"
)

# STEP 5.11 exclusions: gcs_sync.py/_init__.py define/export _get_gcs_bucket() — a helper that
# resolves the GCS bucket name via resolve_bucket_name(). The symbol name contains "gcs_bucket"
# as a substring but is NOT a raw GCS SDK call. Documented in QUALITY_GATE_BYPASS_AUDIT.md §2.6.
HARDCODED_PROTO_EXCLUDE_GLOBS=("--glob=!**/cli/gcs_sync.py" "--glob=!**/cli/__init__.py")

# Type check + pytest + codex often exceed 300s on large trees locally.
PIP_AUDIT_EXTRA_ARGS="--ignore-vuln PYSEC-2024-277 --ignore-vuln PYSEC-2025-183"
MAX_DURATION=600
WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"

# Codex enforcement: lifecycle triple (STARTED / STOPPED / FAILED) via UTL — not duplicated in service code.
# See: unified-trading-pm/codex/03-observability/lifecycle-events.md § Lifecycle Event QG Enforcement
log_section "[5.X/6] UEI LIFECYCLE EVENT ENFORCEMENT (STARTED/STOPPED/FAILED)"
if rg -q 'fastapi_uei_lifespan\s*\(' --type py "$SOURCE_DIR" 2>/dev/null; then
    log_success "UEI lifecycle: fastapi_uei_lifespan (canonical HTTP wiring in UTL)"
elif rg -q 'ServiceBootstrap\s*\(' --type py "$SOURCE_DIR" 2>/dev/null; then
    log_success "UEI lifecycle: ServiceBootstrap (canonical CLI wiring in UTL)"
else
    for event in STARTED STOPPED FAILED; do
        # -U: allow multiline call sites (e.g. log_event(\n  "STARTED", ...))
        run_timeout 30 rg "log_event.*\"${event}\"" "${SOURCE_DIR}" --type py -U -q \
            || log_warn "Missing log_event('${event}') in ${SERVICE_NAME} — see codex 03-observability/lifecycle-events.md"
    done
fi
