"""Configuration for client-reporting-api."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from unified_trading_library import UnifiedCloudConfig


class ClientReportingApiConfig(UnifiedCloudConfig):
    """Configuration for client-reporting-api service."""

    config_store_bucket: str = ""
    reports_bucket: str = ""
    client_data_bucket: str = ""

    # Base URL of alerting-service for the daily ledger digest (P6.1 / P7.1-C).
    # Empty → the digest is built + logged but NOT POSTed (honest no-op, never
    # a silent failure). Read via the typed config, never raw os.environ.
    alerting_service_url: str = Field(default="", alias="ALERTING_SERVICE_URL")

    # Cloud Run / Cloud Run Jobs runtime indicators (populated by the
    # runtime, read here instead of via raw os.environ). Empty when not
    # running under Cloud Run.
    k_service: str = Field(default="", alias="K_SERVICE")
    cloud_run_job: str = Field(default="", alias="CLOUD_RUN_JOB")

    # deployment-api X-API-Key (server-to-server). The value is the same GSM
    # secret `deployment-api-api-key` that deployment-api validates against in
    # the prod GCP project, wired into this service's env via the deploy path
    # (`--update-secrets DEPLOYMENT_API_KEY=deployment-api-api-key:latest`).
    # Empty → send no header (matching deployment-api's own "omit if empty"
    # convention) so the caller still works if the key is temporarily unset.
    deployment_api_key: str = Field(default="", alias="DEPLOYMENT_API_KEY")

    def model_post_init(self, __context: object) -> None:
        project_id = self.gcp_project_id or ""
        if not self.config_store_bucket:
            self.config_store_bucket = f"config-{project_id}"
        if not self.reports_bucket:
            self.reports_bucket = f"reports-{project_id}"
        if not self.client_data_bucket:
            self.client_data_bucket = f"client-reporting-data-{project_id}"


@lru_cache(maxsize=1)
def get_config() -> ClientReportingApiConfig:
    return ClientReportingApiConfig()
