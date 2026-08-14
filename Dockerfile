# Client Reporting API — Dockerfile
#
# Two targets:
#   api     — FastAPI server (Cloud Run Service, port 8080)
#   batch   — CLI for Cloud Run Jobs (update, backfill, onboard)
#
# Build:
#   docker build --build-arg PROJECT_ID=... --target api -t client-reporting-api .
#   docker build --build-arg PROJECT_ID=... --target batch -t client-reporting-batch .

ARG PROJECT_ID

# Digest-pinned UTL base image (QG STEP 5.79 -- reproducible builds + UTL/UAC provenance).
# Refreshed by the dependency-update fan-out (update-dependency-version.yml) on base-image
# republish; cloudbuild may override at build time: --build-arg BASE_IMAGE_DIGEST=sha256:...
ARG BASE_IMAGE_DIGEST=sha256:e3b9da32f0bed45ddc7ee56470783604315c70fec4da5477250a5fcb614ba835
FROM --platform=linux/amd64 asia-northeast1-docker.pkg.dev/${PROJECT_ID}/unified-trading-library/unified-trading-library@${BASE_IMAGE_DIGEST} AS base

# ============================================
# Common: install deps + package
# ============================================
FROM base AS common

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1

WORKDIR /app

# Copy source + deps
COPY pyproject.toml ./
COPY client_reporting_api/ ./client_reporting_api/
COPY scripts/ ./scripts/
RUN mkdir -p ./data ./configs
COPY configs/credentials-registry.yaml ./configs/

# hatch-vcs reads the version from git tags, which are unavailable in the Cloud
# Build context (shallow checkout). cloudbuild's extract-version step resolves the
# real tag and passes it here so `uv pip install .` doesn't fail with
# `setuptools-scm was unable to detect version`.
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0.dev0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

# Strip local uv sources (../unified-trading-library doesn't exist in container)
# UTL is pre-installed in the base image, so uv will see it as satisfied for THIS floor.
RUN sed -i '/\[tool\.uv\.sources/,/^$/d' pyproject.toml

# uv does NOT read pip.conf's extra-index-url (pip-only convention), and this Dockerfile
# carries no pip.conf at all — the install above has silently relied on the pinned base image
# already satisfying unified-trading-library>=0.66.0 / unified-api-contracts>=0.80.0, the exact
# latent gap that broke instruments-service's build the moment ITS floor outran its base image
# (see unified-trading-pm/plans/active/issues/cloud_build_unified_api_contracts_publish_ordering_race_2026_07_29.md).
# Same fix as instruments-service@4c05f2d3: mount a freshly-minted GAR token as a BuildKit
# secret, scoped to only this RUN layer, never baked into an image layer/history. 3-attempt
# retry with backoff mirrors the fleet-wide pattern (transient AR/network hiccups).
RUN --mount=type=secret,id=gar_token \
    UV_EXTRA_INDEX_URL="https://oauth2accesstoken:$(cat /run/secrets/gar_token)@asia-northeast1-python.pkg.dev/central-element-323112/unified-libraries/simple/" \
    sh -c 'i=1; until uv pip install --system .; do [ "$i" -ge 3 ] && { echo "uv pip install failed after 3 attempts" >&2; exit 1; }; w=$((15 * i)); echo "uv pip install failed (attempt $i/3) -- retrying in ${w}s"; sleep "$w"; i=$((i + 1)); done'

# ============================================
# Target: API server (Cloud Run Service)
# ============================================
FROM common AS api

ENV PORT=8080
EXPOSE 8080

# Non-root user
RUN useradd -m -r appuser && chown -R appuser:appuser /app
USER appuser

CMD ["client-reporting"]

# ============================================
# Target: Batch CLI (Cloud Run Jobs)
# ============================================
FROM common AS batch

# Data dir for backfill output (mounted as GCS FUSE volume in prod)
RUN mkdir -p /app/data/backfill

# Non-root user
RUN useradd -m -r appuser && chown -R appuser:appuser /app
USER appuser

# Default: hourly update. Override via Cloud Run Job args.
ENTRYPOINT ["client-reporting-manage"]
CMD ["update"]
