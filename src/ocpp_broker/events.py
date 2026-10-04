"""
The live event bus behind ``GET /api/events``.

The session code publishes small facts ("connector 1 is Charging", "backend primary is down") and any
number of console tabs listen. Publishing is synchronous, never waits and never raises, so OCPP traffic
cannot be slowed or broken by a slow or broken listener: each listener has a bounded queue, and one that
falls behind is cut off (it reconnects and asks for what it missed, see ``replay``).

Events describe *what happened*, never what was said: no OCPP payloads, no id tags and no command
payloads, so the stream is low in sensitivity. Nothing is persisted; a restart starts a new numbering.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger("ocpp_broker.events")

# What can be published. The console switches on these names; the docs list them; a test keeps all three in step.
EVENT_TYPES = (
    "charger.connected",
    "charger.disconnected",
    "charger.replaced",
    "charger.boot",
    "charger.status",
    "backend.link",
    "backend.failover",
    "transaction.started",
    "transaction.stopped",
    "command.result",
)

DEFAULT_HISTORY = 1000  # events kept so a reconnecting listener can catch up
DEFAULT_QUEUE = 256  # events one listener may fall behind by before it is cut off
DEFAULT_MAX_LISTENERS = 100


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    time: datetime
    org: Optional[str]
    charger_id: Optional[str]
    data: Dict[str, Any]

    def matches(self, org: Optional[str], charger_id: Optional[str]) -> bool:
        return (org is None or self.org == org) and (charger_id is None or self.charger_id == charger_id)


@dataclass(eq=False)  # compared by identity: listeners live in a set
class Listener:
    """One subscription. ``overflowed`` is set when it fell too far behind and was cut off."""

    org: Optional[str]
    charger_id: Optional[str]
    queue: "asyncio.Queue[Optional[Event]]"
    overflowed: bool = False
    closed: bool = False  # the bus was shut down

    @property
    def finished(self) -> bool:
        """Cut off or shut down: nothing more will arrive once what is queued has been read."""
        return self.overflowed or self.closed

    async def next(self, timeout: float) -> Optional[Event]:
        """The next event, or None if nothing came within ``timeout`` seconds or the listener is finished
        (``finished`` tells the two apart; what was already queued is still delivered first)."""
        if self.finished and self.queue.empty():
            return None
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)  # None is the end marker of a closed bus
        except asyncio.TimeoutError:
            return None


class StreamRefused(RuntimeError):
    """The bus will not take another listener."""


class TooManyListeners(StreamRefused):
    """The bus is serving as many console streams as it allows."""


class BusClosed(StreamRefused):
    """The broker is shutting down."""


class EventBus:
    def __init__(
        self,
        history: int = DEFAULT_HISTORY,
        queue_size: int = DEFAULT_QUEUE,
        max_listeners: int = DEFAULT_MAX_LISTENERS,
    ):
        self._history: Deque[Event] = deque(maxlen=history)
        self._queue_size = queue_size
        self._max_listeners = max_listeners
        self._listeners: Set[Listener] = set()
        self._last_id = 0
        self._closed = False

    @property
    def last_id(self) -> int:
        return self._last_id

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def publish(
        self, type: str, org: Optional[str] = None, charger_id: Optional[str] = None, **data: Any
    ) -> Optional[Event]:
        """Record an event and hand it to every listener it matches. Never blocks and never raises."""
        try:
            self._last_id += 1
            event = Event(self._last_id, type, datetime.now(timezone.utc), org, charger_id, data)
            self._history.append(event)
            for listener in list(self._listeners):
                if not event.matches(listener.org, listener.charger_id) or listener.overflowed:
                    continue
                try:
                    listener.queue.put_nowait(event)
                except asyncio.QueueFull:
                    listener.overflowed = True
                    logger.info("A console event stream fell %d events behind and was closed", self._queue_size)
                except Exception:  # one broken listener must not keep the others from the event
                    listener.overflowed = True
                    logger.exception("Dropping an event listener that could not take event %s", type)
            return event
        except Exception:  # an observer must never be able to break the traffic it observes
            logger.exception("Could not publish event %s", type)
            return None

    def subscribe(self, org: Optional[str] = None, charger_id: Optional[str] = None) -> Listener:
        if self._closed:
            raise BusClosed("The broker is shutting down")
        if len(self._listeners) >= self._max_listeners:
            raise TooManyListeners(f"At most {self._max_listeners} event streams at a time")
        listener = Listener(org, charger_id, asyncio.Queue(maxsize=self._queue_size))
        self._listeners.add(listener)
        return listener

    def unsubscribe(self, listener: Listener) -> None:
        self._listeners.discard(listener)

    def close(self) -> None:
        """
        End every stream and refuse new ones. The server waits for open responses when it shuts down and an
        event stream never ends on its own, so the broker calls this first. Events already queued are still delivered.
        """
        self._closed = True
        for listener in list(self._listeners):
            listener.closed = True
            try:
                listener.queue.put_nowait(None)  # wake it up
            except asyncio.QueueFull:
                pass  # it will read what is queued, then see that it is finished

    def replay(
        self,
        after_id: Optional[int] = None,
        org: Optional[str] = None,
        charger_id: Optional[str] = None,
        latest: int = 0,
    ) -> "Replay":
        """
        Events a listener has not seen. With ``after_id`` (a reconnect): everything after it that is still
        remembered, and ``missed`` says whether some of it has already been forgotten. Without it (a first
        connection): the ``latest`` most recent events, for a feed that is not empty at the start.
        """
        history: List[Event] = list(self._history)
        if after_id is None:
            matching = [e for e in history if e.matches(org, charger_id)]
            return Replay(matching[-latest:] if latest > 0 else [], missed=False)
        oldest = history[0].id if history else self._last_id + 1
        missed = after_id < oldest - 1 or after_id > self._last_id
        return Replay([e for e in history if e.id > after_id and e.matches(org, charger_id)], missed=missed)


@dataclass
class Replay:
    events: List[Event]
    missed: bool  # events between the listener's last id and the oldest remembered one are gone (or the id is from another run)
