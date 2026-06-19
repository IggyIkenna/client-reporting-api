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
ARG BASE_IMAGE_DIGEST=sha256:6b27286abac323fb71f8badc1ceff9ed72130485dccd7b789d059e843dc7d96b
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

# Strip local uv sources (../unified-trading-library doesn't exist in container)
# UTL is pre-installed in the base image, so uv will see it as satisfied
RUN sed -i '/\[tool\.uv\.sources/,/^$/d' pyproject.toml \
    && uv pip install --system .

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
