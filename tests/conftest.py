"""Shared pytest fixtures for client-reporting-api tests.

Provides:
- Event interface initialization in test mode
- BigQuery emulator fixture for integration tests

BigQuery Emulator (ghcr.io/goccy/bigquery-emulator)
-----------------------------------------------------
To run the BigQuery emulator locally::

    docker run -d -p 9050:9050 -p 9060:9060 ghcr.io/goccy/bigquery-emulator:latest \\
        --project=test-project --dataset=test_dataset

Set ``BIGQUERY_EMULATOR_HOST=localhost:9050`` before running tests that use
the ``bigquery_emulator_url`` fixture. Tests are skipped automatically when the
env var is not set.

Known emulator gaps (community-maintained):
- Window functions (ROW_NUMBER, RANK, etc.) may not work correctly
- ARRAY_AGG with ORDER BY may differ from production BigQuery
- Test only the subset of BQ features used in production paths

CI (GitHub Actions) -- add to the workflow job::

    services:
      bigquery-emulator:
        image: ghcr.io/goccy/bigquery-emulator:latest
        ports:
          - 9050:9050
          - 9060:9060
        options: --project=test-project --dataset=test_dataset
    env:
      BIGQUERY_EMULATOR_HOST: localhost:9050
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Generator

import pytest
from unified_trading_library import setup_events

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def _init_events() -> None:
    """Initialize unified_trading_library.events in test mode before any test runs.

    GoogleOAuthMiddleware calls log_event() which requires setup_events() to
    have been called first.  Mode 'test' silences all real sinks.
    """
    setup_events("client-reporting-api", "test")


def _is_bigquery_emulator_reachable(host: str) -> bool:
    """Return True if the BigQuery emulator endpoint is reachable via TCP."""
    hostname, _, port_str = host.partition(":")
    port = int(port_str) if port_str else 9050
    try:
        with socket.create_connection((hostname, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def bigquery_emulator_url() -> Generator[str]:
    """Yield the BigQuery emulator URL when ``BIGQUERY_EMULATOR_HOST`` is set and reachable.

    Tests that request this fixture are **skipped automatically** when the env var
    is not set or the emulator is not running -- no manual ``pytest.mark.skipif``
    decoration needed.

    To start the emulator locally::

        docker run -d -p 9050:9050 -p 9060:9060 ghcr.io/goccy/bigquery-emulator:latest \\
            --project=test-project --dataset=test_dataset
        export BIGQUERY_EMULATOR_HOST=localhost:9050

    Usage::

        def test_something(bigquery_emulator_url: str) -> None:
            # bigquery_emulator_url is e.g. "http://localhost:9050"
            ...
    """
    host = os.environ.get("BIGQUERY_EMULATOR_HOST", "")
    if not host:
        pytest.skip(
            "BigQuery emulator not configured -- set BIGQUERY_EMULATOR_HOST=localhost:9050 "
            "and start: docker run -d -p 9050:9050 -p 9060:9060 "
            "ghcr.io/goccy/bigquery-emulator:latest --project=test-project --dataset=test_dataset"
        )
    if not _is_bigquery_emulator_reachable(host):
        pytest.skip(
            f"BigQuery emulator not reachable at {host} -- "
            "start: docker run -d -p 9050:9050 -p 9060:9060 "
            "ghcr.io/goccy/bigquery-emulator:latest --project=test-project --dataset=test_dataset"
        )
    url = f"http://{host}"
    logger.info("BigQuery emulator active at %s", url)
    yield url
