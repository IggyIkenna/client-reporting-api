# pyright: reportReturnType=false
"""Singleton live data provider — pre-fetches secrets, creates ExchangeDataCollector.

Lazily initialized on first access so the API starts fast in mock mode.
In live mode, fetches credentials from Secret Manager for all active clients.

Secret naming convention in GCP Secret Manager:
  exec-{client_lower}-{venue}-api-key
  exec-{client_lower}-{venue}-api-secret
  exec-{client_lower}-{venue}-passphrase   (OKX only)
"""

from __future__ import annotations

import logging

from unified_trading_library import UnifiedCloudConfig, get_secret

from .exchange_data_collector import ExchangeDataCollector
from .tranche_router import load_registry

logger = logging.getLogger(__name__)

_cloud_cfg = UnifiedCloudConfig()
_collector: ExchangeDataCollector | None = None


def _derive_secret_base(client_id: str, venue: str) -> str:
    """Derive the Secret Manager base name from client and venue."""
    client_lower = client_id.lower().replace("_", "-")
    return f"exec-{client_lower}-{venue}"


def _fetch_credentials(
    secret_base: str,
    venue: str,
) -> dict[str, str] | None:
    """Fetch API key, secret, and passphrase from Secret Manager."""
    try:
        api_key = get_secret(f"{secret_base}-api-key")
        api_secret = get_secret(f"{secret_base}-api-secret")
    except RuntimeError:
        return None

    # `unified_trading_library.get_secret` returns None (not RuntimeError) on a
    # missing secret; without this guard a missing key becomes a None-filled creds
    # dict counted as "loaded" and CCXT is built with apiKey=None. Treat a missing
    # key/secret as a clean absence instead.
    if api_key is None or api_secret is None:
        logger.warning("Missing api-key/api-secret for %s", secret_base)
        return None

    passphrase = ""
    if venue == "okx":
        try:
            passphrase = get_secret(f"{secret_base}-passphrase") or ""
        except RuntimeError:
            logger.warning("No passphrase found for %s", secret_base)

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "passphrase": passphrase,
    }


def _fetch_all_secrets() -> dict[str, dict[str, str]]:
    """Fetch credentials from Secret Manager for all active managed clients."""
    registry = load_registry()
    clients = registry.get("clients", {})
    secrets: dict[str, dict[str, str]] = {}

    for cid, cfg in clients.items():
        if not cfg.get("is_active", False):
            continue
        secret_name = cfg.get("secret_name", "")
        venue = cfg.get("venue", "")
        if not secret_name or not venue:
            continue
        if secret_name in secrets:
            continue

        secret_base = _derive_secret_base(cid, venue)
        creds = _fetch_credentials(secret_base, venue)
        if creds is not None:
            secrets[secret_name] = creds
            logger.info("Loaded credentials for %s (%s)", cid, secret_base)
        else:
            logger.warning("Failed to load credentials for %s", cid)

    return secrets


def get_collector() -> ExchangeDataCollector:
    """Get or create the singleton ExchangeDataCollector."""
    global _collector
    if _collector is not None:
        return _collector

    if _cloud_cfg.is_mock_mode():
        _collector = ExchangeDataCollector(secrets={})
        return _collector

    logger.info("Initializing live ExchangeDataCollector with Secret Manager")
    secrets = _fetch_all_secrets()
    logger.info("Loaded %d credential set(s) for live data", len(secrets))
    _collector = ExchangeDataCollector(secrets=secrets)
    return _collector
