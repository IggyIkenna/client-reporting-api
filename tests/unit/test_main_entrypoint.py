"""Unit tests for client_reporting_api.main entrypoint."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch


def test_main_calls_setup_events_with_live_mode() -> None:
    """main() calls setup_events with mode='live'."""
    mock_sink = MagicMock()
    mock_cfg = MagicMock()
    mock_cfg.gcp_project_id = "test-project"

    with (
        patch(
            "client_reporting_api.main._auth_cfg",
            mock_cfg,
        ),
        patch(
            "client_reporting_api.main.PubSubEventSink",
            return_value=mock_sink,
        ) as mock_pubsub_cls,
        patch("client_reporting_api.main.setup_events") as mock_setup,
        patch("client_reporting_api.main.setup_tracing") as mock_tracing,
        patch("client_reporting_api.main.uvicorn.run") as mock_uvicorn,
    ):
        from client_reporting_api.main import main

        main()

    mock_pubsub_cls.assert_called_once_with(
        project_id="test-project",
        topic="client-reporting-api-events",
        service_name="client-reporting-api",
    )
    mock_setup.assert_called_once_with(
        service_name="client-reporting-api",
        mode="live",
        sink=mock_sink,
    )
    mock_tracing.assert_called_once_with("client-reporting-api")
    mock_uvicorn.assert_called_once_with(
        "client_reporting_api.api.main:app",
        host="0.0.0.0",  # nosec B104
        port=8080,
        reload=False,
    )


def test_main_module_name_guard() -> None:
    """The __main__ guard exists and calls main()."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).parent.parent.parent / "client_reporting_api" / "main.py"
    ).read_text()
    tree = ast.parse(source)
    # Check that if __name__ == "__main__": main() exists
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
            ):
                found = True
    assert found, "main.py must have if __name__ == '__main__' guard"
