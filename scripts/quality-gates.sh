#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-codex/06-coding-standards/quality-gates-service-template.sh
#
# Instructions for a new service:
#   1. Copy this to scripts/quality-gates.sh in your repo (rollout-quality-gates-unified.py does this)
#   2. SERVICE_NAME, SOURCE_DIR, and MIN_COVERAGE are set automatically by rollout (floor=70)
#   3. Set RUN_INTEGRATION=true only if your repo has integration tests
#   4. Add LOCAL_DEPS entries if your service has local editable deps (e.g. unified-events-interface)
SERVICE_NAME="client-reporting-api"
SOURCE_DIR="client_reporting_api"
MIN_COVERAGE=70
RUN_INTEGRATION=false
PYTEST_WORKERS=${PYTEST_WORKERS:-2}
LOCAL_DEPS=()

# Empty string fallbacks in docusign.py: mock envelope data uses .get("field", "") for optional fields
EMPTY_STR_EXCLUDE_GLOBS=("!**/api/routes/docusign.py")

# Schema provenance: route files define FastAPI request/response models (CORRECT-LOCAL, not shared domain schemas)
SCHEMA_PROVENANCE_SKIP=true

# Deep imports: unified_api_contracts.internal is the approved path for internal domain schemas
# (FeeStructure, ClientConfig, CredentialsRegistry)
DEEP_IMPORT_EXCLUDE_GLOBS=(
    "!**/core/fee_calculator.py"
    "!**/core/tranche_router.py"
)

# pip-audit: ignore known CVEs pending upstream package upgrades
PIP_AUDIT_EXTRA_ARGS="--ignore-vuln CVE-2026-34073"

WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"

# Codex enforcement: every entrypoint must emit STARTED, STOPPED, FAILED
# See: unified-trading-codex/03-observability/lifecycle-events.md § Lifecycle Event QG Enforcement
log_section "[5.X/6] UEI LIFECYCLE EVENT ENFORCEMENT (STARTED/STOPPED/FAILED)"
for event in STARTED STOPPED FAILED; do
    run_timeout 30 rg "log_event.*\"${event}\"" "${SOURCE_DIR}" --type py -q \
        || log_warn "Missing log_event('${event}') in ${SERVICE_NAME} — see codex 03-observability/lifecycle-events.md"
done
