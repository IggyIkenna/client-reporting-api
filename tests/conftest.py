"""Shared pytest fixtures for client-reporting-api tests."""

from __future__ import annotations

import pytest
from unified_events_interface import setup_events


@pytest.fixture(scope="session", autouse=True)
def _init_events() -> None:
    """Initialize unified_events_interface in test mode before any test runs.

    GoogleOAuthMiddleware calls log_event() which requires setup_events() to
    have been called first.  Mode 'test' silences all real sinks.
    """
    setup_events("client-reporting-api", "test")
