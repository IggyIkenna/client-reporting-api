"""Configuration for client-reporting-api."""

from __future__ import annotations

from functools import lru_cache

from unified_trading_library import UnifiedCloudConfig


class ClientReportingApiConfig(UnifiedCloudConfig):
    """Configuration for client-reporting-api service."""

    config_store_bucket: str = ""
    reports_bucket: str = ""
    client_data_bucket: str = ""

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
