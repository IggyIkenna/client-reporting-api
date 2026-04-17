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

# Empty string fallbacks: CCXT returns untyped dicts — .get("field", "") is required for external data parsing
# docusign.py: mock envelope data; exchange_data_collector.py: CCXT raw dicts; clients.py: registry config dicts
EMPTY_STR_EXCLUDE_GLOBS=(
    "!**/api/routes/docusign.py"
    "!**/core/exchange_data_collector.py"
    "!**/api/routes/clients.py"
    "!**/api/routes/tax.py"
)

# Empty dict/list fallbacks: CCXT raw dicts use .get("total", {}) for external API data; exports use .get("key", [])
EMPTY_DICT_LIST_EXCLUDE_GLOBS=(
    "!**/core/exchange_data_collector.py"
    "!**/api/routes/exports.py"
)

# Schema provenance: route files define FastAPI request/response models (CORRECT-LOCAL, not shared domain schemas)
SCHEMA_PROVENANCE_SKIP=true

# Deep imports: unified_api_contracts.internal is the approved path for internal domain schemas
# (FeeStructure, ClientConfig, CredentialsRegistry)
DEEP_IMPORT_EXCLUDE_GLOBS=(
    "!**/core/fee_calculator.py"
    "!**/core/tranche_router.py"
    "!**/core/exchange_data_collector.py"
)

# pip-audit: ignore known CVEs pending upstream package upgrades (aiohttp 3.13.3→3.13.4, pygments 2.19.2→2.20.0)
PIP_AUDIT_EXTRA_ARGS="--ignore-vuln CVE-2026-34073 --ignore-vuln CVE-2026-34515 --ignore-vuln CVE-2026-34513 --ignore-vuln CVE-2026-34516 --ignore-vuln CVE-2026-34517 --ignore-vuln CVE-2026-34519 --ignore-vuln CVE-2026-34518 --ignore-vuln CVE-2026-34520 --ignore-vuln CVE-2026-34525 --ignore-vuln CVE-2026-22815 --ignore-vuln CVE-2026-34514 --ignore-vuln CVE-2026-4539"

WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"

# Codex enforcement: every entrypoint must emit STARTED, STOPPED, FAILED
# See: unified-trading-codex/03-observability/lifecycle-events.md § Lifecycle Event QG Enforcement
log_section "[5.X/6] UEI LIFECYCLE EVENT ENFORCEMENT (STARTED/STOPPED/FAILED)"
for event in STARTED STOPPED FAILED; do
    run_timeout 30 rg "log_event.*\"${event}\"" "${SOURCE_DIR}" --type py -q \
        || log_warn "Missing log_event('${event}') in ${SERVICE_NAME} — see codex 03-observability/lifecycle-events.md"
done
