"""Unit tests for FastAPI app routes (health, metrics)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from client_reporting_api.api.main import app
from client_reporting_api.api.routes.reports_stream import (
    _subscribers,
    publish_report_event,
)


def test_health_endpoint_returns_ok() -> None:
    """GET /health returns 200 with expected JSON payload."""
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "client-reporting-api"


def test_metrics_endpoint_returns_200() -> None:
    """GET /metrics returns 200 with text/plain content type."""
    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_publish_report_event_delivers_to_subscribers() -> None:
    """publish_report_event enqueues event to active subscribers."""
    q: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=200)
    _subscribers.append(q)
    try:
        event = {"type": "report", "id": "rpt-1"}
        publish_report_event(event)
        assert not q.empty()
        result = q.get_nowait()
        assert result == event
    finally:
        _subscribers.remove(q)


def test_publish_report_event_no_subscribers() -> None:
    """publish_report_event with no subscribers runs without error."""
    initial_count = len(_subscribers)
    event = {"type": "report", "id": "rpt-2"}
    publish_report_event(event)
    assert len(_subscribers) == initial_count


def test_publish_report_event_queue_full_is_suppressed() -> None:
    """When subscriber queue is full, QueueFull is suppressed gracefully."""
    q: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)
    q.put_nowait({"existing": True})  # fill the queue
    _subscribers.append(q)
    try:
        publish_report_event({"type": "overflow"})
        # No exception raised — queue full is suppressed
        assert q.qsize() == 1  # still 1 item (original), new item dropped
    finally:
        _subscribers.remove(q)
