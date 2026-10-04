"""
This broker itself as one of a charger's backends.

A backend marked ``local: true`` is the broker answering the charger with the ``ocpp`` library, the same
code as broker mode. To the session it looks like any other backend connection (``BackendConnection``):
frames for it are sent with ``send``, what it says comes back through ``OcppBroker.forward_backend_message``,
and the transaction id table translates ids for it like for any other backend. The difference is the "socket":
instead of a WebSocket to another server the library reads frames from a queue and writes them to the session.

* As the **leader** it answers the charger: its replies are forwarded to the charger.
* As a **follower** (a standby) it runs silently: it sees copies of the charger's requests, keeps its own
  state (the transactions it numbered, what it stores) and its replies are read by the id table and thrown
  away, exactly like an external follower's. If the external leader fails and the standby is promoted,
  it already knows what is running and simply starts being heard.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from .backend_manager import DEFAULT_MAX_BUFFERED, DEFAULT_OUTAGE_TIMEOUT
from .charge_point import BrokerChargePoint

logger = logging.getLogger("ocpp_broker.local_backend")


class LoopbackConnection:
    """What the ``ocpp`` library takes for the charger's WebSocket: frames come from a queue, frames it sends go to the owner."""

    subprotocol = "ocpp1.6"
    closed = False

    def __init__(self, deliver: Callable[[str], Awaitable[None]]):
        self._queue: "asyncio.Queue[str]" = asyncio.Queue()
        self._deliver = deliver

    def put(self, message: str) -> None:
        self._queue.put_nowait(message)

    async def recv(self) -> str:
        return await self._queue.get()

    async def send(self, message: str) -> None:
        await self._deliver(message)

    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None:
        self.closed = True


class LocalBackend:
    """A backend that is this broker (see the module docstring). Offers what the session uses of a ``BackendConnection``."""

    local = True

    def __init__(
        self,
        broker: Any,
        charger_id: str,
        org: str,
        key: str,
        is_leader: bool,
        response_timeout: float = 30,
        on_failed: Optional[Callable[["LocalBackend"], None]] = None,
    ):
        self.broker = broker
        self.id = charger_id
        self.org = org
        self.key = key
        self.url = "this broker"
        self.subprotocol = "ocpp1.6"
        self.is_leader = is_leader
        # The members of a session are handled alike; these only matter to an external backend's socket
        self.max_buffered = DEFAULT_MAX_BUFFERED if is_leader else 0
        self.outage_timeout = DEFAULT_OUTAGE_TIMEOUT
        self.on_undeliverable: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_disconnected = None
        self.on_connected = None
        self.disconnected_since: Optional[float] = None
        self.connected_event = asyncio.Event()
        self.charge_point: Optional[BrokerChargePoint] = None
        self._response_timeout = response_timeout
        self._on_failed = on_failed
        self._connection = LoopbackConnection(self._from_library)
        self._task: Optional[asyncio.Task] = None
        self._failed = False
        self._closed = False

    async def connect(self, wait: bool = True) -> None:
        if self._task is not None:
            return
        self.charge_point = BrokerChargePoint(
            charge_point_id=self.id,
            websocket=self._connection,
            broker=self.broker,
            org_name=self.org,
            response_timeout=self._response_timeout,
        )
        self.connected_event.set()
        self._task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        assert self.charge_point is not None
        try:
            await self.charge_point.start()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The local backend for charger %s stopped with an error", self.id)
            self._failed = True
            self.connected_event.clear()
            if self._on_failed is not None:
                self._on_failed(self)

    def is_ready(self) -> bool:
        return self._task is not None and not self._task.done() and not self._failed and not self._closed

    async def send(self, message: str) -> None:
        """A frame for the local backend (the library). Dropped once it has stopped."""
        if self.is_ready():
            self._connection.put(message)

    async def _from_library(self, message: str) -> None:
        """A frame the library sends: handled like a frame from any backend (leader: to the charger; follower: read and dropped)."""
        await self.broker.forward_backend_message(self, message)

    def reject_buffered(self) -> None:
        """Nothing is ever held for the local backend."""

    @property
    def buffered_count(self) -> int:
        return 0

    async def close(self) -> None:
        self._closed = True
        self.connected_event.clear()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
