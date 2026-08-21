"""SSE streaming endpoint for client report events."""

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse
from unified_trading_library import AuthContext, create_api_auth

from client_reporting_api.core.entitlement import require_internal

logger = logging.getLogger(__name__)
router = APIRouter()

_require_auth = create_api_auth("client-reporting-api")
AuthDep = Annotated[AuthContext, Depends(_require_auth)]

_reports_queue: deque[dict[str, object]] = deque(maxlen=500)
_subscribers: list[asyncio.Queue[dict[str, object]]] = []


def publish_report_event(event: dict[str, object]) -> None:
    """Publish a report event to all active SSE subscribers."""
    _reports_queue.append(event)
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull as e:
            logger.debug("Suppressed %s during publish_report_event: %s", type(e).__name__, e)


@router.get("/stream/reports")
async def stream_reports(auth: AuthDep) -> EventSourceResponse:
    """Stream client report events via Server-Sent Events.

    Clients receive a heartbeat every 30 seconds when no events are pending.

    Entitlement: the underlying queue is a single global fan-out with no
    per-client_id scoping — every subscriber receives every published
    report event for every client. Until the queue carries a client_id
    to filter on, ``require_internal`` is the strictest safe gate
    (2026-08-21 CTO handoff P0 fix, judgment call documented in
    ``walkthrough_feedback_remediation_2026_08_21.md``): it denies any
    external caller outright rather than leaking cross-client events to
    them, matching how other cross-client aggregate routes in this
    service (e.g. ``reports.py::list_reports``) are gated.
    """
    require_internal(auth)

    async def generator() -> AsyncGenerator[dict[str, str]]:
        q: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=200)
        _subscribers.append(q)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield {"data": json.dumps(event)}
                except TimeoutError:
                    yield {"data": json.dumps({"heartbeat": True})}
        finally:
            _subscribers.remove(q)

    return EventSourceResponse(generator())
