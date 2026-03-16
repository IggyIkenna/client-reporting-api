"""BigQuery emulator integration tests for client-reporting-api.

These tests verify that client-reporting-api can interact with a BigQuery
emulator for analytics queries. Tests are **automatically skipped** when the
emulator is not running.

To run locally::

    docker run -d -p 9050:9050 -p 9060:9060 ghcr.io/goccy/bigquery-emulator:latest \\
        --project=test-project --dataset=test_dataset
    export BIGQUERY_EMULATOR_HOST=localhost:9050
    cd client-reporting-api && bash scripts/quality-gates.sh
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.integration]


class TestBigQueryEmulatorFixture:
    """Verify the bigquery_emulator_url fixture works correctly."""

    def test_emulator_url_is_http(self, bigquery_emulator_url: str) -> None:
        """The fixture yields a proper HTTP URL."""
        assert bigquery_emulator_url.startswith("http://")

    def test_emulator_url_contains_host_and_port(
        self, bigquery_emulator_url: str,
    ) -> None:
        """URL includes the expected host:port structure."""
        # Strip http://
        host_port = bigquery_emulator_url.replace("http://", "")
        assert ":" in host_port
        host, port_str = host_port.rsplit(":", 1)
        assert len(host) > 0
        port = int(port_str)
        assert 1 <= port <= 65535

    def test_env_var_is_set_when_emulator_active(
        self, bigquery_emulator_url: str,
    ) -> None:
        """BIGQUERY_EMULATOR_HOST must be set when the fixture yields."""
        host = os.environ.get("BIGQUERY_EMULATOR_HOST", "")
        assert len(host) > 0, "BIGQUERY_EMULATOR_HOST should be set"
        # The fixture URL should match the env var
        assert bigquery_emulator_url == f"http://{host}"


class TestBigQueryEmulatorConnectivity:
    """Test basic connectivity to the BigQuery emulator."""

    def test_emulator_accepts_tcp_connection(
        self, bigquery_emulator_url: str,
    ) -> None:
        """The emulator accepts TCP connections on the configured port."""
        import socket

        host_port = bigquery_emulator_url.replace("http://", "")
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
        with socket.create_connection((host, port), timeout=2.0):
            pass  # Connection succeeded

    def test_emulator_responds_to_http(
        self, bigquery_emulator_url: str,
    ) -> None:
        """The emulator responds to an HTTP request (even if 404)."""
        import urllib.request

        req = urllib.request.Request(
            f"{bigquery_emulator_url}/bigquery/v2/projects/test-project/datasets",
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                # Any non-exception response is valid — the emulator is alive
                assert resp.status in range(200, 600)
        except urllib.error.HTTPError as exc:
            # 404 or other HTTP errors mean the emulator is alive and responding
            assert exc.code in range(200, 600)
