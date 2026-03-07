# client-reporting-api — Deployment Guide

## Prerequisites

- Python 3.13
- `uv` package manager
- GCP project (for Cloud Run deployment)
- Credentials registry YAML accessible at
  `../execution-services/configs/credentials-registry.yaml`

## Local Development

```bash
cd client-reporting-api
uv pip install -e ".[dev]"
DISABLE_AUTH=true uvicorn client_reporting_api.api.main:app --port 8080 --reload
```

Verify:

```bash
curl http://localhost:8080/health
```

## Docker

```bash
docker build -t client-reporting-api .
docker run -p 8080:8080 \
  -e DISABLE_AUTH=true \
  client-reporting-api
```

## Cloud Run Deployment

```bash
# Build via Cloud Build
gcloud builds submit --config cloudbuild.yaml

# Deploy
gcloud run deploy client-reporting-api \
  --image gcr.io/{project_id}/client-reporting-api \
  --region us-central1 \
  --platform managed \
  --set-env-vars ENVIRONMENT=production \
  --set-secrets API_KEY=client-reporting-api-key:latest \
  --service-account client-reporting-api@{project_id}.iam.gserviceaccount.com \
  --min-instances 1 \
  --memory 1Gi
```

## Secret Manager Setup

```bash
echo -n "your-api-key" | gcloud secrets create client-reporting-api-key --data-file=-

gcloud secrets add-iam-policy-binding client-reporting-api-key \
  --member="serviceAccount:client-reporting-api@{project_id}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## AWS Deployment (ECS)

```bash
aws codebuild start-build --project-name client-reporting-api
# See buildspec.aws.yaml for details
```

## Health Check

```
GET /health → {"status": "ok"}
```

## Credentials Registry Dependency

The `tranche_router.py` reads a registry YAML from the workspace. In Cloud Run,
this file must be:

- Bundled into the container image at build time, or
- Mounted from a GCS FUSE volume, or
- Replaced with a Secret Manager-backed implementation

The registry path defaults to:
`<workspace_root>/execution-services/configs/credentials-registry.yaml`
