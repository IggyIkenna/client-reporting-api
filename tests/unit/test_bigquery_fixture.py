"""Unit tests for BigQuery emulator conftest helpers.

These test the _is_bigquery_emulator_reachable function and the skip logic
without needing a running emulator.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.conftest import _is_bigquery_emulator_reachable


class TestBigQueryReachabilityCheck:
    """Test the TCP reachability helper."""

    def test_reachable_returns_true_on_success(self) -> None:
        """Returns True when TCP connection succeeds."""
        with patch("tests.conftest.socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = lambda s: s
            mock_conn.return_value.__exit__ = lambda s, *a: None
            result = _is_bigquery_emulator_reachable("localhost:9050")
        assert result is True

    def test_unreachable_returns_false_on_oserror(self) -> None:
        """Returns False when TCP connection raises OSError."""
        with patch(
            "tests.conftest.socket.create_connection",
            side_effect=OSError("Connection refused"),
        ):
            result = _is_bigquery_emulator_reachable("localhost:9050")
        assert result is False

    def test_default_port_when_no_port_specified(self) -> None:
        """Uses port 9050 when no port is specified in the host string."""
        with patch("tests.conftest.socket.create_connection") as mock_conn:
            mock_conn.side_effect = OSError("refuse")
            _is_bigquery_emulator_reachable("localhost")
            mock_conn.assert_called_once_with(("localhost", 9050), timeout=1.0)

    def test_custom_port_parsed_correctly(self) -> None:
        """Parses custom port from host:port string."""
        with patch("tests.conftest.socket.create_connection") as mock_conn:
            mock_conn.side_effect = OSError("refuse")
            _is_bigquery_emulator_reachable("myhost:1234")
            mock_conn.assert_called_once_with(("myhost", 1234), timeout=1.0)
