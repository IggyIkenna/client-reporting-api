"""Domain config hot-reload wiring for client-reporting-api."""

from __future__ import annotations

import logging

from unified_trading_library import (
    ClientDomainConfig,
    DomainConfigReloader,
    InstrumentDomainConfig,
    log_event,
)

logger = logging.getLogger(__name__)

_instrument_reloader: DomainConfigReloader[InstrumentDomainConfig] | None = None
_client_reloader: DomainConfigReloader[ClientDomainConfig] | None = None


def _on_instruments_reload(config: InstrumentDomainConfig) -> None:
    logger.info(
        "Instruments domain config reloaded: %d instruments, %d venues",
        len(config.subscription_list),
        len(config.enabled_venues),
    )
    log_event(
        "CONFIG_CHANGED",
        details={
            "domain": "instruments",
            "service": "client-reporting-api",
            "instruments_count": len(config.subscription_list),
            "venues_count": len(config.enabled_venues),
        },
    )


def _on_clients_reload(config: ClientDomainConfig) -> None:
    logger.info(
        "Clients domain config reloaded: %d active clients",
        len(config.active_clients),
    )
    log_event(
        "CONFIG_CHANGED",
        details={
            "domain": "clients",
            "service": "client-reporting-api",
            "active_clients_count": len(config.active_clients),
        },
    )


def start_domain_config_reloaders(service_config: object) -> None:
    """Start domain config reloaders. Call on service startup."""
    global _instrument_reloader, _client_reloader

    config_store_bucket: str = getattr(service_config, "config_store_bucket", "")
    project_id: str | None = getattr(service_config, "project_id", None)

    if not config_store_bucket:
        logger.info("CONFIG_STORE_BUCKET not set — domain config hot-reload disabled")
        return

    _instrument_reloader = DomainConfigReloader(
        domain="instruments",
        config_class=InstrumentDomainConfig,
        config_bucket=config_store_bucket,
        project_id=project_id,
    )
    _instrument_reloader.on_reload(_on_instruments_reload)
    _instrument_reloader.start_watching()

    _client_reloader = DomainConfigReloader(
        domain="clients",
        config_class=ClientDomainConfig,
        config_bucket=config_store_bucket,
        project_id=project_id,
    )
    _client_reloader.on_reload(_on_clients_reload)
    _client_reloader.start_watching()

    logger.info("Domain config reloaders started: instruments, clients")


def stop_domain_config_reloaders() -> None:
    """Stop domain config reloaders. Call on service shutdown."""
    global _instrument_reloader, _client_reloader
    if _instrument_reloader is not None:
        _instrument_reloader.stop_watching()
        _instrument_reloader = None
    if _client_reloader is not None:
        _client_reloader.stop_watching()
        _client_reloader = None
    logger.info("Domain config reloaders stopped")
