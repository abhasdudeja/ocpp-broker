"""
``GET /api/events``: the live event stream of the web console, as Server-Sent Events.

The browser's ``EventSource`` cannot send the ``X-API-Key`` header and the key must never travel in a
URL, so the console reads this stream with ``fetch``; any HTTP client that can send the header can too.

Each event is one SSE message::

    id: 42
    event: charger.status
    data: {"id": 42, "type": "charger.status", "time": "...", "org": "Fleet", "charger_id": "CP-1", "data": {...}}

The first message is always ``stream.open`` (not a numbered event): it carries this broker run's
``instance_id`` and ``missed``, which is true when the client asked to resume (``Last-Event-ID``) but some of
what it missed is no longer remembered, or comes from another run. A client that sees ``missed`` should
reload what it shows. A comment line (``: keepalive``) is sent when nothing else has been for a while, so
proxies do not close an idle stream. A client that falls too far behind is disconnected; reconnecting with
its last id catches it up.
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, AsyncIterator, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .events import Event, EventBus, StreamRefused
from .schemas.console import ConsoleEvent

KEEPALIVE_SECONDS = 15.0
MAX_REPLAY = 200


class EventStreamResponse(StreamingResponse):
    media_type = "text/event-stream"


def event_payload(event: Event) -> Dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "time": event.time.isoformat(),
        "org": event.org,
        "charger_id": event.charger_id,
        "data": event.data,
    }


def sse_message(event: Event) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event_payload(event), default=str)}\n\n"


def _open_message(instance_id: str, last_id: int, missed: bool) -> str:
    data = json.dumps({"instance_id": instance_id, "last_id": last_id, "missed": missed})
    return f"event: stream.open\ndata: {data}\n\n"


async def event_stream(
    bus: EventBus,
    instance_id: str,
    org: Optional[str] = None,
    charger_id: Optional[str] = None,
    after_id: Optional[int] = None,
    replay: int = 0,
    keepalive: float = KEEPALIVE_SECONDS,
) -> AsyncGenerator[str, None]:
    """The SSE text of one stream. Ends when the listener falls behind or the broker shuts down; the caller cancels it on disconnect."""
    # Subscribe before reading the history, so nothing falls between the two; ids already sent are skipped.
    listener = bus.subscribe(org, charger_id)
    try:
        past = bus.replay(after_id, org, charger_id, latest=min(replay, MAX_REPLAY))
        yield _open_message(instance_id, bus.last_id, past.missed)
        sent = 0
        for event in past.events:
            yield sse_message(event)
            sent = max(sent, event.id)
        while True:
            queued = await listener.next(keepalive)
            if queued is None:
                if listener.finished:
                    return
                yield ": keepalive\n\n"
                continue
            if queued.id > sent:
                sent = queued.id
                yield sse_message(queued)
    finally:
        bus.unsubscribe(listener)


def create_events_api(broker: Any) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["Console"])

    @router.get(
        "/events",
        summary="Live events (Server-Sent Events)",
        response_class=EventStreamResponse,
        responses={200: {"model": ConsoleEvent, "description": "A text/event-stream. Each `data:` line is one event as JSON."}},
    )
    async def events(
        request: Request,
        org: Optional[str] = Query(None, description="Only events of this organization"),
        charger_id: Optional[str] = Query(None, description="Only events of this charger"),
        replay: int = Query(0, ge=0, le=MAX_REPLAY, description="On a first connection, start with this many of the latest matching events"),
    ) -> EventStreamResponse:
        bus: EventBus = broker.events
        last = request.headers.get("last-event-id")
        after_id = int(last) if last is not None and last.strip().isdigit() else None
        stream = event_stream(bus, broker.instance_id, org, charger_id, after_id, replay)
        try:
            # Subscribe now so a full house is a clean 503 instead of a stream that dies at once
            first = await stream.__anext__()
        except StreamRefused as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        async def body() -> AsyncIterator[str]:
            try:
                yield first
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

        return EventStreamResponse(body(), headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    return router
