"""SSE streaming endpoint for client report events."""

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)
router = APIRouter()

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
async def stream_reports() -> EventSourceResponse:
    """Stream client report events via Server-Sent Events.

    Clients receive a heartbeat every 30 seconds when no events are pending.
    """

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
